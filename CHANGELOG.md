# VYRO Web development changelog

## Core download milestone

- Focused the browser workflow on URL analysis, quality/format selection, video/audio download and task progress.
- Added separate video and audio stream selectors, including selection of multiple audio streams for muxing.
- Added playlist item selection (up to 200 visible entries) and preserved manual yt-dlp playlist range input for larger lists.
- Returned available subtitle and automatic-caption language/format metadata; added language selection in the interface.
- Added User-Agent, optional aria2c, and persistent yt-dlp download-archive options.
- Added FFmpeg and aria2 installation to the Docker image.
- Added persisted-job detail lookup after a browser refresh/restart.
- Added deployment preparation notes and refreshed the feature parity register.
- Automated tests: 7 passing (using a mocked yt-dlp engine; not a real extraction/download test).

## Limitations

- Not yet feature-complete, deployed, or approved for public production.
- Real extraction and download tests were not possible in this environment because yt-dlp, aria2c and Docker are unavailable and outbound package/network access is disabled.


## 0.2.0 — deployment hardening and download parity improvements
- Added shared HTTP Basic authentication for Docker deployments, request throttling and concurrent-job limits.
- Added custom title replacement, separate thumbnail writing, multi-audio-stream merging, configurable SponsorBlock categories and format sorting fields.
- Added audio extraction quality selection and honored a selected audio format when extracting audio.
- Fixed full-playlist behavior when the playlist picker truncates its visible list to 200 entries.
- Added stale temporary cookie-file cleanup and Docker healthcheck.
- Automated tests: 9 passing (API/unit tests with mocked yt-dlp only). Real extractor/download and Blitz deployment tests remain blocked in this offline environment.


## 2026-11-17 — consolidated functional release candidate
- Added Android-style video codec preference (AV1/VP9 quality preference or H.264 compatibility) and resolution targets from 2160p through 360p. Explicit format-sort input takes precedence over automatic preferences.
- Added the Android ultra-low audio bitrate preference (`abr~32`) alongside 192/128/64 kbps.
- Replaced the persistent task/history panels with one inline progress area in the analyzed-media card, including status, percent, transferred bytes, speed, ETA, cancellation and final file links.
- Stopped writing new completed-download rows to the legacy in-app history table; the browser's native downloads list remains the user's download history. Existing history APIs/table are retained for compatibility and are not shown in the UI.
- Expanded automated coverage to 28 tests. Tests use a mocked yt-dlp engine; live platform extraction, FFmpeg processing and aria2c execution remain unverified.


## 2026-11-17 — Seal visual identity adaptation

- Reworked the web interface around the extracted Seal light-mode palette and Neumorphic surface/button language.
- Refined the page hierarchy, responsive spacing, control alignment, card surfaces, inset fields, raised buttons, and pressed states.
- Rewrote Arabic interface labels and helper text for clearer, more consistent terminology.
- Kept the download workflow and element IDs intact; removed the visible theme switch to respect Seal's confirmed Light interface style.
- See `docs/SEAL-VISUAL-ADAPTATION.md` for the source-derived facts and the implementation choices that remain adaptations rather than verified original role mappings.


## 2026-11-18 — direct Android source parity pass

- Reopened the supplied `VYRO-main.zip` and compared the existing web package against `FormatPage.kt`, `VideoInfo.kt`, `DownloadUtil.kt`, `Downloader.kt`, `PreferenceUtil.kt`, and the engine initialization/update paths.
- Extended extraction metadata with requested formats/downloads, chapters, additional media metadata and the fields required for the suggested format.
- Added the suggested format and separated muxed video formats from video-only formats in the visitor selector.
- Added user-facing time clipping, chapter splitting and output-title replacement, mapped to the existing yt-dlp/FFmpeg backend options.
- Changed the visible audio selector to one stream by default, matching Android's default multi-audio preference (off); no Android settings screen was added.
- Kept task history, app settings, cookie profiles, proxy/IPv4/rate-limit controls and Android-only storage/UI behavior out of the visitor interface.
- Added a detailed Arabic source-to-web comparison and a note about the VYRO naming restriction present in the supplied Android README.
- Automated tests: 40 passed. Tests use a mocked yt-dlp engine; real platform extraction, FFmpeg processing, Docker build and hosting deployment remain unverified.

## 2026-11-18 — source-parity UI cache refresh
- Bumped the service-worker cache to `vyro-web-v4` so installed PWAs do not keep serving the previous `app.js`/`styles.css` after the new source-parity controls are deployed.
- Final offline validation after the worker-pool configuration: 39 automated tests passed; Python, JavaScript and HTML syntax checks passed.

## 2026-11-18 — Android engine-behavior alignment
- Removed the forced YouTube `player_client` list from the shared web extractor options. The supplied Android download log selected `visionos` automatically; the web override had restricted extraction to `mweb` and `web_safari`.
- Kept Node.js as the configured JavaScript runtime and retained BGUtil/PO Token support; this change does not disable either component.
- Expanded safe diagnostics to record the selected YouTube player client and TikTok's native-Python JS-challenge flow without retaining raw URLs, cookies, or tokens.
- Added clearer error classification for JavaScript challenge/runtime failures.
- Validation: 43 automated tests passed; Python compilation and JavaScript syntax checks passed. No live platform extraction, Docker build, or Railway deployment was performed.


## 2026-11-18 — Android yt-dlp nightly version alignment
- Updated the web dependency from the open-ended `2026.10.26` minimum to the exact Android-reported nightly build `2026.11.17.232707` (PyPI prerelease identifier `2026.11.17.232707.dev0`).
- This nightly contains YouTube extractor fixes for web_embedded client policies and post-live stream handling.
- A clean package installation, Docker image build, Railway deployment and live platform downloads are still pending; mocked tests do not prove extractor success.
