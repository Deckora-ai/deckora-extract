# deckora-extract — zero-LLM OM extraction + photo de-layering service.
# Host-agnostic: runs identically on Cloud Run, Cloudflare Containers, Fly.io,
# or any Docker host. Listens on $PORT (default 8080).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

# libglib: opencv-python-headless wheels still link it at import time on slim
# images. tesseract-ocr: enables the OCR fallback so scanned/thin-text OMs get
# field extraction (capped at MEDIUM confidence by design) instead of all
# routing to pending_review.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-service.txt \
        "pytesseract>=0.3.10" "pillow>=10.0"

COPY src ./src
COPY app ./app

EXPOSE 8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
