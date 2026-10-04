FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bankrag ./bankrag
COPY manage.py wsgi.py ./
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "manage.py", "run", "--host", "0.0.0.0", "--port", "8080"]
