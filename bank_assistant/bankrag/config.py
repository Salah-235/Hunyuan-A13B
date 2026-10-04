"""Application settings, read from environment variables (or a .env file)."""
import json
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Minimal .env loader: KEY=VALUE lines, existing env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(name, default):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def _float(name, default):
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


class Config:
    def __init__(self, **overrides):
        _load_dotenv(BASE_DIR / ".env")
        env = os.environ.get

        self.DATA_DIR = Path(env("DATA_DIR") or BASE_DIR / "data").resolve()
        self.FILES_DIR = self.DATA_DIR / "files"
        self.DB_PATH = self.DATA_DIR / "app.db"

        # --- LLM (any OpenAI-compatible server: vLLM / SGLang serving Hunyuan-A13B, Ollama, ...) ---
        self.LLM_BASE_URL = (env("LLM_BASE_URL") or "http://localhost:8000/v1").rstrip("/")
        self.LLM_API_KEY = env("LLM_API_KEY") or "EMPTY"
        self.LLM_MODEL = env("LLM_MODEL") or "tencent/Hunyuan-A13B-Instruct"
        self.LLM_TEMPERATURE = _float("LLM_TEMPERATURE", 0.2)
        self.LLM_MAX_TOKENS = _int("LLM_MAX_TOKENS", 8192)
        self.LLM_TIMEOUT = _int("LLM_TIMEOUT", 300)
        # Hunyuan-A13B "thinks" before answering by default (slower, more accurate).
        self.LLM_THINKING = _bool("LLM_THINKING", True)
        is_hunyuan = "hunyuan" in self.LLM_MODEL.lower()
        self.LLM_NO_THINK_PREFIX = env("LLM_NO_THINK_PREFIX", "/no_think" if is_hunyuan else "")
        # Extra sampling fields sent as-is (vLLM/SGLang understand top_k etc.; plain OpenAI does not).
        default_extra = (
            '{"top_k": 20, "repetition_penalty": 1.05, "stop_token_ids": [127960]}' if is_hunyuan else "{}"
        )
        self.LLM_EXTRA_BODY = json.loads(env("LLM_EXTRA_BODY") or default_extra)

        # --- Embeddings (optional; enables semantic + cross-language search) ---
        self.EMBEDDING_MODEL = env("EMBEDDING_MODEL") or ""
        self.EMBEDDING_BASE_URL = (env("EMBEDDING_BASE_URL") or self.LLM_BASE_URL).rstrip("/")
        self.EMBEDDING_API_KEY = env("EMBEDDING_API_KEY") or self.LLM_API_KEY

        # --- Retrieval ---
        self.CHUNK_SIZE = _int("CHUNK_SIZE", 1200)
        self.CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 200)
        self.TOP_K = _int("TOP_K", 8)
        self.QUERY_REWRITE = _bool("QUERY_REWRITE", True)
        self.HISTORY_TURNS = _int("HISTORY_TURNS", 4)

        # --- Security ---
        self.SESSION_HOURS = _int("SESSION_HOURS", 10)
        self.IDLE_MINUTES = _int("IDLE_MINUTES", 60)
        self.COOKIE_SECURE = _bool("COOKIE_SECURE", False)
        self.MAX_LOGIN_ATTEMPTS = _int("MAX_LOGIN_ATTEMPTS", 5)
        self.LOCKOUT_MINUTES = _int("LOCKOUT_MINUTES", 15)
        self.MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 200)
        self.SECRET_KEY = env("SECRET_KEY") or ""

        self.DEFAULT_LANG = env("DEFAULT_LANG") or "ar"
        self.APP_NAME = env("APP_NAME") or ""

        for key, value in overrides.items():
            setattr(self, key, value)
        # Embeddings default to the chat server unless configured separately.
        if not env("EMBEDDING_BASE_URL") and "EMBEDDING_BASE_URL" not in overrides:
            self.EMBEDDING_BASE_URL = self.LLM_BASE_URL.rstrip("/")
        if not env("EMBEDDING_API_KEY") and "EMBEDDING_API_KEY" not in overrides:
            self.EMBEDDING_API_KEY = self.LLM_API_KEY

        if isinstance(self.DATA_DIR, str):
            self.DATA_DIR = Path(self.DATA_DIR)
            self.FILES_DIR = self.DATA_DIR / "files"
            self.DB_PATH = self.DATA_DIR / "app.db"

    def ensure_dirs(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.FILES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.DATA_DIR, 0o700)
        except OSError:
            pass

    def load_secret_key(self):
        """Use SECRET_KEY from env, otherwise a random key persisted in DATA_DIR."""
        if self.SECRET_KEY:
            return self.SECRET_KEY
        key_file = self.DATA_DIR / "secret_key"
        if key_file.is_file():
            return key_file.read_text().strip()
        key = secrets.token_hex(32)
        key_file.write_text(key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return key
