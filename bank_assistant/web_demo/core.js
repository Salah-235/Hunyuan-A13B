/* Core text pipeline (port of bankrag/textproc.py + ingest.py): normalisation, bidi repair,
   chunking and BM25 search. Pure functions, no DOM. */
const Core = (() => {
  "use strict";

  // ------------------------------------------------------------------ normalisation
  const DIGIT_MAP = { "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9" };
  const ARABIC_LETTER = /[ء-ي]/;
  const TOKEN = /[\p{L}\p{N}_]+/gu;

  function cleanText(text) {
    if (!text) return "";
    return text.normalize("NFKC")
      .replace(/ـ/g, "").replace(/\u0000/g, "")
      .replace(/[ \t ​‎‏‪-‮⁦-⁩]+/g, " ")
      .replace(/ *\n */g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  }

  function normalize(text) {
    if (!text) return "";
    return text.normalize("NFKC").normalize("NFKD").replace(/\p{Mn}/gu, "")
      .replace(/ـ/g, "").replace(/ى/g, "ي").replace(/ة/g, "ه")
      .replace(/[٠-٩۰-۹]/g, (d) => DIGIT_MAP[d]).toLowerCase();
  }

  const AR_PREFIXES = ["وال", "بال", "كال", "فال", "لل", "ال"];
  const AR_SUFFIXES = ["ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي"];

  function stem(token) {
    if (ARABIC_LETTER.test(token)) {
      if (token.length >= 4 && token.startsWith("و")) token = token.slice(1);
      for (const p of AR_PREFIXES) {
        if (token.startsWith(p) && token.length - p.length >= 2) { token = token.slice(p.length); break; }
      }
      for (const s of AR_SUFFIXES) {
        if (token.length - s.length >= 2 && token.endsWith(s)) token = token.slice(0, -s.length);
      }
      return token;
    }
    if (/^\d+$/.test(token)) return token;
    if (token.length > 3 && (token.endsWith("s") || token.endsWith("x"))) token = token.slice(0, -1);
    return token;
  }

  const STOPWORDS_RAW = `
في من على الى إلى عن مع هذا هذه هذان ذلك تلك التي الذي الذين اللذين اللتي التى هو هي هم هن انا أنا نحن انت أنت
هل ما ماذا كيف متى أين اين لماذا كم أي اي او أو ثم ان أن إن كان كانت يكون تكون قد لقد لا لم لن ليس ليست بين حتى
عند عندما كل بعض غير بعد قبل اذا إذا كما لكن ايضا أيضا نحو حول ضمن خلال منذ لدى الا إلا وفق حسب فيه فيها منه منها
عليه عليها له لها به بها هناك هنا تم يتم وهو وهي كذلك اريد أريد اعرف أعرف ارجو أرجو يرجى
le la les l de des du d un une et ou en au aux a à pour par sur dans avec sans que qu qui quoi quel quelle quels
quelles est sont etre être ce cet cette ces se sa son ses leur leurs il elle ils elles on nous vous je j tu y ne n
pas plus comme mais donc car si quand comment combien
the a an of to in on for and or is are what how which who when where with by from at as be this that it do does`;
  const STOPWORDS = new Set(STOPWORDS_RAW.split(/\s+/).filter(Boolean).map(normalize));

  function tokenize(text) {
    const out = [];
    for (const raw of normalize(text).match(TOKEN) || []) {
      if (STOPWORDS.has(raw) || (raw.length < 2 && !/^\d$/.test(raw))) continue;
      const tok = stem(raw);
      if (!tok || STOPWORDS.has(tok)) continue;
      out.push(tok);
    }
    return out;
  }

  function detectLang(text) {
    const ar = (text.match(/[ء-ي]/g) || []).length;
    const lat = (text.match(/[A-Za-zÀ-ÿ]/g) || []).length;
    return ar >= lat ? "ar" : "fr";
  }

  // ------------------------------------------------------------------ bidi repair
  // PDFs store glyphs in drawing (visual) order. To recover reading (logical) order we resolve
  // embedding levels with the Unicode bidi algorithm (rules W1-W7, N1-N2, I1-I2 for one
  // paragraph without explicit embeddings) and undo the line reordering (rule L2).
  const RTL_CHAR = /[\u0590-\u065F\u066A-\u06EF\u06FA-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFE]/;
  const LTR_CHAR = /[A-Za-z0-9\u00C0-\u024F\u0660-\u0669\u06F0-\u06F9]/;
  const LATIN_LETTER = /[A-Za-z\u00C0-\u024F]/;
  const JOINERS = new Set([" ", ".", ",", ":", "/", "-", "'", "’", "_"]);
  const MIRROR = { "(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<", "«": "»", "»": "«" };

  const PCT_BEFORE_NUMBER = /(?<![0-9٠-٩])([%٪‰])([0-9]+(?:[.,][0-9]+)*|[٠-٩]+(?:[٫٬][٠-٩]+)*)/g;
  const isRtl = (g) => RTL_CHAR.test(g);
  const isLtr = (g) => LTR_CHAR.test(g) && !RTL_CHAR.test(g);

  function bidiType(g) {
    const ch = g ? g[0] : " ";
    if (/\p{Mn}/u.test(ch)) return "NSM";
    if (/[\u0660-\u0669\u066B\u066C]/.test(ch)) return "AN";
    if (/[0-9\u06F0-\u06F9]/.test(ch)) return "EN";
    if (ch === "\u060C") return "CS";
    if (/[\u066A%\u2030\u2031$\u20AC\u00A3\u00A5#\u00B0\u00A2]/.test(ch)) return "ET";
    if (/[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFE]/.test(ch)) return "R";
    if (/[+\-\u2212]/.test(ch)) return "ES";
    if (/[,.:/\u00A0]/.test(ch)) return "CS";
    if (/\s/.test(ch)) return "WS";
    if (/\p{L}/u.test(ch)) return "L";
    return "ON";
  }

  /** Embedding level (0, 1 or 2) of each character, in logical order, for a paragraph of base level 0 or 1. */
  function resolveLevels(types, rtl) {
    const base = rtl ? "R" : "L";
    const t = types.slice();
    const n = t.length;
    for (let i = 0; i < n; i++) if (t[i] === "NSM") t[i] = i ? t[i - 1] : base;            // W1
    let last = "sos";   // the paragraph start is not an Arabic letter: a leading number stays European
    for (let i = 0; i < n; i++) {                                                          // W2
      if (t[i] === "R" || t[i] === "L") last = t[i];
      else if (t[i] === "EN" && last === "R") t[i] = "AN";
    }
    for (let i = 1; i < n - 1; i++) {                                                      // W4
      if (t[i] === "ES" && t[i - 1] === "EN" && t[i + 1] === "EN") t[i] = "EN";
      else if (t[i] === "CS" && (t[i - 1] === "EN" || t[i - 1] === "AN") && t[i + 1] === t[i - 1]) t[i] = t[i - 1];
    }
    for (let i = 0; i < n; i++) {                                                          // W5
      if (t[i] !== "ET") continue;
      let j = i;
      while (j < n && t[j] === "ET") j++;
      if ((i > 0 && t[i - 1] === "EN") || (j < n && t[j] === "EN")) for (let k = i; k < j; k++) t[k] = "EN";
      i = j - 1;
    }
    for (let i = 0; i < n; i++) if (t[i] === "ES" || t[i] === "ET" || t[i] === "CS") t[i] = "ON";   // W6
    last = base;
    for (let i = 0; i < n; i++) {                                                          // W7
      if (t[i] === "R" || t[i] === "L") last = t[i];
      else if (t[i] === "EN" && last === "L") t[i] = "L";
    }
    const strongOf = (x) => (x === "L" ? "L" : x === "R" || x === "EN" || x === "AN" ? "R" : null);
    for (let i = 0; i < n; i++) {                                                          // N1, N2
      if (t[i] !== "WS" && t[i] !== "ON") continue;
      let j = i;
      while (j < n && (t[j] === "WS" || t[j] === "ON")) j++;
      const before = i > 0 ? strongOf(t[i - 1]) : base;
      const after = j < n ? strongOf(t[j]) : base;
      const dir = before === after ? before : base;
      for (let k = i; k < j; k++) t[k] = dir;
      i = j - 1;
    }
    return t.map((x) => {                                                                  // I1, I2
      if (rtl) return x === "R" ? 1 : 2;
      return x === "L" ? 0 : x === "R" ? 1 : 2;
    });
  }

  function reverseRuns(items, minLevel) {
    const out = [];
    let i = 0;
    while (i < items.length) {
      if (items[i].level >= minLevel) {
        let j = i;
        while (j < items.length && items[j].level >= minLevel) j++;
        for (let k = j - 1; k >= i; k--) out.push(items[k]);
        i = j;
      } else out.push(items[i++]);
    }
    return out;
  }

  /** Approximate logical order (used only to resolve levels): reverse the line, keep letter/number clusters readable. */
  function flipRuns(seq, member) {
    const out = [];
    let i = 0;
    const n = seq.length;
    while (i < n) {
      if (member(seq[i].g)) {
        let j = i + 1;
        while (j < n) {
          if (member(seq[j].g)) { j++; continue; }
          if (JOINERS.has(seq[j].g)) {
            let k = j;
            while (k < n && JOINERS.has(seq[k].g)) k++;
            if (k < n && member(seq[k].g)) { j = k; continue; }
          }
          break;
        }
        for (let x = j - 1; x >= i; x--) out.push(seq[x]);
        i = j;
      } else out.push(seq[i++]);
    }
    return out;
  }

  function lineDirection(glyphs, rtlHint) {
    const strong = [];
    for (const g of glyphs) {
      if (isRtl(g)) strong.push("R");
      else if (LATIN_LETTER.test(g)) strong.push("L");   // digits are weak: they never decide the direction
    }
    if (!strong.includes("R")) return null;
    if (strong[0] === strong[strong.length - 1]) return strong[0] === "R";
    if (rtlHint !== undefined && rtlHint !== null) return rtlHint;
    return strong.filter((s) => s === "R").length >= strong.filter((s) => s === "L").length;
  }

  /** One line (or table cell) of glyph strings in visual (left-to-right) order -> logical text. */
  function visualToLogical(glyphs, rtlHint, forceDir) {
    const rtl = forceDir === undefined ? lineDirection(glyphs, rtlHint) : forceDir;
    if (rtl === null || !glyphs.some(isRtl)) return glyphs.join("");
    const visual = glyphs.map((g, pos) => ({ g, pos, level: 0 }));
    const approx = rtl ? flipRuns(visual.slice().reverse(), isLtr) : flipRuns(visual.slice(), isRtl);
    const levels = resolveLevels(approx.map((x) => bidiType(x.g)), rtl);
    approx.forEach((x, k) => { x.level = levels[k]; });
    const logical = reverseRuns(reverseRuns(visual, 1), 2);
    const text = logical.map((x) => (x.level % 2 === 1 && x.g.length === 1 && MIRROR[x.g] ? MIRROR[x.g] : x.g)).join("");
    // A line that continues an Arabic paragraph draws "6.5%" as "%6.5"; percentages are always
    // written number first in Arabic and French, so restore that order.
    return rtl ? text.replace(PCT_BEFORE_NUMBER, "$2$1") : text;
  }

  /** Logical text -> visual glyph order (rule L2), used to undo pdf.js's own per-item reordering. */
  function logicalToVisual(chars, rtl) {
    const levels = resolveLevels(chars.map(bidiType), rtl);
    const items = chars.map((g, i) => ({ g, level: levels[i] }));
    return reverseRuns(reverseRuns(items, 2), 1)
      .map((x) => (x.level % 2 === 1 && x.g.length === 1 && MIRROR[x.g] ? MIRROR[x.g] : x.g));
  }

  /** A row of visual segments (table cells split at wide gaps) -> one logical line. */
  function rowToLogical(segments, rtlHint) {
    const all = segments.flat();
    const rtl = lineDirection(all, rtlHint);
    if (rtl === null) return segments.map((s) => s.join("").trim()).filter(Boolean).join(" | ");
    const cells = segments.map((s) => visualToLogical(s, rtlHint, rtl).trim()).filter(Boolean);
    return (rtl ? cells.reverse() : cells).join(" | ");
  }

  /** pdf.js text items of one page -> logical lines. pdf.js already reorders each RTL item to
   *  logical order, so items are first put back into visual order, then the whole line is repaired. */
  const CELL_GAP = 1.6;   // a gap wider than 1.6 x font size separates table cells

  /** Vertical edges of drawn boxes and rules (table borders) from a pdf.js operator list: [{x, y0, y1}]. */
  function pdfEdgesFromOps(opList, OPS) {
    const edges = [];
    let ctm = [1, 0, 0, 1, 0, 0];
    const stack = [];
    const mul = (m, n) => [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1], m[0] * n[2] + m[2] * n[3],
      m[1] * n[2] + m[3] * n[3], m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
    const pt = (x, y) => [ctm[0] * x + ctm[2] * y + ctm[4], ctm[1] * x + ctm[3] * y + ctm[5]];
    const addBox = (xa, xb, ya, yb) => {
      const x0 = Math.min(xa, xb), x1 = Math.max(xa, xb), y0 = Math.min(ya, yb), y1 = Math.max(ya, yb);
      if (y1 - y0 < 4) return;
      edges.push({ x: x0, y0, y1 });
      if (x1 - x0 > 1) edges.push({ x: x1, y0, y1 });
    };
    const { fnArray, argsArray } = opList;
    for (let i = 0; i < fnArray.length; i++) {
      const fn = fnArray[i], args = argsArray[i];
      if (fn === OPS.save) stack.push(ctm);
      else if (fn === OPS.restore) ctm = stack.pop() || [1, 0, 0, 1, 0, 0];
      else if (fn === OPS.transform) ctm = mul(ctm, args);
      else if (fn === OPS.paintFormXObjectBegin) { stack.push(ctm); if (args && args[0]) ctm = mul(ctm, args[0]); }
      else if (fn === OPS.paintFormXObjectEnd) ctm = stack.pop() || [1, 0, 0, 1, 0, 0];
      else if (fn === OPS.constructPath) {
        const [ops, coords] = args;
        let k = 0, cur = null;
        for (const op of ops) {
          if (op === OPS.rectangle) {
            const [x, y, w, h] = coords.slice(k, k + 4); k += 4;
            const a = pt(x, y), b = pt(x + w, y + h);
            addBox(a[0], b[0], a[1], b[1]);
          } else if (op === OPS.moveTo) { cur = pt(coords[k], coords[k + 1]); k += 2; }
          else if (op === OPS.lineTo) {
            const next = pt(coords[k], coords[k + 1]); k += 2;
            if (cur && Math.abs(next[0] - cur[0]) < 1) addBox(cur[0], next[0], cur[1], next[1]);
            cur = next;
          } else if (op === OPS.curveTo) { k += 6; cur = pt(coords[k - 2], coords[k - 1]); }
          else if (op === OPS.curveTo2 || op === OPS.curveTo3) { k += 4; cur = pt(coords[k - 2], coords[k - 1]); }
        }
      }
    }
    return edges;
  }

  function pdfItemsToLines(items, edges) {
    edges = edges || [];
    const rows = [];
    for (const it of items) {
      if (!it.str) continue;
      const x = it.transform[4], y = it.transform[5];
      const size = Math.max(Math.abs(it.transform[3]) || 0, Math.abs(it.transform[0]) || 0, 1);
      let row = rows.find((r) => Math.abs(r.y - y) < size * 0.45);
      if (!row) { row = { y, size, items: [] }; rows.push(row); }
      row.mid = row.y + row.size * 0.3;
      row.items.push({ x, w: it.width || 0, str: it.str, dir: it.dir, size });
    }
    rows.sort((a, b) => b.y - a.y);
    const segRows = rows.map((row) => {
      row.items.sort((a, b) => a.x - b.x);
      const segments = [[]];
      const rowEdges = edges.filter((e) => e.y0 <= row.mid && row.mid <= e.y1).map((e) => e.x);
      let prev = null;
      for (const it of row.items) {
        let chars = Array.from(it.str);
        if (it.dir === "rtl" && chars.length > 1) chars = logicalToVisual(chars, true);
        let seg = segments[segments.length - 1];
        if (prev) {
          const gap = it.x - (prev.x + prev.w);
          const ruled = rowEdges.some((x) => prev.x + prev.w - 0.5 <= x && x <= it.x + 0.5);
          if ((gap > CELL_GAP * it.size || ruled) && seg.length) { seg = []; segments.push(seg); }
          else if (gap > 0.18 * it.size && !/^\s/.test(it.str) && seg.length && !/\s$/.test(seg[seg.length - 1])) seg.push(" ");
        }
        seg.push(...chars);
        prev = it;
      }
      return segments;
    });
    let rtlCount = 0, ltrCount = 0;
    for (const g of segRows.flat(2)) { if (isRtl(g)) rtlCount++; else if (LATIN_LETTER.test(g)) ltrCount++; }
    const pageRtl = rtlCount >= ltrCount;
    // a combining mark (tanween, shadda...) never follows a space
    return segRows.map((segs) => rowToLogical(segs, pageRtl).replace(/[ \t]+/g, " ").replace(/ (\p{Mn})/gu, "$1").trim())
      .filter(Boolean);
  }

  // ------------------------------------------------------------------ structure + chunking
  const NUM = "(?:\\d+|[٠-٩]+|[IVXLC]+(?![\\p{L}])|[A-Z](?![\\p{L}])|premier|première|1er|unique|PREMIER|UNIQUE|"
    + "[أا]ل[أا]ول[ى]?|الثاني[ة]?|الثالث[ة]?|الرابع[ة]?|الخامس[ة]?|السادس[ة]?|السابع[ة]?|الثامن[ة]?|التاسع[ة]?|العاشر[ة]?|الحادي[ة]?)";
  const LEVEL1 = new RegExp("^(?:الباب|الفصل|القسم|الجزء|الملحق|TITRE|Titre|CHAPITRE|Chapitre|PARTIE|Partie|SECTION|Section|ANNEXE|Annexe|CHAPTER|Chapter|PART|Part)\\s+"
    + NUM + "(?![\\p{L}\\p{N}_])", "u");
  const LEVEL2 = new RegExp("^(?:(?:المادة|مادة|البند)\\s+" + NUM + "|(?:ARTICLE|Article|Art\\.)\\s*(?:\\d+|premier|1er|unique|PREMIER|UNIQUE))(?![\\p{L}\\p{N}_])", "u");

  function headingLevel(line) {
    if (line.length > 150) return 0;
    if (LEVEL1.test(line)) return 1;
    if (LEVEL2.test(line)) return 2;
    return 0;
  }

  function linesToUnits(text, page) {
    return cleanText(text).split("\n").map((l) => l.trim()).filter(Boolean)
      .map((l) => ({ text: l, page: page ?? null, level: headingLevel(l) }));
  }

  const SENTENCE_END = /(?<=[.!?؟؛;:])\s+/;

  function splitLong(unit, size) {
    const pieces = [];
    let current = "";
    for (let sentence of unit.text.split(SENTENCE_END)) {
      while (sentence.length > size) {
        let cut = sentence.lastIndexOf(" ", size);
        cut = cut > size / 2 ? cut : size;
        if (current) { pieces.push(current); current = ""; }
        pieces.push(sentence.slice(0, cut).trim());
        sentence = sentence.slice(cut).trim();
      }
      if (current && current.length + 1 + sentence.length > size) { pieces.push(current); current = sentence; }
      else current = (current + " " + sentence).trim();
    }
    if (current) pieces.push(current);
    return pieces.filter(Boolean).map((p) => ({ text: p, page: unit.page, level: unit.level }));
  }

  function chunkUnits(units, size = 1200, overlap = 200) {
    const expanded = [];
    for (const u of units) expanded.push(...(u.text.length > size ? splitLong(u, size) : [u]));
    const chunks = [];
    let current = [], currentLen = 0, fresh = 0;
    const headings = ["", ""];
    let chunkHeading = "";
    const headingPath = () => headings.filter(Boolean).join(" › ");

    function flush(keepOverlap) {
      fresh = 0;
      if (current.length) {
        const text = current.map((u) => u.text).join("\n").trim();
        const pages = current.map((u) => u.page).filter((p) => p !== null && p !== undefined);
        if (text.length >= 15) {
          chunks.push({ text, p0: pages.length ? Math.min(...pages) : null,
            p1: pages.length ? Math.max(...pages) : null, h: chunkHeading });
        }
      }
      let tail = [];
      if (keepOverlap && overlap > 0) {
        let total = 0;
        for (let i = current.length - 1; i >= 0; i--) {
          if (total + current[i].text.length > overlap) break;
          tail.unshift(current[i]);
          total += current[i].text.length + 1;
        }
      }
      current = tail;
      currentLen = tail.reduce((s, u) => s + u.text.length + 1, 0);
      chunkHeading = headingPath();
    }

    for (const unit of expanded) {
      if (unit.level) {
        if (fresh && (unit.level === 1 || currentLen > size * 0.35)) flush(false);
        else if (fresh === 0) { current = []; currentLen = 0; }
        if (unit.level === 1) { headings[0] = unit.text.slice(0, 120); headings[1] = ""; }
        else headings[1] = unit.text.slice(0, 120);
        if (!current.length) chunkHeading = headingPath();
      }
      if (currentLen + unit.text.length + 1 > size && current.length) flush(true);
      if (!current.length) chunkHeading = headingPath();
      current.push(unit);
      currentLen += unit.text.length + 1;
      fresh++;
    }
    flush(false);
    return chunks;
  }

  // ------------------------------------------------------------------ BM25 search
  class Index {
    constructor(entries) {
      // entries: [{key, docId, title, chunk}]
      this.entries = entries;
      this.postings = new Map();
      this.lengths = [];
      let total = 0;
      entries.forEach((e, idx) => {
        const toks = tokenize(`${e.title}\n${e.chunk.h || ""}\n${e.chunk.text}`);
        this.lengths.push(toks.length);
        total += toks.length;
        const tf = new Map();
        for (const t of toks) tf.set(t, (tf.get(t) || 0) + 1);
        for (const [t, c] of tf) {
          if (!this.postings.has(t)) this.postings.set(t, []);
          this.postings.get(t).push([idx, c]);
        }
      });
      this.avgdl = entries.length ? total / entries.length : 1;
    }

    search(query, limit, allowed) {
      const terms = [...new Set(tokenize(query))].slice(0, 40);
      const N = this.entries.length;
      const scores = new Map();
      const k1 = 1.2, b = 0.75;
      for (const t of terms) {
        const list = this.postings.get(t);
        if (!list) continue;
        const idf = Math.max(Math.log((N - list.length + 0.5) / (list.length + 0.5)), 1e-6);
        for (const [idx, tf] of list) {
          if (allowed && !allowed.has(this.entries[idx].docId)) continue;
          const dl = this.lengths[idx];
          const s = idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / this.avgdl));
          scores.set(idx, (scores.get(idx) || 0) + s);
        }
      }
      return [...scores.entries()].sort((a, b2) => b2[1] - a[1]).slice(0, limit).map(([idx, s]) => [idx, s]);
    }

    /** Several queries fused with reciprocal rank fusion. */
    searchAll(queries, topK, allowed) {
      const fused = new Map();
      const pool = Math.max(topK * 4, 30);
      for (const q of queries) {
        this.search(q, pool, allowed).forEach(([idx], rank) => {
          fused.set(idx, (fused.get(idx) || 0) + 1 / (60 + rank + 1));
        });
      }
      return [...fused.entries()].sort((a, b) => b[1] - a[1]).slice(0, topK).map(([idx]) => this.entries[idx]);
    }
  }

  return { cleanText, normalize, stem, tokenize, detectLang, visualToLogical, logicalToVisual, rowToLogical, pdfEdgesFromOps, pdfItemsToLines,
    headingLevel, linesToUnits, chunkUnits, Index };
})();
if (typeof module !== "undefined") module.exports = Core;
