FROM node:22-bookworm-slim AS pot-provider-build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git python3 make g++ pkg-config libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/bgutil-ytdlp-pot-provider
RUN git clone --depth 1 --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git .
WORKDIR /opt/bgutil-ytdlp-pot-provider/server
RUN npm ci && npx tsc

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080 VYRO_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg aria2 ca-certificates curl libstdc++6 libatomic1 libcairo2 libpango-1.0-0 libjpeg62-turbo libgif7 librsvg2-2 && rm -rf /var/lib/apt/lists/*
COPY --from=pot-provider-build /usr/local/bin/node /usr/local/bin/node
COPY --from=pot-provider-build /opt/bgutil-ytdlp-pot-provider/server/build /opt/bgutil/build
COPY --from=pot-provider-build /opt/bgutil-ytdlp-pot-provider/server/node_modules /opt/bgutil/node_modules
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
RUN mkdir -p /data/downloads && chown -R 1000:1000 /app /data /opt/bgutil
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:${PORT:-8080}/api/health >/dev/null || exit 1
CMD ["sh", "-c", "node /opt/bgutil/build/main.js --host 127.0.0.1 & exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
