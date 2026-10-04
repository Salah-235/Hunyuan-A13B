"""Text extraction (PDF, Word, Excel, CSV, text) and structure-aware chunking."""
import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

from .textproc import clean_text

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}


@dataclass
class Unit:
    """One line / paragraph / table row of a document."""
    text: str
    page: int | None = None
    level: int = 0  # 0 = body text, 1 = chapter-like heading, 2 = article-like heading


@dataclass
class Chunk:
    text: str
    page_start: int | None
    page_end: int | None
    heading: str = ""


@dataclass
class Extraction:
    units: list = field(default_factory=list)
    pages: int = 0
    warning: str = ""


# A heading keyword only counts when a number or ordinal follows it, so wrapped lines that merely
# start with "partie", "titre" or "الجزء" stay body text.
_NUM = (r"(?:\d+|[٠-٩]+|[IVXLC]+(?![^\W\d_])|[A-Z](?![^\W\d_])|premier|première|1er|unique|PREMIER|UNIQUE|"
        r"[أا]ل[أا]ول[ى]?|الثاني[ة]?|الثالث[ة]?|الرابع[ة]?|الخامس[ة]?|السادس[ة]?|السابع[ة]?|الثامن[ة]?|"
        r"التاسع[ة]?|العاشر[ة]?|الحادي[ة]?)")
_LEVEL1 = re.compile(
    r"^(?:الباب|الفصل|القسم|الجزء|الملحق|TITRE|Titre|CHAPITRE|Chapitre|PARTIE|Partie|SECTION|Section|"
    r"ANNEXE|Annexe|CHAPTER|Chapter|PART|Part)\s+" + _NUM + r"(?!\w)"
)
_LEVEL2 = re.compile(
    r"^(?:(?:المادة|مادة|البند)\s+" + _NUM + r"|(?:ARTICLE|Article|Art\.)\s*(?:\d+|premier|1er|unique|PREMIER|UNIQUE))(?!\w)"
)


def heading_level(line):
    if len(line) > 150:
        return 0
    if _LEVEL1.match(line):
        return 1
    if _LEVEL2.match(line):
        return 2
    return 0


def _lines_to_units(text, page=None):
    units = []
    for line in clean_text(text).split("\n"):
        line = line.strip()
        if line:
            units.append(Unit(line, page, heading_level(line)))
    return units


# --------------------------------------------------------------------------- PDF + bidi
#
# PDFs store glyphs in drawing (visual) order. To recover reading (logical) order we resolve
# embedding levels with the Unicode bidi algorithm (rules W1-W7, N1-N2, I1-I2 for one paragraph
# without explicit embeddings) and undo the line reordering of rule L2.

_RTL_CHAR = re.compile("[\u0590-\u065F\u066A-\u06EF\u06FA-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFE]")
# Latin letters and digits; European and Arabic-Indic digits are both laid out left-to-right.
_LTR_CHAR = re.compile("[A-Za-z0-9\u00C0-\u024F\u0660-\u0669\u06F0-\u06F9]")
_LATIN_LETTER = re.compile("[A-Za-z\u00C0-\u024F]")
_JOINERS = set(" .,:/-'’_")
_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")
CELL_GAP = 1.6  # a gap wider than 1.6 x font size separates table cells
_PCT_BEFORE_NUMBER = re.compile("(?<![0-9٠-٩])([%٪‰])([0-9]+(?:[.,][0-9]+)*|[٠-٩]+(?:[٫٬][٠-٩]+)*)")

_AN = re.compile("[\u0660-\u0669\u066B\u066C]")
_EN = re.compile("[0-9\u06F0-\u06F9]")
_ET = re.compile("[\u066A%\u2030\u2031$\u20AC\u00A3\u00A5#\u00B0\u00A2]")
_R = re.compile("[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFE]")


def _is_ltr(glyph):
    return bool(_LTR_CHAR.search(glyph)) and not _RTL_CHAR.search(glyph)


def _is_rtl(glyph):
    return bool(_RTL_CHAR.search(glyph))


def bidi_type(glyph):
    ch = glyph[0] if glyph else " "
    if unicodedata.category(ch) == "Mn":
        return "NSM"
    if _AN.match(ch):
        return "AN"
    if _EN.match(ch):
        return "EN"
    if ch == "\u060C":
        return "CS"
    if _ET.match(ch):
        return "ET"
    if _R.match(ch):
        return "R"
    if ch in "+-\u2212":
        return "ES"
    if ch in ",.:/\u00A0":
        return "CS"
    if ch.isspace():
        return "WS"
    if ch.isalpha():
        return "L"
    return "ON"


def resolve_levels(types, rtl):
    """Embedding level (0, 1 or 2) of each character, in logical order."""
    base = "R" if rtl else "L"
    t = list(types)
    n = len(t)
    for i in range(n):  # W1
        if t[i] == "NSM":
            t[i] = t[i - 1] if i else base
    last = "sos"  # the paragraph start is not an Arabic letter: a leading number stays European
    for i in range(n):  # W2
        if t[i] in ("R", "L"):
            last = t[i]
        elif t[i] == "EN" and last == "R":
            t[i] = "AN"
    for i in range(1, n - 1):  # W4
        if t[i] == "ES" and t[i - 1] == "EN" and t[i + 1] == "EN":
            t[i] = "EN"
        elif t[i] == "CS" and t[i - 1] in ("EN", "AN") and t[i + 1] == t[i - 1]:
            t[i] = t[i - 1]
    i = 0
    while i < n:  # W5
        if t[i] == "ET":
            j = i
            while j < n and t[j] == "ET":
                j += 1
            if (i > 0 and t[i - 1] == "EN") or (j < n and t[j] == "EN"):
                t[i:j] = ["EN"] * (j - i)
            i = j
        else:
            i += 1
    t = ["ON" if x in ("ES", "ET", "CS") else x for x in t]  # W6
    last = base
    for i in range(n):  # W7
        if t[i] in ("R", "L"):
            last = t[i]
        elif t[i] == "EN" and last == "L":
            t[i] = "L"

    def strong(x):
        return "L" if x == "L" else "R" if x in ("R", "EN", "AN") else None

    i = 0
    while i < n:  # N1, N2
        if t[i] in ("WS", "ON"):
            j = i
            while j < n and t[j] in ("WS", "ON"):
                j += 1
            before = strong(t[i - 1]) if i > 0 else base
            after = strong(t[j]) if j < n else base
            t[i:j] = [before if before == after else base] * (j - i)
            i = j
        else:
            i += 1
    if rtl:  # I1, I2
        return [1 if x == "R" else 2 for x in t]
    return [0 if x == "L" else 1 if x == "R" else 2 for x in t]


def _reverse_runs(items, min_level):
    out, i = [], 0
    while i < len(items):
        if items[i][2] >= min_level:
            j = i
            while j < len(items) and items[j][2] >= min_level:
                j += 1
            out.extend(reversed(items[i:j]))
            i = j
        else:
            out.append(items[i])
            i += 1
    return out


def _flip_runs(seq, member):
    """Approximate logical order (used only to resolve levels): keep letter/number clusters readable."""
    out, i, n = [], 0, len(seq)
    while i < n:
        if member(seq[i][0]):
            j = i + 1
            while j < n:
                if member(seq[j][0]):
                    j += 1
                    continue
                if seq[j][0] in _JOINERS:
                    k = j
                    while k < n and seq[k][0] in _JOINERS:
                        k += 1
                    if k < n and member(seq[k][0]):
                        j = k
                        continue
                break
            out.extend(reversed(seq[i:j]))
            i = j
        else:
            out.append(seq[i])
            i += 1
    return out


def _line_direction(glyphs, rtl_hint=None):
    strong = []
    for glyph in glyphs:
        if _is_rtl(glyph):
            strong.append("R")
        elif _LATIN_LETTER.search(glyph):  # digits are weak: they never decide the direction
            strong.append("L")
    if "R" not in strong:
        return None
    if strong[0] == strong[-1]:
        return strong[0] == "R"
    if rtl_hint is not None:
        return rtl_hint
    return strong.count("R") >= strong.count("L")


def visual_to_logical(glyphs, rtl_hint=None, force_dir=None):
    """Turn one line (or table cell) of glyph texts in visual (left-to-right) order into logical order.

    Arabic PDFs store glyphs as they are drawn; reading them left-to-right puts words backwards
    and numbers in the wrong place ("الشهري3المادة"). `rtl_hint` (the page's dominant direction)
    settles lines whose two ends disagree.
    """
    rtl = _line_direction(glyphs, rtl_hint) if force_dir is None else force_dir
    if rtl is None or not any(map(_is_rtl, glyphs)):
        return "".join(glyphs)
    visual = [[g, pos, 0] for pos, g in enumerate(glyphs)]
    approx = _flip_runs(list(reversed(visual)), _is_ltr) if rtl else _flip_runs(list(visual), _is_rtl)
    for item, level in zip(approx, resolve_levels([bidi_type(x[0]) for x in approx], rtl)):
        item[2] = level
    logical = _reverse_runs(_reverse_runs(visual, 1), 2)
    text = "".join(g.translate(_MIRROR) if level % 2 == 1 and len(g) == 1 else g for g, _pos, level in logical)
    if rtl:
        # A line that continues an Arabic paragraph draws "6.5%" as "%6.5"; percentages are always
        # written number first in Arabic and French, so restore that order.
        text = _PCT_BEFORE_NUMBER.sub(r"\2\1", text)
    return text


def logical_to_visual(chars, rtl):
    """Logical text -> visual glyph order (rule L2)."""
    items = [[g, i, level] for i, (g, level) in enumerate(zip(chars, resolve_levels([bidi_type(c) for c in chars], rtl)))]
    return [g.translate(_MIRROR) if level % 2 == 1 and len(g) == 1 else g
            for g, _i, level in _reverse_runs(_reverse_runs(items, 2), 1)]


def row_to_logical(segments, rtl_hint=None):
    """A row of visual segments (table cells split at wide gaps) -> one logical line."""
    flat = [g for seg in segments for g in seg]
    rtl = _line_direction(flat, rtl_hint)
    if rtl is None:
        return " | ".join(t for t in ("".join(s).strip() for s in segments) if t)
    cells = [c for c in (visual_to_logical(s, rtl_hint, rtl).strip() for s in segments) if c]
    return " | ".join(reversed(cells) if rtl else cells)


def _page_chars(layout):
    from pdfminer.layout import LTChar

    for node in layout:
        if isinstance(node, LTChar):
            yield node
        elif hasattr(node, "__iter__"):
            yield from _page_chars(node)


def _cell_edges(layout):
    """Vertical edges of drawn boxes and rules (table borders): (x, y_low, y_high)."""
    from pdfminer.layout import LTCurve

    edges = []
    for node in layout:
        if isinstance(node, LTCurve):  # LTLine and LTRect are curves too
            if node.y1 - node.y0 >= 4:
                edges.append((node.x0, node.y0, node.y1))
                if node.x1 - node.x0 > 1:
                    edges.append((node.x1, node.y0, node.y1))
        elif hasattr(node, "__iter__") and not hasattr(node, "get_text"):
            edges.extend(_cell_edges(node))
    return edges


def _pdf_lines_pdfminer(data):
    """Rows are rebuilt from glyph positions over the whole page (not pdfminer's text boxes), so a
    table row stays one line with its cells separated by " | "."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams

    pages = []
    for page in extract_pages(io.BytesIO(data), laparams=LAParams(line_margin=0.4)):
        chars = sorted(_page_chars(page), key=lambda c: -(c.y0 + c.y1) / 2)
        rows = []
        for char in chars:
            middle = (char.y0 + char.y1) / 2
            if rows and abs(rows[-1][0] - middle) < 0.45 * max(char.size, 1):
                rows[-1][1].append(char)
            else:
                rows.append([middle, [char]])
        edges = _cell_edges(page)
        glyph_rows = []
        for middle, row in rows:
            row.sort(key=lambda c: c.x0)
            row_edges = [x for x, low, high in edges if low <= middle <= high]
            segments, prev = [[]], None
            for char in row:
                text = char.get_text()
                seg = segments[-1]
                if prev is not None:
                    gap = char.x0 - prev.x1
                    ruled = any(prev.x1 - 0.5 <= x <= char.x0 + 0.5 for x in row_edges)
                    if (gap > CELL_GAP * max(char.size, 1) or ruled) and seg:
                        seg = []
                        segments.append(seg)
                    elif gap > 0.18 * max(char.size, 1) and not text.isspace() and seg and not seg[-1].isspace():
                        seg.append(" ")
                seg.append(text)
                prev = char
            glyph_rows.append(segments)
        flat = [g for segs in glyph_rows for seg in segs for g in seg]
        rtl_count = sum(map(_is_rtl, flat))
        ltr_count = sum(1 for g in flat if _LATIN_LETTER.search(g))
        page_rtl = rtl_count >= ltr_count
        lines = [re.sub(r"[ \t]+", " ", row_to_logical(segs, page_rtl)).strip() for segs in glyph_rows]
        lines = [re.sub(r" (\S)", lambda m: m.group(0) if unicodedata.category(m.group(1)) != "Mn" else m.group(1), line)
                 for line in lines]
        pages.append("\n".join(line for line in lines if line))
    return pages


def _pdf_lines_pypdf(data):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("encrypted_pdf") from exc
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not fail the document
            pages.append("")
    return pages


def extract_pdf(data):
    try:
        pages = _pdf_lines_pdfminer(data)
    except Exception as exc:  # noqa: BLE001 - fall back to the simpler extractor
        if "encrypt" in str(exc).lower() or "password" in str(exc).lower():
            raise ValueError("encrypted_pdf") from exc
        pages = _pdf_lines_pypdf(data)
    result = Extraction(pages=len(pages))
    text_chars = 0
    for number, text in enumerate(pages, start=1):
        text_chars += len(text.strip())
        result.units.extend(_lines_to_units(text, number))
    if result.pages and text_chars < 30 * result.pages:
        result.warning = "scanned_pdf"
    return result


def extract_docx(data):
    import docx
    from docx.oxml.ns import qn

    document = docx.Document(io.BytesIO(data))
    result = Extraction()
    page = 1
    saw_break = False

    def page_breaks(element):
        return len(element.findall(".//" + qn("w:lastRenderedPageBreak"))) + len(
            [br for br in element.findall(".//" + qn("w:br")) if br.get(qn("w:type")) == "page"]
        )

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            breaks = page_breaks(child)
            if breaks:
                saw_break = True
                page += breaks
            paragraph = docx.text.paragraph.Paragraph(child, document)
            text = clean_text(paragraph.text)
            if not text:
                continue
            style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
            level = heading_level(text)
            if style.startswith(("heading 1", "titre 1", "title", "titre")):
                level = 1
            elif style.startswith(("heading", "titre")):
                level = level or 2
            for line in text.split("\n"):
                if line.strip():
                    result.units.append(Unit(line.strip(), page, level))
        elif child.tag == qn("w:tbl"):
            table = docx.table.Table(child, document)
            # python-docx repeats a merged cell in every column it spans, which keeps columns aligned.
            rows = [[clean_text(cell.text).replace("\n", " ") for cell in row.cells] for row in table.rows]
            result.units.extend(table_to_units(rows, page))
    if not saw_break:
        for unit in result.units:
            unit.page = None
        result.pages = 0
    else:
        result.pages = page
    return result


def table_to_units(rows, page=None):
    """Rows of a document table -> units, keeping every value in its column.

    A data grid (3+ rows, 3+ columns, filled header row) is written as "Header: value" pairs so a
    chunk stays readable without its header; smaller tables keep positions, with "—" for empty cells.
    """
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header = rows[0]
    labelled = len(rows) >= 3 and len(header) >= 3 and all(header)
    units = [Unit(" | ".join(c or "—" for c in header), page, 0)]
    for row in rows[1:]:
        if labelled:
            parts = [f"{header[i]}: {c}" if i < len(header) else c for i, c in enumerate(row) if c]
        else:
            parts = [c or "—" for c in row]
        units.append(Unit(" | ".join(parts), page, 0))
    return units


def _rows_to_units(rows, sheet_number, sheet_name):
    units = []
    if sheet_name:
        units.append(Unit(sheet_name, sheet_number, 1))
    header = None
    for row in rows:
        values = ["" if v is None else clean_text(str(v)).replace("\n", " ") for v in row]
        if not any(values):
            continue
        if header is None:
            header = values
            units.append(Unit(" | ".join(v for v in values if v), sheet_number, 0))
            continue
        parts = []
        for i, value in enumerate(values):
            if not value:
                continue
            name = header[i] if i < len(header) and header[i] else ""
            parts.append(f"{name}: {value}" if name else value)
        units.append(Unit(" | ".join(parts), sheet_number, 0))
    return units


def extract_xlsx(data):
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    result = Extraction()
    for number, sheet in enumerate(workbook.worksheets, start=1):
        result.units.extend(_rows_to_units(sheet.iter_rows(values_only=True), number, sheet.title))
    result.pages = len(workbook.worksheets)
    workbook.close()
    return result


def decode_text(data):
    for encoding in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    for encoding in ("cp1256", "latin-1"):  # Arabic Windows, then Western
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("utf-8", errors="replace")


def extract_csv(data):
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return Extraction(units=_rows_to_units(rows, None, ""), pages=0)


def extract_text_file(data):
    return Extraction(units=_lines_to_units(decode_text(data)), pages=0)


def extract(data, ext):
    ext = ext.lower()
    if ext == ".pdf":
        return extract_pdf(data)
    if ext == ".docx":
        return extract_docx(data)
    if ext == ".xlsx":
        return extract_xlsx(data)
    if ext == ".csv":
        return extract_csv(data)
    if ext in (".txt", ".md"):
        return extract_text_file(data)
    raise ValueError("unsupported_type")


# --------------------------------------------------------------------------- chunking

_SENTENCE_END = re.compile(r"(?<=[.!?؟؛;:])\s+")


def _split_long(unit, size):
    """Split a unit longer than `size` on sentence boundaries, then hard-cut."""
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(unit.text):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > size:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [Unit(p, unit.page, unit.level) for p in pieces if p]


def chunk_units(units, size=1200, overlap=200):
    """Group units into chunks of about `size` characters.

    Chunks prefer to start at headings (chapter / article), carry `overlap`
    characters of trailing context otherwise, and remember the heading path
    (e.g. "الفصل الثالث › المادة 12") they belong to.
    """
    expanded = []
    for unit in units:
        expanded.extend(_split_long(unit, size) if len(unit.text) > size else [unit])

    chunks = []
    current = []
    current_len = 0
    fresh = 0  # units added since the last flush (overlap units excluded)
    headings = ["", ""]
    chunk_heading = ""

    def heading_path():
        return " › ".join(h for h in headings if h)

    def flush(keep_overlap):
        nonlocal current, current_len, chunk_heading, fresh
        fresh = 0
        if current:
            text = "\n".join(u.text for u in current).strip()
            pages = [u.page for u in current if u.page is not None]
            if len(text) >= 15:
                chunks.append(Chunk(text, min(pages) if pages else None,
                                    max(pages) if pages else None, chunk_heading))
        tail = []
        if keep_overlap and overlap > 0:
            total = 0
            for unit in reversed(current):
                if total + len(unit.text) > overlap:
                    break
                tail.insert(0, unit)
                total += len(unit.text) + 1
        current = tail
        current_len = sum(len(u.text) + 1 for u in tail)
        chunk_heading = heading_path()

    for unit in expanded:
        if unit.level:
            # a chapter always opens a new chunk; an article does once the chunk has some substance
            if fresh and (unit.level == 1 or current_len > size * 0.35):
                flush(keep_overlap=False)
            elif fresh == 0:
                current, current_len = [], 0  # only overlap from the previous section
            if unit.level == 1:
                headings[0], headings[1] = unit.text[:120], ""
            else:
                headings[1] = unit.text[:120]
            if not current:
                chunk_heading = heading_path()
        if current_len + len(unit.text) + 1 > size and current:
            flush(keep_overlap=True)
        if not current:
            chunk_heading = heading_path()
        current.append(unit)
        current_len += len(unit.text) + 1
        fresh += 1
    flush(keep_overlap=False)
    return chunks
