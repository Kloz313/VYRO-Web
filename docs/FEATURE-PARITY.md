# VYRO Web feature-parity register

Status meanings: **Implemented (initial)** = code exists but still needs real extractor/production testing; **Partial** = a limited first version exists; **Not implemented** = not yet ported; **Web replacement required** = Android behavior must be redesigned for browsers/servers.

The complete source-derived inventory is in [`SOURCE-BASELINE.md`](SOURCE-BASELINE.md). This register deliberately does not claim feature parity prematurely.

| Original VYRO capability | Web approach | Current status |
|---|---|---|
| URL analysis / media metadata | yt-dlp in backend | Implemented (initial; real extractors untested here) |
| Extractor, title, uploader, thumbnail, duration | API metadata response | Implemented (initial) |
| Format and quality list | Browser format selector plus preferred resolution and codec sorting (AV1/VP9 or H.264) | Implemented at option-mapping level; live extractor selection remains untested |
| Audio-only and audio conversion | yt-dlp + FFmpeg | Partial; native source audio is now the default, optional conversion remains, but preferred source codec selection and full parity testing are pending |
| Playlist discovery and download | yt-dlp playlist support + browser item picker | Partial; item selection UI added for first 200 entries; live playlist tests pending |
| Playlist item ranges | `playlist_items` option | Implemented (initial; UI field added) |
| Subtitle language / automatic subtitles | yt-dlp subtitle options + available-language picker | Partial; Android default language pattern and translated-caption exclusion are aligned; live tests and all retention combinations pending |
| Subtitle embedding and conversion | FFmpeg/yt-dlp postprocessors | Partial; conversion option added, requires testing |
| Thumbnail write/embed | yt-dlp + FFmpeg | Partial; standalone thumbnails use PNG and audio metadata embedding requests JPG artwork; live tests pending |
| Metadata embedding | yt-dlp postprocessor | Partial |
| SponsorBlock | yt-dlp SponsorBlock integration | Partial; category UI not complete |
| Proxy / forced IPv4 / rate limit | Not exposed to visitors; server uses automatic defaults | Removed from visitor workflow |
| Cookie upload | Not requested from visitors; no cookie upload endpoint | Not exposed; normal use requires no visitor session |
| Cookie profiles in Room | No visitor cookie profiles | Not implemented; visitor login/cookies are not part of the web workflow |
| User-Agent selection | Automatic engine defaults | Not exposed to visitors |
| aria2c external downloader | Optional Linux aria2c worker | Implemented (initial; Docker image installs aria2; runtime test pending) |
| Concurrent fragments | yt-dlp option | Implemented (initial) |
| Download progress | Inline progress within the analyzed media card | Implemented; polls the individual job, shows progress/speed/ETA, supports cancellation and exposes final file links without a task list |
| Cancel task | Progress-hook and postprocessor-hook cancellation | Implemented (initial; cancellation is checked during download and between postprocessors; real download tests pending) |
| Resume after browser refresh | SQLite job snapshots | Implemented (initial); progress and completed-file links survive browser refresh |
| Durable queue / restart recovery | SQLite-persisted request snapshots + in-process worker recovery | Partial; jobs without visitor-supplied session secrets are persisted and requeued after process restart; no independent/distributed worker service |
| Download history | Browser native downloads list; no in-app completion/history panel | UI removed and new completion rows are no longer written; legacy SQLite table/API are retained for backward compatibility |
| Export history as JSON / URL list | API export endpoints | Implemented (initial) |
| Import backup/history | Validated import workflow | Not implemented |
| Custom output templates | Restricted output template field | Implemented with conservative filename validation; actual yt-dlp behavior still requires runtime test |
| Download archive | Local archive file in server data directory | Implemented for this single-instance deployment; not user-isolated |
| Time clips / download sections | yt-dlp `download_sections` | Implemented at option-mapping level with Android `*start-end` syntax and timestamped filenames; real extraction test pending |
| Chapter embedding/splitting | yt-dlp + FFmpeg | Chapter embedding and chapter-specific output templates implemented; real FFmpeg test pending |
| Custom title replacement | yt-dlp metadata replacement | Implemented through validated title replacement |
| Custom command templates and task logs | Authenticated isolated worker + strict limits | Not implemented; unsafe to expose as arbitrary commands on a public service |
| Option shortcuts | Web preset management | Not implemented |
| Private mode/private directory | No-retention policy and isolated storage | Not implemented |
| Debug output | Admin-only diagnostics | Not implemented |
| yt-dlp update controls | Admin-controlled image rebuild/version pin | Web replacement required |
| Languages / Arabic and English | Web i18n | Arabic baseline only; more languages not implemented |
| Theme selection | Browser local preference | Partial; dark/light toggle |
| Palette and interface customization | Web theme settings | Not implemented |
| Browser notifications | Permission-based browser notification | Not implemented |
| Android notification actions / foreground service | In-page progress and browser notifications | Web replacement required |
| SD card / Android storage picker / media library scan | Server storage and browser file download | Web replacement required |
| Android crash activity / app updater / haptics / Quick Download Activity | No direct web equivalent | Remove from web build after parity decision |
| Backup and restore | Versioned JSON export/import | Partial (history export only) |
| Docker / Blitz.cloud deployment | Linux amd64 container on configured port | Dockerfile present; actual Blitz deployment not tested |


## Functional audit update — 2026-11-17

Compared the Android source (`DownloadUtil.kt`, `Downloader.kt`, `DownloadViewModel.kt`, and `PreferenceUtil.kt`) with the web backend and its API client. The following source-level mismatches were corrected in the web working copy:

- Fixed `/api/jobs`: its persisted-row query now selects the `id` column that the response builder reads.
- Added restart recovery: request snapshots are persisted without visitor-supplied session secrets; jobs are requeued on restart and can resume partial downloads.
- Aligned several Android defaults: 5-second socket timeout, one retry, 8 concurrent fragments, `--no-mtime`, chapter embedding for normal video downloads, subtitle language pattern `en.*,.*-orig`, and the Android 200-byte title output template.
- Audio-only now keeps the source audio format unless conversion is explicitly selected; audio bitrate preference maps to Android's `abr~192`, `abr~128`, `abr~64`, and `abr~32` sorting behavior. Audio-only requests no longer inherit video chapter/subtitle embedding behavior.
- Expanded format-sort validation to accept Android's `vcodec`, `acodec`, and `abr~` sort fields. Browser codec capability now supplies a default codec preference when the user has not explicitly selected a format.
- Corrected the yt-dlp embedded-Python option mapping: FFmpeg extraction, remuxing, metadata, chapters, thumbnail conversion/embedding, subtitle conversion/embedding, SponsorBlock, metadata parsing, and chapter splitting are now configured as explicit postprocessors; time clips use the Python `download_ranges` callback; platform-specific request settings use Python-API values. A nonzero yt-dlp return code now fails the job instead of being reported as completed.
- Normalized time-based download sections to yt-dlp's `*start-end` syntax and Android-style timestamped filenames.
- Added chapter-specific output templates for split chapters, optional extractor and playlist subdirectories, separate Audio/Video output folders, audio album/date/track metadata parsing, and optional square audio-artwork cropping.
- File delivery now safely supports nested output paths.
- Aligned thumbnail conversion, audio artwork embedding, default YouTube translated-caption exclusion, and MKV remuxing when embedding subtitles in video.
- Prevented temporary pre-merge filenames from being recorded as the final downloaded file when a postprocessor reports the final path; cancellation is also checked between postprocessors.

The combined release pass also adds Android-style preferred video codec/resolution sorting, the 32 kbps audio preference, and a single inline progress/cancel/result-download area. The old task-list and in-app history sections were removed; new completions are no longer written to the legacy history table. No broader visual redesign was made.

### Remaining functional differences from Android

- Android can optionally read cookies from its built-in WebView; the web version deliberately does not ask visitors for cookies or login details.
- Android stores audio/video in device-selected directories and can use private/SD-card locations; web downloads are stored on the server and delivered through browser file downloads.
- Android's foreground service can continue work independently of the visible screen. Web jobs run in the server worker pool and are requeued after a process restart; this is not yet an independent/distributed worker service.
- Android has device-specific AV1/VP9 support checks; the web edition uses browser codec capability as a substitute, which is not identical to Android hardware codec detection.
- Playlist discovery remains capped to the first 200 entries in the browser picker.
- Real chapter-splitting/FFmpeg behavior and archive duplicate messaging still need runtime validation. The web copy now maps chapter-specific output templates, optional extractor/playlist folders, separate Audio/Video output directories, audio date/album/track metadata, and optional square artwork cropping.
- No real yt-dlp extraction or download was run in this environment: yt-dlp and Docker are unavailable here, and network access is disabled. The automated tests deliberately stub yt-dlp. The option mapping was cross-checked against yt-dlp's documented Python embedding/API conversion approach, but the live engine still needs runtime verification.

## Current core-focused milestone

The interface has been narrowed around the primary workflow: paste URL, analyze media, choose a format/quality, optionally select audio/subtitles/playlist items, start the download, and monitor the current job inline. The task-list and history panels are not shown; completed files are offered directly from the same progress area and the browser provides its own downloads history. Non-download Android settings and app-only screens are not being ported. Advanced connection and session controls have been removed from the visitor interface. The visible workflow retains media quality, audio format/quality, subtitles and playlist selection.

The playlist picker exposes up to 200 entries to avoid unbounded response/UI growth; users can still specify yt-dlp playlist ranges for larger playlists.

## Critical production blockers

1. **Open public access:** No visitor login, subscription, per-IP request quota, or per-user download quota is enabled, by design. Public use still depends on the hosting plan's finite CPU, memory, storage and network capacity.
2. **SSRF/redirect safety:** initial URL checks reject obvious private destinations, but extractor redirects and nested requests need network-level egress restrictions. URL validation alone is not a complete SSRF defense.
3. **Durable workers:** SQLite can requeue tasks after restart, but execution is still in-process. There is no independent worker service, distributed locking, or multi-instance coordination.
4. **Storage lifecycle:** persistence volume, storage retention, cleanup and download expiration must be configured for the chosen hosting plan.
5. **Visitor sessions:** visitor cookie uploads and account sessions are intentionally not part of the web workflow.
6. **Resource protection:** there are no per-IP request throttles or per-user job caps. Network egress restrictions and host-level capacity monitoring remain important to protect the public service.
7. **Platform testing:** yt-dlp/FFmpeg/aria2c must be tested on the actual Linux amd64 runtime. This execution environment has no network access and does not have yt-dlp installed; FFmpeg is present locally, but aria2c and Docker are unavailable. No real extractor/download or full media-processing test has been performed. The 26 automated tests are API/unit tests using a deliberately mocked yt-dlp engine; they validate API plumbing, option mapping, job snapshots, progress/cancellation hooks and filename handling, but do not prove live extractor behavior.
8. **Blitz.cloud:** the Dockerfile is prepared for a Linux amd64 container and `$PORT` (default 8080), but deployment, persistent volume behavior, runtime limits and plan suitability remain unverified.
9. **Legal/platform restrictions:** users must only download media they are authorized to download and must comply with each source platform's terms and applicable law.

## Release gate

Do not label this project complete or production-ready until every source feature is marked with a tested outcome, all remaining exclusions are documented, public access is protected against internal-network redirects and workers/storage are durable, and a real deployment regression suite passes for representative extractors, playlists, formats, subtitles, clips, chapter processing, cancellation and recovery.
