# Blitz.cloud deployment preparation

This repository is packaged as a Docker web service. The container:

- listens on `0.0.0.0:${PORT:-8080}`;
- installs `ffmpeg` and `aria2` in the Linux image;
- installs the Python dependencies, including yt-dlp;
- stores SQLite state, the yt-dlp download archive and downloaded media under `VYRO_DATA_DIR` (default `/data`).

## Deployment configuration

1. Deploy the repository as a Docker application from its Git repository or supported source flow.
2. Configure the service's exposed/listening port as `8080` (or pass the platform-provided `PORT`).
3. Attach persistent storage mounted at `/data`. Without persistent storage, downloads and history may disappear when the instance is replaced or restarted.
4. Ensure the selected runtime supports the required CPU architecture (Linux AMD64 is the intended target), sufficient disk space, memory, execution time and outbound network access.
5. Test real yt-dlp extraction, FFmpeg post-processing, aria2c, YouTube PO-token generation, TikTok browser impersonation, large files and cancellation on the deployed runtime.

## Important: not yet deployed or production-approved

No deployment has been performed against the user's Blitz.cloud application. The Docker image has not been built in this execution environment because Docker is unavailable here. Plan limits, persistent-volume availability, outbound network rules and long-running job behavior must be confirmed in the actual Blitz account. The visitor interface intentionally has no login, subscription, or per-user download quotas. The host still has finite CPU, memory, disk and network capacity; configure network-level egress restrictions to protect internal services from redirected or nested extractor requests.
