# VYRO Android source baseline

This audit is based on the user-supplied `VYRO-main.zip` archive (root directory `VYRO-main/`, archive commit marker `4906223eef1451ea7216ee5770d2da5e20cbca35`). The archive was extracted into a separate temporary audit directory. The uploaded archive and Android project were not edited.

## Source architecture observed

- Android application ID / Kotlin namespace: `com.vyro.downloader`.
- Android UI: Kotlin + Jetpack Compose.
- Main application modules: `app/` and `color/`.
- Download engine integration: `com.yausername.youtubedl_android` (yt-dlp wrapper).
- Media post-processing: FFmpeg through the Android wrapper.
- External downloader option: aria2c.
- Persistence: Room database, with entities `DownloadedVideoInfo`, `CommandTemplate`, `CookieProfile`, and `OptionShortcut`.
- Download orchestration: `Downloader.kt`, `DownloadService.kt`, and `util/DownloadUtil.kt`.
- User-facing settings are split into general, format, subtitle, directory, interaction, appearance, network/cookies, and custom-command screens.

## Source feature inventory (must be evaluated for web parity)

### Media discovery and format selection
- URL/video information extraction, extractor identity, title, uploader/channel, thumbnail, duration, metadata and format list.
- Playlist/multi-item discovery and playlist item selection.
- Audio-only extraction and selectable audio conversion format/quality.
- Video format, resolution and format-sort preferences.
- Audio/video stream merge options, including MKV preference and audio multistream handling.
- AV1 hardware capability check is device-specific; the web server must not pretend to have the visitor's hardware decoder capability.

### Download processing
- Video and audio download paths, output naming templates, output folders and extractor/playlist subdirectories.
- Download archive (avoid downloading the same extractor/id repeatedly).
- Concurrent fragments, aria2c external downloader, rate limit, proxy, forced IPv4, cookies and optional User-Agent.
- Restrict filenames and timestamp behavior.
- Thumbnail creation/conversion/cropping, metadata embedding, subtitle embedding/retention, chapter embedding and chapter splitting.
- Subtitle language selection, automatic subtitles, translated subtitles and subtitle conversion (ASS/SRT/VTT/LRC).
- SponsorBlock category selection and removal.
- User-selected time clips via download sections; custom title replacement.
- Playlist processing, including playlist-specific output naming.

### Power-user commands and state
- Custom yt-dlp config/command templates; execution output, progress, restart and cancellation.
- Cookie profiles stored in Room, cookie enable/disable and cookie import/export behavior.
- Option shortcuts.
- Download history, duplicate-by-path handling, history deletion, URL-list export and JSON backup/import for templates/shortcuts/history.
- Private mode and private output directory.
- Debug/verbose output and yt-dlp update controls.

### Interface and app behavior
- Arabic and other translated UI languages.
- Light/dark theme, palette/style customization, interface/interaction preferences and format-selection UI.
- In-app WebView helper page.
- App update screen, crash-report activity, Android notifications/actions, foreground download service, Quick Download Activity, clipboard and haptic feedback.
- Android-specific directory picker, SD-card URI access, media-library scanning and Android permissions.

## Web migration rules

- Preserve the user-visible outcome of a feature when feasible, even if its implementation changes.
- Replace Android-only storage with server-side storage plus browser downloads; expose storage quotas and retention policies.
- Replace Room with a durable database and explicit user/account ownership.
- Replace Android notifications with in-page progress and optional browser notifications.
- Replace Android cookie access with user-controlled Netscape cookie-file upload and encrypted, user-scoped cookie profiles. Never attempt to read a visitor's browser cookie jar.
- Custom yt-dlp config templates are powerful and can execute arbitrary extractor options. They must be restricted to authenticated users and isolated worker processes before being exposed on a public multi-user site.
- Private mode must be implemented as a clear no-history/no-retention policy, not merely a hidden UI toggle.
- Do not emulate Android's AV1 hardware check, media-library scan, haptics, notification actions, SD-card URI or app updater in a web environment.

## Scope note

This inventory is a source-derived checklist, not proof that the current web branch implements each feature. The authoritative implementation status is tracked in `FEATURE-PARITY.md`.
