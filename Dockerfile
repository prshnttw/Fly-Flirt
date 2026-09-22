FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production DATA_DIR=/data PORT=8080

WORKDIR /app
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY app.py ./
COPY flyflirt ./flyflirt
COPY templates ./templates
COPY static ./static

RUN useradd -m app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8080

# One worker on purpose: chat rooms live in memory. Threads give the concurrency.
CMD ["sh", "-c", "gunicorn -k gthread -w 1 --threads 100 --timeout 120 -b 0.0.0.0:${PORT} app:app"]
