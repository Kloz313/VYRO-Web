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
