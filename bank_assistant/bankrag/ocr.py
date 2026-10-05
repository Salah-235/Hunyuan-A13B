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
import os
import re
import shutil

import requests

from .llm import strip_thinking

# Tesseract's OpenMP threads spin and starve each other under any other CPU load: one thread per call
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

log = logging.getLogger(__name__)

VISION_PROMPT = (
    "Transcribe all the text on this scanned page of a bank document exactly as written, in reading order. "
    "Keep Arabic and French text as it is, do not translate, summarise or add anything. Copy numbers, "
    "percentages, amounts and dates exactly. Write each table row on one line with cells separated by ' | '. "
    "If a word is unreadable write [?]. Output only the page text."
)


MAX_RENDER_SIDE = 4000  # pixels: a page drawn as a huge "photo" must not need gigabytes of memory


class PageRenderer:
    """Renders single PDF pages to images; open once per document, one page at a time."""

    def __init__(self, data):
        import pypdfium2 as pdfium

        self.pdf = pdfium.PdfDocument(data)

    def render(self, number, dpi, grayscale=False):
        page = self.pdf[number - 1]
        try:
            width, height = page.get_size()
            scale = min(dpi / 72, MAX_RENDER_SIDE / max(width, height, 1))
            image = page.render(scale=scale, grayscale=grayscale).to_pil()
            return image.convert("L" if grayscale else "RGB")
        finally:
            page.close()

    def close(self):
        self.pdf.close()


_PERCENT = re.compile(r"[0-9٠-٩]\s*[%٪]|[%٪]\s*[0-9٠-٩]")
_NUMBER = re.compile(r"[0-9][0-9.,]*")
TESSERACT_TIMEOUT = 120  # seconds per call: one pathological page must not block the indexer


class TesseractOcr:
    """Tesseract on this server.

    Pages are turned into black text on white with table rules removed (Tesseract drops the text of
    ruled cells otherwise). Next to Arabic text the Arabic/French models often read "30%" as "9030",
    so every number is re-read on its own with the English model limited to digits and "%"; the
    re-read replaces the number only when it found a percent sign and the same digits."""

    name = "tesseract"
    grayscale = True

    def __init__(self, cfg):
        self.wanted = [lang for lang in cfg.OCR_LANGS.split("+") if lang]
        self.langs = cfg.OCR_LANGS
        self.number_lang = None
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
        self.number_lang = "eng" if "eng" in installed else None
        return True

    def _words(self, image, langs, config):
        import pytesseract

        data = pytesseract.image_to_data(image, lang=langs, config=config, timeout=TESSERACT_TIMEOUT,
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

    def _fix_percentages(self, image, words):
        """Re-read every number in one strip image (English model, digits and % only)."""
        from PIL import Image

        targets = [w for w in words if _NUMBER.search(w["text"]) and not _PERCENT.search(w["text"])
                   and len(w["text"]) <= 16]
        if not targets or not self.number_lang:
            return
        pad, gap = 6, 24
        crops = []
        for word in targets:
            left, top, right, bottom = word["box"]
            crops.append(image.crop((max(0, left - pad), max(0, top - pad),
                                     min(image.width, right + pad), min(image.height, bottom + pad))))
        width = max(c.width for c in crops) + 2 * gap
        height = sum(c.height + gap for c in crops) + gap
        strip = Image.new("L", (width, height), 255)
        bands, y = [], gap
        for crop in crops:
            strip.paste(crop, (gap, y))
            bands.append((y, y + crop.height))
            y += crop.height + gap
        found = {}
        config = "--psm 6 -c tessedit_char_whitelist=0123456789%.,"
        for item in self._words(strip, self.number_lang, config):
            middle = (item["box"][1] + item["box"][3]) / 2
            for index, (top, bottom) in enumerate(bands):
                if top - gap / 2 <= middle <= bottom + gap / 2:
                    found[index] = found.get(index, "") + item["text"]
                    break
        for index, text in found.items():
            word = targets[index]
            digits = re.sub(r"\D", "", text)
            match = _NUMBER.search(word["text"])
            # the misread sign shows up as extra digits: accept only a % reading of the same digits
            if "%" in text and digits and digits in re.sub(r"\D", "", match.group()):
                word["text"] = word["text"][:match.start()] + text + word["text"][match.end():]

    @staticmethod
    def binarize(image):
        """Black text on white with long table rules removed."""
        import numpy as np
        from PIL import Image, ImageOps

        grey = np.asarray(ImageOps.autocontrast(image.convert("L")), dtype=np.uint8)
        hist = np.bincount(grey.ravel(), minlength=256).astype(np.float64)
        levels = np.arange(256)
        weight = np.cumsum(hist)
        mean = np.cumsum(hist * levels)
        total, total_mean = weight[-1], mean[-1]
        with np.errstate(divide="ignore", invalid="ignore"):  # Otsu's threshold
            between = (total_mean * weight - mean * total) ** 2 / (weight * (total - weight))
        threshold = int(np.nanargmax(between)) if np.isfinite(between).any() else 128
        threshold = min(threshold + 20, 210)  # keeps thin strokes of Arabic letters and % signs
        ink = grey <= threshold
        height, width = ink.shape
        for axis, length in ((1, max(120, width // 12)), (0, max(120, height // 20))):
            ink &= ~_long_runs(ink, axis, length)
        return Image.fromarray(np.where(ink, 0, 255).astype(np.uint8))

    def page_text(self, image):
        image = self.binarize(image)
        words = self._words(image, self.langs, f"--psm {self.psm}")
        self._fix_percentages(image, words)
        lines = {}
        for word in words:
            lines.setdefault(word["line"], []).append(word)
        return "\n".join(" ".join(w["text"] for w in sorted(group, key=lambda w: w["n"]))
                         for _, group in sorted(lines.items()))


def _long_runs(mask, axis, length):
    """Pixels that belong to a straight run of at least `length` set pixels along `axis`."""
    import numpy as np

    m = np.moveaxis(mask, axis, -1).astype(np.int32)
    csum = np.concatenate([np.zeros(m.shape[:-1] + (1,), np.int32), np.cumsum(m, axis=-1)], axis=-1)
    full = (csum[..., length:] - csum[..., :-length]) == length   # windows that are entirely set
    starts = np.concatenate([np.zeros(full.shape[:-1] + (1,), np.int32), np.cumsum(full, axis=-1)], axis=-1)
    n = m.shape[-1]
    idx = np.arange(n)
    lo = np.clip(idx - length + 1, 0, full.shape[-1])
    hi = np.clip(idx + 1, 0, full.shape[-1])
    covered = (starts[..., hi] - starts[..., lo]) > 0               # some full window covers the pixel
    return np.moveaxis(covered, -1, axis)


class VisionOcr:
    name = "vision"

    def __init__(self, cfg):
        self.base_url = cfg.OCR_VISION_BASE_URL.rstrip("/")
        self.model = cfg.OCR_VISION_MODEL
        self.api_key = cfg.OCR_VISION_API_KEY
        self.timeout = cfg.OCR_TIMEOUT
        self.dpi = min(cfg.OCR_DPI, 200)

    grayscale = False

    def available(self):
        return bool(self.model)

    def page_text(self, image):
        buffer = io.BytesIO()
        image.thumbnail((1700, 2200))
        image.save(buffer, format="JPEG", quality=88)
        url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        payload = {
            "model": self.model,
            "temperature": 0,
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
