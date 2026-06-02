FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DB_PATH=/data/calencraft.db
ENV APP_VERSION=1
ENV FLASK_ENV=production
ENV FLASK_APP=run.py
# SECRET_KEY must be set at runtime — container will refuse to start without it.
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 4 workers — tune WEB_CONCURRENCY env var to match container CPU count
CMD ["gunicorn", "run:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info"]
