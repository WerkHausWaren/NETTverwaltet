# Vereinsverwaltung – Produktions-Image (Gunicorn)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VV_DATA_DIR=/data

WORKDIR /app

# Abhängigkeiten zuerst (bessere Build-Cache-Nutzung)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App-Code
COPY . .

# Nicht als root laufen; Datenverzeichnis anlegen
RUN useradd -m appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000

# 1 Worker + Threads: teilt In-Memory-Zustand (z. B. Bank-TAN-Ablauf) und
# reicht für einen Verein locker.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", \
     "--timeout", "180", "--error-logfile", "-", "wsgi:app"]
