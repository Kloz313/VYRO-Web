# VYRO Web

An independent web adaptation of the user-supplied VYRO Android source. The Android archive is kept unchanged; web development is isolated in this repository.

## Current milestone

This is an engineering prototype, **not yet a feature-complete port and not ready for public production**. The scope is deliberately focused on VYRO's core download workflow: paste a URL, analyze it, select quality/format (including preferred resolution and codec), download video or audio, choose playlist entries and subtitle languages, and monitor/cancel the current job inline. Completed files are delivered through links in that same area; there is no in-app task list or completion-history panel. Download-related options include Cookies, Proxy, User-Agent, rate limiting, IPv4, aria2c, download archive, time ranges, subtitle conversion/embedding, chapters and metadata/thumbnail processing. Android-only settings and screens unrelated to downloading are intentionally excluded.

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

The container exposes `$PORT` (8080 by default) and stores downloads/database under `/data`. Docker mode requires `VYRO_ACCESS_USERNAME` and `VYRO_ACCESS_PASSWORD` because `VYRO_REQUIRE_AUTH=1` is enabled by default. Set both as deployment secrets; use HTTPS at the hosting proxy. On Blitz.cloud, deploy the repository using its Docker/GitHub app flow, set the service port to 8080, and configure persistent storage for `/data` if supported by the selected plan. Actual Blitz deployment and resource suitability have not yet been tested. The current authentication is a single shared Basic-Auth gate, not individual user accounts or per-user download isolation.

## Important safety and deployment warning

Do not expose this prototype publicly yet. It currently has only a shared Basic-Auth gate in Docker mode and basic request/concurrency limits; it lacks individual user isolation, durable queue recovery, storage quotas/retention, complete SSRF/egress protections and encrypted reusable cookie profiles. It executes media extraction and FFmpeg work on the server, so public exposure without these controls can cause abuse, data leakage and resource exhaustion.

The backend checks user-supplied URLs against obvious private/local destinations, and rate-limits extraction/download requests plus concurrent jobs. These are baseline safeguards only, not a substitute for network-level egress restrictions, especially where extractor redirects or nested requests occur.

Only download content the user is authorized to access and follow source-platform terms and applicable law.

## License

The original VYRO Android source is distributed under the license included in [`LICENSE`](LICENSE). This web adaptation is derived from that source; preserve applicable license and attribution notices when distributing the derivative work.
