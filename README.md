# VYRO Web

An independent web adaptation of the user-supplied VYRO Android source. The Android archive is kept unchanged; web development is isolated in this repository.

## Current milestone

This is an engineering prototype, **not yet a feature-complete port and not ready for public production**. The scope is deliberately focused on VYRO's core download workflow: paste a URL, analyze it, select quality/format (including preferred resolution and codec), download video or audio, choose playlist entries and subtitle languages, and monitor/cancel the current job inline. Completed files are delivered through links in that same area; there is no in-app task list or completion-history panel. The visitor-facing interface contains only the core media, quality, audio and subtitle choices. Advanced transport and session settings are not exposed to visitors. The service does not require accounts, passwords, subscriptions, cookies or per-user download quotas. Android-only settings and screens unrelated to downloading are intentionally excluded.

The release validation record is [`docs/RELEASE-VALIDATION.md`](docs/RELEASE-VALIDATION.md). The source-derived feature inventory is [`docs/SOURCE-BASELINE.md`](docs/SOURCE-BASELINE.md). The implementation status and release blockers are tracked in [`docs/FEATURE-PARITY.md`](docs/FEATURE-PARITY.md). The current automated tests use a mocked yt-dlp engine and therefore do not prove real platform extraction or downloads. New completions are not added to an in-app history; use the browser downloads list.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
VYRO_DATA_DIR=./data uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080`.

## Docker

```bash
docker build --platform linux/amd64 -t vyro-web:dev .
docker run --rm -p 8080:8080 -v "$PWD/data:/data" vyro-web:dev
```

The container exposes `$PORT` (8080 by default) and stores downloads/database under `/data`. The public web interface does not require visitor accounts or passwords. Use HTTPS at the hosting proxy. On Blitz.cloud, deploy the repository using its Docker/GitHub app flow, set the service port to 8080, and configure persistent storage for `/data` if supported by the selected plan. Actual Blitz deployment and resource suitability have not yet been tested. Download jobs are queued by the server worker pool; there is no per-IP request quota or per-user download quota. The number of jobs the server can process at once is still limited by the CPU, memory, disk and network capacity of the hosting plan.

## Important safety and deployment warning

The intended deployment is an open public downloader with no visitor accounts or per-user quotas. Before deploying publicly, confirm persistent storage, network egress restrictions against SSRF through redirects, and the hosting plan capacity. Media extraction and FFmpeg run on the server, so its finite resources must be monitored.

The backend checks submitted URLs against obvious private/local destinations. This is not a substitute for network-level egress restrictions, especially where extractor redirects or nested requests occur.

Only download content the user is authorized to access and follow source-platform terms and applicable law.

## License

The original VYRO Android source is distributed under the license included in [`LICENSE`](LICENSE). This web adaptation is derived from that source; preserve applicable license and attribution notices when distributing the derivative work.
