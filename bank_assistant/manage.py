#!/usr/bin/env python3
"""Command line tools.

  python manage.py run [--host 0.0.0.0] [--port 8080]   start the server
  python manage.py create-admin                         create an administrator account
  python manage.py reset-password USERNAME              set a new password for a user
  python manage.py reindex                              re-process every document (e.g. after enabling embeddings)
"""
import argparse
import getpass
import logging
import sys

from bankrag.auth import USERNAME_RE, hash_password, validate_new_password
from bankrag.config import Config
from bankrag.db import Database, now_iso


def ask_password():
    while True:
        password = getpass.getpass("Password (min 8 chars): ")
        error = validate_new_password(password, getpass.getpass("Confirm: "))
        if not error:
            return password
        print("  -> passwords must match and be at least 8 characters.")


def cmd_run(args):
    from waitress import serve

    from bankrag import create_app

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app()
    print(f"Serving on http://{args.host}:{args.port}  (Ctrl+C to stop)", flush=True)
    serve(app, host=args.host, port=args.port, threads=args.threads, channel_timeout=600,
          max_request_body_size=app.config["MAX_CONTENT_LENGTH"])


def cmd_create_admin(_args):
    cfg = Config()
    cfg.ensure_dirs()
    db = Database(cfg.DB_PATH)
    db.init_schema()
    username = input("Username: ").strip()
    if not USERNAME_RE.match(username):
        sys.exit("Invalid username (3-32 latin letters, digits, . _ -)")
    if db.query_one("SELECT 1 FROM users WHERE username = ?", (username,)):
        sys.exit("This username already exists.")
    display = input("Full name: ").strip() or username
    db.execute(
        "INSERT INTO users (username, display_name, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
        (username, display, hash_password(ask_password()), now_iso()),
    )
    print(f"Administrator '{username}' created.")


def cmd_reset_password(args):
    cfg = Config()
    db = Database(cfg.DB_PATH)
    db.init_schema()
    if not db.query_one("SELECT 1 FROM users WHERE username = ?", (args.username,)):
        sys.exit("No such user.")
    db.execute(
        "UPDATE users SET password_hash = ?, active = 1, session_epoch = session_epoch + 1 WHERE username = ?",
        (hash_password(ask_password()), args.username),
    )
    print("Password updated.")


def cmd_reindex(_args):
    from bankrag import create_app

    app = create_app(start_worker=False)
    ctx = app.extensions["bankrag"]
    rows = ctx.db.query("SELECT id, title FROM documents ORDER BY created_at")
    for i, row in enumerate(rows, start=1):
        print(f"[{i}/{len(rows)}] {row['title']}", flush=True)
        ctx.indexer.process(row["id"])
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--host", default="0.0.0.0")
    run.add_argument("--port", type=int, default=8080)
    run.add_argument("--threads", type=int, default=16)
    run.set_defaults(func=cmd_run)
    sub.add_parser("create-admin").set_defaults(func=cmd_create_admin)
    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    reset.set_defaults(func=cmd_reset_password)
    sub.add_parser("reindex").set_defaults(func=cmd_reindex)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
