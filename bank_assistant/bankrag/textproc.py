"""Text normalisation for search in Arabic, French and English.

Search text and queries go through the same pipeline so that spelling
variants still match: diacritics / accents removed, Alef/Hamza/Yeh/Teh-marbuta
forms unified, Arabic-Indic digits converted, light Arabic stemming
(Light10-style prefix/suffix stripping) and stop-word removal.
"""
import re
import unicodedata

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_LETTERS = re.compile(r"[ء-ي]")
_TOKEN = re.compile(r"\w+", re.UNICODE)


def clean_text(text):
    """Light cleanup applied to stored/displayed text (keeps diacritics)."""
    if not text:
        return ""
    # NFKC folds Arabic presentation forms (common in PDF extraction) into normal letters.
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("ـ", "")  # tatweel
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t ​‎‏‪-‮⁦-⁩]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize(text):
    """Aggressive normalisation used only for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = unicodedata.normalize("NFKD", text)
    # Drops Arabic harakat, hamza/madda marks (أ إ آ ؤ ئ -> ا ا ا و ي) and Latin accents.
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("ـ", "").replace("ى", "ي").replace("ة", "ه")
    text = text.translate(_DIGITS)
    return text.casefold()


_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال")
_AR_SUFFIXES = ("ها", "ان", "ات", "ون", "ين", "يه", "ه", "ي")


def stem(token):
    if _ARABIC_LETTERS.search(token):
        if len(token) >= 4 and token.startswith("و"):
            token = token[1:]
        for prefix in _AR_PREFIXES:
            if token.startswith(prefix) and len(token) - len(prefix) >= 2:
                token = token[len(prefix):]
                break
        for suffix in _AR_SUFFIXES:
            if len(token) - len(suffix) >= 2 and token.endswith(suffix):
                token = token[: -len(suffix)]
        return token
    if token.isdigit():
        return token
    if len(token) > 3 and token.endswith(("s", "x")):
        token = token[:-1]
    return token


_STOPWORDS_RAW = """
في من على الى إلى عن مع هذا هذه هذان ذلك تلك التي الذي الذين اللذين اللتي التى هو هي هم هن انا أنا نحن انت أنت
هل ما ماذا كيف متى أين اين لماذا كم أي اي او أو ثم ان أن إن كان كانت يكون تكون قد لقد لا لم لن ليس ليست بين حتى
عند عندما كل بعض غير بعد قبل اذا إذا كما لكن ايضا أيضا نحو حول ضمن خلال منذ لدى الا إلا وفق حسب فيه فيها منه منها
عليه عليها له لها به بها هناك هنا تم يتم وهو وهي كذلك اريد أريد اعرف أعرف ارجو أرجو يرجى
le la les l de des du d un une et ou en au aux a à pour par sur dans avec sans que qu qui quoi quel quelle quels
quelles est sont etre être ce cet cette ces se sa son ses leur leurs il elle ils elles on nous vous je j tu y ne n
pas plus comme mais donc car si quand comment combien
the a an of to in on for and or is are what how which who when where with by from at as be this that it do does
"""
STOPWORDS = {normalize(w) for w in _STOPWORDS_RAW.split()}


def tokenize(text):
    """Normalised, stemmed search tokens (stop-words and 1-letter tokens removed)."""
    tokens = []
    for raw in _TOKEN.findall(normalize(text)):
        if raw in STOPWORDS or (len(raw) < 2 and not raw.isdigit()):
            continue
        token = stem(raw)
        if token in STOPWORDS or not token:
            continue
        tokens.append(token)
    return tokens


def search_text(text):
    return " ".join(tokenize(text))


def fts_query(text, max_terms=40):
    """Build an FTS5 MATCH expression (OR of quoted terms) from free text."""
    seen = []
    for token in tokenize(text):
        if token not in seen:
            seen.append(token)
        if len(seen) >= max_terms:
            break
    return " OR ".join('"%s"' % t.replace('"', "") for t in seen)


def detect_lang(text):
    """Very small heuristic: 'ar' when the text is mostly Arabic script, else 'fr'."""
    arabic = len(_ARABIC_LETTERS.findall(text or ""))
    latin = len(re.findall(r"[A-Za-zÀ-ÿ]", text or ""))
    return "ar" if arabic >= latin else "fr"
