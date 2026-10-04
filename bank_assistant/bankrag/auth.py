"""Authentication helpers: password hashing, login throttling, CSRF, access decorators."""
import hmac
import re
import secrets
import threading
import time
from functools import wraps

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD = 8
_DUMMY_HASH = generate_password_hash("timing-equaliser")


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_hash, password):
    if not stored_hash:
        check_password_hash(_DUMMY_HASH, password)  # same work for unknown users
        return False
    return check_password_hash(stored_hash, password)


class LoginThrottle:
    """Locks a (username, ip) pair — and an ip overall — after repeated failures."""

    def __init__(self, max_attempts, lockout_minutes):
        self.max_attempts = max_attempts
        self.window = lockout_minutes * 60
        self.failures = {}
        self.lock = threading.Lock()

    def _recent(self, key, now):
        stamps = [t for t in self.failures.get(key, []) if now - t < self.window]
        self.failures[key] = stamps
        return stamps

    def locked_for(self, username, ip):
        """Seconds until the lock expires (0 when not locked)."""
        now = time.time()
        with self.lock:
            for key, limit in (((username.lower(), ip), self.max_attempts), (("*", ip), self.max_attempts * 4)):
                stamps = self._recent(key, now)
                if len(stamps) >= limit:
                    return int(self.window - (now - stamps[-limit])) + 1
        return 0

    def fail(self, username, ip):
        now = time.time()
        with self.lock:
            for key in ((username.lower(), ip), ("*", ip)):
                self._recent(key, now).append(now)

    def reset(self, username, ip):
        with self.lock:
            self.failures.pop((username.lower(), ip), None)


def csrf_token():
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def check_csrf():
    expected = session.get("csrf")
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    return bool(expected) and hmac.compare_digest(expected, sent)


def client_ip():
    # Behind a reverse proxy set TRUST_PROXY=1 (handled by ProxyFix in the app factory).
    return request.remote_addr or ""


def _wants_json():
    return request.path.startswith("/api/")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.get("user") is None:
            if _wants_json():
                return jsonify(error="auth_required"), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if g.user["role"] != "admin":
            if _wants_json():
                return jsonify(error="forbidden"), 403
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def validate_new_password(password, confirm=None):
    if len(password or "") < MIN_PASSWORD:
        return "password_min"
    if confirm is not None and password != confirm:
        return "passwords_mismatch"
    return None


def setup_code():
    """One-time code (printed in the server console) required to create the first admin."""
    app = current_app
    code = app.config.get("SETUP_CODE")
    if not code:
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        app.config["SETUP_CODE"] = code
    return code
