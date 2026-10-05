"""Flask application: pages, JSON API and security plumbing."""
import hmac
import json
import logging
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import requests
from flask import (Flask, Response, abort, g, jsonify, redirect, render_template, request,
                   send_file, session, stream_with_context, url_for)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth
from .config import Config
from .db import Database, now_iso
from .i18n import DIRECTION, LANGS, js_strings, translate
from .indexer import Indexer
from .ingest import SUPPORTED_EXTENSIONS
from .llm import LLMClient
from .rag import Answerer, Summarizer
from .search import Retriever

log = logging.getLogger(__name__)

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'")


def create_app(cfg=None, start_worker=True):
    cfg = cfg or Config()
    cfg.ensure_dirs()

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.load_secret_key(),
        SESSION_COOKIE_NAME="bankrag_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=cfg.SESSION_HOURS),
        MAX_CONTENT_LENGTH=(cfg.MAX_UPLOAD_MB + 1) * 1024 * 1024,
        JSON_AS_ASCII=False,
    )
    if os.environ.get("TRUST_PROXY", "").lower() in ("1", "true", "yes"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db = Database(cfg.DB_PATH)
    db.init_schema()
    llm = LLMClient(cfg)
    retriever = Retriever(db, llm)
    indexer = Indexer(cfg, db, llm, retriever)
    answerer = Answerer(cfg, llm, retriever)
    summarizer = Summarizer(cfg, db, llm)
    throttle = auth.LoginThrottle(cfg.MAX_LOGIN_ATTEMPTS, cfg.LOCKOUT_MINUTES)
    app.extensions["bankrag"] = SimpleNamespace(
        cfg=cfg, db=db, llm=llm, retriever=retriever, indexer=indexer, answerer=answerer,
        summarizer=summarizer)

    def user_count():
        return db.query_one("SELECT COUNT(*) AS n FROM users")["n"]

    def t(key, **kwargs):
        return translate(key, g.get("lang", cfg.DEFAULT_LANG), **kwargs)

    def audit(action, detail=""):
        user = g.get("user")
        db.audit(user["username"] if user else "", action, detail, auth.client_ip())

    def api_error(code, status=400, **extra):
        return jsonify(error=code, message=t(code), **extra), status

    def safe_next(target):
        if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
            return target
        return url_for("index")

    def start_session(user):
        session.clear()
        session.permanent = True
        session["uid"] = user["id"]
        session["epoch"] = user["session_epoch"]
        session["login_at"] = session["seen"] = int(time.time())
        auth.csrf_token()

    # ------------------------------------------------------------------ request hooks

    @app.before_request
    def before():
        lang = request.cookies.get("lang")
        g.lang = lang if lang in LANGS else cfg.DEFAULT_LANG
        g.user = None
        uid = session.get("uid")
        if uid:
            user = db.query_one("SELECT * FROM users WHERE id = ?", (uid,))
            now = int(time.time())
            valid = (
                user is not None and user["active"]
                and session.get("epoch") == user["session_epoch"]
                and now - session.get("seen", 0) <= cfg.IDLE_MINUTES * 60
                and now - session.get("login_at", 0) <= cfg.SESSION_HOURS * 3600
            )
            if valid:
                g.user = user
                if now - session.get("seen", 0) > 60:
                    session["seen"] = now
            else:
                session.clear()
                g.session_expired = True

        if g.get("session_expired") and request.path.startswith("/api/"):
            return jsonify(error="auth_required"), 401
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not auth.check_csrf():
            if request.path.startswith("/api/"):
                return api_error("csrf_failed", 400)
            g.csrf_error = True

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if cfg.COOKIE_SECURE:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
            if not request.path.startswith("/files/"):
                response.headers.setdefault("Content-Security-Policy", CSP)
        return response

    @app.context_processor
    def template_globals():
        lang = g.get("lang", cfg.DEFAULT_LANG)
        return {
            "t": t,
            "lang": lang,
            "other_lang": "fr" if lang == "ar" else "ar",
            "dir": DIRECTION[lang],
            "user": g.get("user"),
            "csrf_token": auth.csrf_token,
            "app_name": cfg.APP_NAME or translate("app_name", lang),
            "js_strings": js_strings(lang),
            "max_upload_mb": cfg.MAX_UPLOAD_MB,
            "asset_version": ASSET_VERSION,
        }

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return api_error("file_too_large", 413)

    @app.errorhandler(403)
    def forbidden(_exc):
        if request.path.startswith("/api/"):
            return api_error("forbidden", 403)
        return render_template("error.html", message=t("forbidden")), 403

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify(error="not_found"), 404
        return render_template("error.html", message="404"), 404

    # ------------------------------------------------------------------ pages

    @app.get("/")
    @auth.login_required
    def index():
        return render_template("chat.html")

    @app.get("/admin")
    @auth.admin_required
    def admin():
        return render_template("admin.html")

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if user_count() > 0:
            return redirect(url_for("login"))
        error = None
        form = {}
        if request.method == "POST":
            form = request.form
            username = form.get("username", "").strip()
            if g.get("csrf_error"):
                error = "csrf_failed"
            elif not hmac.compare_digest(form.get("code", "").strip().upper(), auth.setup_code()):
                error = "setup_bad_code"
            elif not auth.USERNAME_RE.match(username):
                error = "username_invalid"
            else:
                error = auth.validate_new_password(form.get("password", ""), form.get("confirm", ""))
            if not error:
                db.execute(
                    "INSERT INTO users (username, display_name, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, 'admin', ?)",
                    (username, form.get("display_name", "").strip()[:80] or username,
                     auth.hash_password(form["password"]), now_iso()),
                )
                db.audit(username, "setup", "", auth.client_ip())
                app.config.pop("SETUP_CODE", None)
                return redirect(url_for("login"))
        return render_template("setup.html", error=error, form=form)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if user_count() == 0:
            return redirect(url_for("setup"))
        if g.user is not None:
            return redirect(url_for("index"))
        error = None
        error_args = {}
        username = ""
        if request.method == "POST":
            username = request.form.get("username", "").strip()[:64]
            password = request.form.get("password", "")
            ip = auth.client_ip()
            wait = throttle.locked_for(username, ip)
            if g.get("csrf_error"):
                error = "csrf_failed"
            elif wait:
                error, error_args = "login_locked", {"minutes": max(1, wait // 60)}
            else:
                user = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
                ok = auth.verify_password(user["password_hash"] if user else None, password)
                if ok and user["active"]:
                    throttle.reset(username, ip)
                    start_session(user)
                    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now_iso(), user["id"]))
                    db.audit(user["username"], "login_ok", "", ip)
                    return redirect(safe_next(request.args.get("next")))
                throttle.fail(username, ip)
                db.audit(username, "login_fail", "", ip)
                error = "login_failed"
        elif g.get("session_expired"):
            error = "session_expired"
        return render_template("login.html", error=error, error_args=error_args, username=username)

    @app.post("/logout")
    def logout():
        if g.user is not None and not g.get("csrf_error"):
            audit("logout")
            session.clear()
        return redirect(url_for("login"))

    @app.get("/lang/<code>")
    def set_lang(code):
        response = redirect(safe_next(request.args.get("next")))
        if code in LANGS:
            response.set_cookie("lang", code, max_age=365 * 86400, samesite="Lax",
                                secure=cfg.COOKIE_SECURE, httponly=True)
        return response

    @app.get("/offline")
    def offline():
        return render_template("offline.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        lang = g.lang
        data = {
            "name": cfg.APP_NAME or translate("app_name", lang),
            "short_name": translate("app_short", lang),
            "description": translate("app_tagline", lang),
            "lang": lang,
            "dir": DIRECTION[lang],
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#f4f6f8",
            "theme_color": "#0f4c5c",
            "icons": [
                {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
                 "purpose": "maskable"},
            ],
        }
        return Response(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        response = send_file(Path(app.static_folder) / "js" / "sw.js", mimetype="text/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/files/<doc_id>")
    @auth.login_required
    def view_file(doc_id):
        doc = db.query_one("SELECT * FROM documents WHERE id = ?", (doc_id,))
        if doc is None:
            abort(404)
        path = cfg.FILES_DIR / f"{doc['id']}{doc['ext']}"
        if not path.is_file():
            abort(404)
        audit("view_file", doc["title"])
        return send_file(
            path,
            mimetype=MIME_TYPES.get(doc["ext"], "application/octet-stream"),
            as_attachment=doc["ext"] in (".docx", ".xlsx"),
            download_name=doc["filename"],
            max_age=0,
        )

    # ------------------------------------------------------------------ API: documents

    def doc_json(row, full):
        data = {k: row[k] for k in ("id", "title", "category", "ext", "pages", "status")}
        if full:
            data.update({k: row[k] for k in ("filename", "size", "chunks", "embedded", "error",
                                             "warning", "uploaded_by", "created_at")})
        return data

    @app.get("/api/documents")
    @auth.login_required
    def list_documents():
        full = g.user["role"] == "admin"
        sql = "SELECT * FROM documents" + ("" if full else " WHERE status = 'ready'")
        rows = db.query(sql + " ORDER BY category COLLATE NOCASE, title COLLATE NOCASE")
        return jsonify(documents=[doc_json(r, full) for r in rows])

    @app.post("/api/documents")
    @auth.admin_required
    def upload_documents():
        category = request.form.get("category", "").strip()[:80]
        created, rejected = [], []
        for upload in request.files.getlist("files"):
            name = os.path.basename((upload.filename or "").replace("\\", "/")).strip()[:200]
            ext = os.path.splitext(name)[1].lower()
            if not name or ext not in SUPPORTED_EXTENSIONS:
                rejected.append({"name": name, "reason": t("err_unsupported_type")})
                continue
            doc_id = uuid.uuid4().hex
            path = cfg.FILES_DIR / f"{doc_id}{ext}"
            upload.save(path)
            size = path.stat().st_size
            if size > cfg.MAX_UPLOAD_MB * 1024 * 1024:
                path.unlink()
                rejected.append({"name": name, "reason": t("file_too_large")})
                continue
            title = os.path.splitext(name)[0].replace("_", " ").strip() or name
            db.execute(
                "INSERT INTO documents (id, title, filename, ext, size, category, status, uploaded_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (doc_id, title, name, ext, size, category, g.user["username"], now_iso()),
            )
            indexer.enqueue(doc_id)
            audit("upload", name)
            created.append(doc_json(db.query_one("SELECT * FROM documents WHERE id = ?", (doc_id,)), True))
        return jsonify(documents=created, rejected=rejected)

    @app.patch("/api/documents/<doc_id>")
    @auth.admin_required
    def update_document(doc_id):
        doc = db.query_one("SELECT * FROM documents WHERE id = ?", (doc_id,))
        if doc is None:
            abort(404)
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", doc["title"])).strip()[:200] or doc["title"]
        category = str(data.get("category", doc["category"])).strip()[:80]
        db.execute("UPDATE documents SET title = ?, category = ? WHERE id = ?", (title, category, doc_id))
        audit("update_doc", title)
        return jsonify(document=doc_json(db.query_one("SELECT * FROM documents WHERE id = ?", (doc_id,)), True))

    @app.delete("/api/documents/<doc_id>")
    @auth.admin_required
    def delete_document(doc_id):
        doc = db.query_one("SELECT title FROM documents WHERE id = ?", (doc_id,))
        if doc is None or not indexer.delete_document(doc_id):
            abort(404)
        audit("delete_doc", doc["title"])
        return jsonify(ok=True)

    @app.post("/api/documents/<doc_id>/reindex")
    @auth.admin_required
    def reindex_document(doc_id):
        doc = db.query_one("SELECT title FROM documents WHERE id = ?", (doc_id,))
        if doc is None:
            abort(404)
        db.execute("UPDATE documents SET status = 'pending', error = '', attempts = 0 WHERE id = ?", (doc_id,))
        indexer.enqueue(doc_id)
        audit("reindex", doc["title"])
        return jsonify(ok=True)

    # ------------------------------------------------------------------ API: ask

    @app.post("/api/ask")
    @auth.login_required
    def ask():
        data = request.get_json(silent=True) or {}
        question = str(data.get("question", "")).strip()[:2000]
        if not question:
            return api_error("error_generic")
        history = []
        for item in (data.get("history") or [])[-cfg.HISTORY_TURNS * 2:]:
            if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
                history.append({"role": item["role"], "content": str(item.get("content", ""))[:4000]})
        doc_filter = data.get("doc_ids")
        if doc_filter is not None:
            if not isinstance(doc_filter, list):
                return api_error("error_generic")
            doc_filter = [str(d) for d in doc_filter][:5000]
            if not doc_filter:
                return api_error("no_sources_selected")
        mode = "full" if data.get("mode") == "full" else "normal"
        audit("ask", ("[full] " if mode == "full" else "") + question[:500])
        return ndjson(answerer.stream(question, history, doc_filter, mode), "answer failed")

    @app.post("/api/documents/<doc_id>/summary")
    @auth.login_required
    def summarize_document(doc_id):
        doc = db.query_one("SELECT * FROM documents WHERE id = ? AND status = 'ready'", (doc_id,))
        if doc is None:
            abort(404)
        data = request.get_json(silent=True) or {}
        lang = data.get("lang") if data.get("lang") in LANGS else g.get("lang", cfg.DEFAULT_LANG)
        # a fresh summary costs model time: only administrators can replace the saved one
        refresh = bool(data.get("refresh")) and g.user["role"] == "admin"
        follow = bool(data.get("follow"))  # asking again while another request prepares it
        if not (follow and summarizer.following(doc["id"], lang)):
            audit("summary", doc["title"])  # polls for a job already audited are not logged again
        return ndjson(summarizer.stream(doc, lang, refresh, follow), "summary failed")

    def ndjson(events, what):
        def generate():
            try:
                for event in events:
                    yield json.dumps(event, ensure_ascii=False) + "\n"
            except Exception:  # noqa: BLE001
                log.exception(what)
                yield json.dumps({"type": "error", "code": "error_generic"}) + "\n"

        return Response(stream_with_context(generate()), mimetype="application/x-ndjson",
                        headers={"X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ API: users

    def user_json(row):
        return {k: row[k] for k in ("id", "username", "display_name", "role", "active",
                                    "created_at", "last_login")}

    def active_admins():
        return db.query_one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1")["n"]

    @app.get("/api/users")
    @auth.admin_required
    def list_users():
        rows = db.query("SELECT * FROM users ORDER BY role, username COLLATE NOCASE")
        return jsonify(users=[user_json(r) for r in rows], me=g.user["id"])

    @app.post("/api/users")
    @auth.admin_required
    def create_user():
        data = request.get_json(silent=True) or {}
        username = str(data.get("username", "")).strip()
        if not auth.USERNAME_RE.match(username):
            return api_error("username_invalid")
        error = auth.validate_new_password(str(data.get("password", "")))
        if error:
            return api_error(error)
        if db.query_one("SELECT 1 FROM users WHERE username = ?", (username,)):
            return api_error("username_taken")
        role = "admin" if data.get("role") == "admin" else "user"
        display = str(data.get("display_name", "")).strip()[:80] or username
        db.execute(
            "INSERT INTO users (username, display_name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, display, auth.hash_password(str(data["password"])), role, now_iso()),
        )
        audit("user_create", f"{username} ({role})")
        return jsonify(user=user_json(db.query_one("SELECT * FROM users WHERE username = ?", (username,))))

    @app.patch("/api/users/<int:user_id>")
    @auth.admin_required
    def update_user(user_id):
        target = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            abort(404)
        data = request.get_json(silent=True) or {}
        is_self = target["id"] == g.user["id"]
        fields, changes = {}, []
        bump_epoch = False
        if "display_name" in data:
            fields["display_name"] = str(data["display_name"]).strip()[:80] or target["username"]
        if "role" in data and data["role"] in ("admin", "user") and data["role"] != target["role"]:
            if is_self:
                return api_error("cannot_change_self")
            if target["role"] == "admin" and target["active"] and active_admins() <= 1:
                return api_error("cannot_remove_last_admin")
            fields["role"] = data["role"]
            changes.append(f"role={data['role']}")
        if "active" in data and bool(data["active"]) != bool(target["active"]):
            if is_self:
                return api_error("cannot_change_self")
            if not data["active"] and target["role"] == "admin" and active_admins() <= 1:
                return api_error("cannot_remove_last_admin")
            fields["active"] = 1 if data["active"] else 0
            changes.append("enabled" if data["active"] else "disabled")
            bump_epoch = True
        if data.get("password"):
            error = auth.validate_new_password(str(data["password"]))
            if error:
                return api_error(error)
            fields["password_hash"] = auth.hash_password(str(data["password"]))
            changes.append("password reset")
            bump_epoch = True
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            if bump_epoch:
                cols += ", session_epoch = session_epoch + 1"
            db.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))
            audit("user_update", f"{target['username']}: {', '.join(changes) or 'profile'}")
            if is_self and bump_epoch:
                session["epoch"] = db.query_one("SELECT session_epoch FROM users WHERE id = ?",
                                                (user_id,))["session_epoch"]
        return jsonify(user=user_json(db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))))

    @app.delete("/api/users/<int:user_id>")
    @auth.admin_required
    def delete_user(user_id):
        target = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if target is None:
            abort(404)
        if target["id"] == g.user["id"]:
            return api_error("cannot_change_self")
        if target["role"] == "admin" and target["active"] and active_admins() <= 1:
            return api_error("cannot_remove_last_admin")
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        audit("user_delete", target["username"])
        return jsonify(ok=True)

    @app.post("/api/account/password")
    @auth.login_required
    def change_own_password():
        data = request.get_json(silent=True) or {}
        if not auth.verify_password(g.user["password_hash"], str(data.get("current", ""))):
            return api_error("wrong_password")
        error = auth.validate_new_password(str(data.get("new", "")), str(data.get("confirm", "")))
        if error:
            return api_error(error)
        db.execute("UPDATE users SET password_hash = ?, session_epoch = session_epoch + 1 WHERE id = ?",
                   (auth.hash_password(str(data["new"])), g.user["id"]))
        session["epoch"] = g.user["session_epoch"] + 1
        audit("password_change")
        return jsonify(ok=True, message=t("password_changed"))

    # ------------------------------------------------------------------ API: admin info

    @app.get("/api/audit")
    @auth.admin_required
    def audit_log():
        rows = db.query("SELECT ts, username, action, detail, ip FROM audit_log ORDER BY id DESC LIMIT 500")
        return jsonify(events=[dict(r) for r in rows])

    @app.get("/api/system")
    @auth.admin_required
    def system_info():
        return jsonify(
            llm_base_url=cfg.LLM_BASE_URL,
            llm_model=cfg.LLM_MODEL,
            thinking=cfg.LLM_THINKING,
            query_rewrite=cfg.QUERY_REWRITE,
            embedding_model=cfg.EMBEDDING_MODEL,
            top_k=cfg.TOP_K,
            top_k_full=cfg.TOP_K_FULL,
            ocr_engine=indexer.ocr.name if indexer.ocr else "",
            ocr_model=cfg.OCR_VISION_MODEL if indexer.ocr and indexer.ocr.name == "vision" else "",
            documents=db.query_one("SELECT COUNT(*) AS n FROM documents WHERE status = 'ready'")["n"],
            chunks=db.query_one("SELECT COUNT(*) AS n FROM chunks")["n"],
            users=db.query_one("SELECT COUNT(*) AS n FROM users WHERE active = 1")["n"],
        )

    @app.post("/api/system/check")
    @auth.admin_required
    def system_check():
        try:
            response = requests.get(cfg.LLM_BASE_URL + "/models",
                                    headers={"Authorization": f"Bearer {cfg.LLM_API_KEY}"}, timeout=10)
            response.raise_for_status()
            models = [m.get("id") for m in response.json().get("data", [])]
        except (requests.RequestException, ValueError) as exc:
            return jsonify(ok=False, detail=str(exc)[:200])
        result = {"ok": True, "models": models}
        if llm.embeddings_enabled:
            try:
                llm.embed(["test"])
                result["embeddings_ok"] = True
            except Exception as exc:  # noqa: BLE001
                result.update(ok=False, embeddings_ok=False, detail=str(exc)[:200])
        return jsonify(result)

    if start_worker:
        indexer.start()
    if user_count() == 0:
        with app.app_context():
            code = auth.setup_code()
        banner = "=" * 64
        print(f"\n{banner}\n  First start: open /setup in the browser and enter this setup code:\n"
              f"      {code}\n{banner}\n", flush=True)
    return app


def _asset_version():
    static = Path(__file__).parent / "static"
    stamps = [p.stat().st_mtime for p in static.rglob("*") if p.is_file()]
    return str(int(max(stamps))) if stamps else "1"


ASSET_VERSION = _asset_version()
