# deckora-extract — zero-LLM OM extraction + photo de-layering service.
# Host-agnostic: runs identically on Cloud Run, Cloudflare Containers, Fly.io,
# or any Docker host. Listens on $PORT (default 8080).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

# opencv-python-headless wheels still link libglib at import time on slim images.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-service.txt

COPY src ./src
COPY app ./app

EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
