FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 VYRO_DATA_DIR=/data VYRO_REQUIRE_AUTH=1 VYRO_API_RATE_PER_MINUTE=30 VYRO_MAX_ACTIVE_JOBS=2
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg aria2 ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
RUN mkdir -p /data/downloads && chown -R 1000:1000 /app /data
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:${PORT:-8080}/api/health >/dev/null || exit 1
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
