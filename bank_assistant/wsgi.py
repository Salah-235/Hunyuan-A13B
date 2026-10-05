"""WSGI entry point (e.g. `gunicorn -w 1 --threads 16 wsgi:app`)."""
from bankrag import create_app

app = create_app()
