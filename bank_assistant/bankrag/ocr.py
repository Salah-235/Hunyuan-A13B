"""Optical character recognition for scanned PDF pages.

Two engines, chosen with OCR_ENGINE:
- "tesseract": runs on this server (pages never leave it); needs the tesseract program with the
  Arabic and French language data (apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-fra).
- "vision": an OpenAI-compatible model that reads images (for example a vision model served by
  vLLM inside the bank). Usually more accurate on Arabic numbers and percentages.
"auto" (default) uses "vision" when OCR_VISION_MODEL is set, otherwise Tesseract when installed.
"""
import base64
import importlib
import io
import json
import logging
import re
import shutil

import requests

from .llm import strip_thinking

log = logging.getLogger(__name__)

VISION_PROMPT = (
    "Transcribe all the text on this scanned page of a bank document exactly as written, in reading order. "
    "Keep Arabic and French text as it is, do not translate, summarise or add anything. Copy numbers, "
    "percentages, amounts and dates exactly. Write each table row on one line with cells separated by ' | '. "
    "If a word is unreadable write [?]. Output only the page text."
)


def render_pages(data, page_numbers, dpi):
    """Yield (page_number, PIL image) for the given 1-based page numbers."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        for number in page_numbers:
            page = pdf[number - 1]
            yield number, page.render(scale=dpi / 72).to_pil().convert("RGB")
            page.close()
    finally:
        pdf.close()


_PERCENT = re.compile(r"[0-9٠-٩]\s*[%٪]|[%٪]\s*[0-9٠-٩]")
_DIGIT = re.compile(r"[0-9٠-٩]")


def _overlap(a, b):
    """Share of box a covered by box b (boxes are left, top, right, bottom)."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    area = (a[2] - a[0]) * (a[3] - a[1])
    return width * height / area if width > 0 and height > 0 and area > 0 else 0.0


class TesseractOcr:
    """Tesseract with two passes over each page:
    1. the configured languages (Arabic + French) read the text;
    2. Arabic + English re-reads the page only to recover "%" signs, which the Arabic/French
       models often turn into digits next to Arabic text (30% -> 9030). A number from pass 1 is
       replaced only where pass 2 found a percentage at the same place on the page."""

    name = "tesseract"

    def __init__(self, cfg):
        self.wanted = [lang for lang in cfg.OCR_LANGS.split("+") if lang]
        self.langs = cfg.OCR_LANGS
        self.percent_langs = None
        self.dpi = cfg.OCR_DPI
        self.psm = cfg.OCR_PSM

    def available(self):
        if not shutil.which("tesseract"):
            return False
        try:
            import pytesseract

            importlib.import_module("pypdfium2")  # needed to render the pages
            installed = set(pytesseract.get_languages(config=""))
        except Exception:  # noqa: BLE001 - missing package or broken install: OCR is simply off
            return False
        found = [lang for lang in self.wanted if lang in installed]
        missing = [lang for lang in self.wanted if lang not in installed]
        if missing:
            log.warning("Tesseract language data missing: %s", ", ".join(missing))
        if not found or ("ara" in self.wanted and "ara" not in found):
            return False  # Arabic documents would come out as garbage
        self.langs = "+".join(found)
        if "ara" in found and "eng" in installed and "eng" not in found:
            self.percent_langs = "ara+eng"
        return True

    def _words(self, image, langs):
        import pytesseract

        data = pytesseract.image_to_data(image, lang=langs, config=f"--psm {self.psm}",
                                         output_type=pytesseract.Output.DICT)
        words = []
        for i, text in enumerate(data["text"]):
            if text and text.strip():
                left, top = data["left"][i], data["top"][i]
                words.append({
                    "text": text.strip(),
                    "line": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
                    "n": data["word_num"][i],
                    "box": (left, top, left + data["width"][i], top + data["height"][i]),
                })
        return words

    def page_text(self, image):
        image = self.binarize(image)
        words = self._words(image, self.langs)
        if self.percent_langs and any(_DIGIT.search(w["text"]) for w in words):
            for fix in self._words(image, self.percent_langs):
                if not _PERCENT.search(fix["text"]):
                    continue
                target = max(words, key=lambda w: _overlap(fix["box"], w["box"]), default=None)
                if (target is not None and _overlap(fix["box"], target["box"]) >= 0.5
                        and _DIGIT.search(target["text"]) and not _PERCENT.search(target["text"])):
                    target["text"] = fix["text"]
        lines = {}
        for word in words:
            lines.setdefault(word["line"], []).append(word)
        return "\n".join(" ".join(w["text"] for w in sorted(group, key=lambda w: w["n"]))
                         for _, group in sorted(lines.items()))

    @staticmethod
    def binarize(image):
        """Black text on white: removes scan noise and grey backgrounds (much better accuracy)."""
        from PIL import ImageOps

        grey = ImageOps.autocontrast(image.convert("L"))
        hist = grey.histogram()[:256]
        total = sum(hist)
        sum_all = sum(i * h for i, h in enumerate(hist))
        weight = acc = 0
        best, threshold = -1.0, 128
        for level in range(256):  # Otsu's threshold
            weight += hist[level]
            if not weight or weight == total:
                continue
            acc += level * hist[level]
            mean_b, mean_f = acc / weight, (sum_all - acc) / (total - weight)
            between = weight * (total - weight) * (mean_b - mean_f) ** 2
            if between > best:
                best, threshold = between, level
        threshold = min(threshold + 20, 210)  # keep thin strokes of Arabic letters and % signs
        return grey.point(lambda v: 255 if v > threshold else 0)



class VisionOcr:
    name = "vision"

    def __init__(self, cfg):
        self.base_url = cfg.OCR_VISION_BASE_URL.rstrip("/")
        self.model = cfg.OCR_VISION_MODEL
        self.api_key = cfg.OCR_VISION_API_KEY
        self.timeout = cfg.LLM_TIMEOUT
        self.dpi = min(cfg.OCR_DPI, 200)

    def available(self):
        return bool(self.model)

    def page_text(self, image):
        buffer = io.BytesIO()
        image.thumbnail((1700, 2200))
        image.save(buffer, format="JPEG", quality=88)
        url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": url}},
            ]}],
        }
        response = requests.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload), timeout=(15, self.timeout),
        )
        response.raise_for_status()
        return strip_thinking(response.json()["choices"][0]["message"].get("content") or "")


def make_ocr(cfg):
    """The configured OCR engine, or None when OCR is off or not installed."""
    choice = (cfg.OCR_ENGINE or "auto").lower()
    if choice == "off":
        return None
    candidates = {"vision": [VisionOcr], "tesseract": [TesseractOcr]}.get(choice, [VisionOcr, TesseractOcr])
    for cls in candidates:
        engine = cls(cfg)
        if engine.available():
            return engine
    if choice != "auto":
        log.warning("OCR engine %r requested but not available", choice)
    return None
