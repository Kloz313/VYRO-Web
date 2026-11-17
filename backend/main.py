from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import re
import socket
import threading
from contextlib import contextmanager
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import yt_dlp

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DATA_DIR = Path(os.getenv("VYRO_DATA_DIR", ROOT / "data"))
DOWNLOAD_DIR = DATA_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
(DOWNLOAD_DIR / "Audio").mkdir(parents=True, exist_ok=True)
(DOWNLOAD_DIR / "Video").mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "vyro-web.sqlite3"

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db() -> None:
    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL, updated_at REAL NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS download_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, title TEXT, source_url TEXT,
            extractor TEXT, filename TEXT NOT NULL, completed_at REAL NOT NULL
        )""")
        # Recover queued and interrupted work from its persisted request snapshot.
        active_rows = conn.execute(
            "SELECT id, data FROM jobs WHERE status IN ('queued','downloading','processing','cancelling')"
        ).fetchall()
        now = time.time()
        for row in active_rows:
            try:
                snapshot = json.loads(row["data"])
            except (TypeError, json.JSONDecodeError):
                snapshot = {"id": row["id"]}
            can_recover = bool(snapshot.get("restart_recoverable") and isinstance(snapshot.get("request"), dict))
            if can_recover:
                snapshot.update({
                    "status": "queued", "percent": 0, "downloaded_bytes": 0,
                    "total_bytes": 0, "speed": None, "eta": None,
                    "error": None, "resumed_after_restart": True,
                })
                new_status = "queued"
            else:
                snapshot.update({
                    "status": "interrupted",
                    "error": "توقف التنزيل بعد إعادة تشغيل الخادم وتعذرت استعادة المهمة تلقائياً.",
                    "interrupted_at": now,
                })
                new_status = "interrupted"
            conn.execute(
                "UPDATE jobs SET status=?, data=?, updated_at=? WHERE id=?",
                (new_status, json.dumps(snapshot, ensure_ascii=False), now, row["id"]),
            )

init_db()
app = FastAPI(title="VYRO Web API", version="0.3.0", description="Web edition of VYRO Downloader")

# Public service: no account gate, per-IP request quota, or per-user download quota.
# Requests enter the worker queue; throughput is bounded only by host resources.
executor = ThreadPoolExecutor()
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.RLock()
cancel_events: dict[str, threading.Event] = {}


class ExtractRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    include_playlist: bool = False


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    format_id: str | None = None
    audio_only: bool = False
    # Android extracts the source audio by default and converts only when the
    # user explicitly enables audio conversion. None therefore means keep the
    # extractor's native audio container.
    audio_format: str | None = None
    audio_quality: str = "0"
    subtitles: bool = False
    auto_subtitles: bool = False
    auto_translated_subtitles: bool = False
    subtitle_languages: str = "en.*,.*-orig"
    embed_subtitles: bool = False
    keep_subtitle_files: bool = False
    embed_thumbnail: bool = False
    write_thumbnail: bool = False
    embed_metadata: bool = True
    sponsorblock: bool = False
    sponsorblock_categories: list[str] = Field(default_factory=lambda: ["sponsor"])
    merge_audio_streams: bool = False
    new_title: str | None = Field(default=None, max_length=240)
    playlist: bool = False
    restrict_filenames: bool = False
    concurrent_fragments: int = Field(default=8, ge=1, le=16)
    use_aria2c: bool = False
    use_download_archive: bool = False
    playlist_items: str | None = None
    remux_video: str | None = None
    # Android embeds chapters for normal video downloads unless clip ranges
    # are selected.
    embed_chapters: bool = True
    split_chapters: bool = False
    subdirectory_extractor: bool = False
    subdirectory_playlist_title: bool = False
    crop_artwork: bool = False
    # Android always passes --no-mtime.
    no_mtime: bool = True
    convert_subtitles: str | None = None
    subtitle_format: str | None = None
    download_sections: list[str] = Field(default_factory=list)
    format_sort: list[str] = Field(default_factory=list)
    output_template: str | None = None
    media_title: str | None = None
    extractor: str | None = None


def persist_job(job_id: str) -> None:
    with jobs_lock:
        item = jobs.get(job_id)
        if item is None:
            return
        snapshot = dict(item)
    with db() as conn:
        conn.execute(
            "INSERT INTO jobs(id,status,data,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data,updated_at=excluded.updated_at",
            (job_id, snapshot.get("status", "queued"), json.dumps(snapshot, ensure_ascii=False), time.time()),
        )

def validate_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="أدخل رابطاً صحيحاً يبدأ بـ http أو https")
    host = parsed.hostname.lower().rstrip(".")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="الروابط التي تحتوي بيانات دخول مضمّنة غير مسموحة")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(status_code=422, detail="هذا العنوان غير مسموح")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise HTTPException(status_code=422, detail="عناوين الشبكات الخاصة غير مسموحة")
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            addresses = {item[4][0] for item in resolved}
            if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
                raise HTTPException(status_code=422, detail="اسم المضيف يشير إلى عنوان شبكة غير مسموح")
        except socket.gaierror as exc:
            raise HTTPException(status_code=422, detail="تعذر التحقق من عنوان المضيف") from exc
    return value.strip()


def common_options(payload: BaseModel) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 5,
        "retries": 1,
        "extractor_retries": 1,
        "noplaylist": not bool(getattr(payload, "include_playlist", False) or getattr(payload, "playlist", False)),
        # Node.js + yt-dlp-ejs are installed in the container for full YouTube support.
        "js_runtimes": {"node": {}},
        # The local provider supplies PO tokens without requiring visitor cookies.
        "extractor_args": {"youtube": {"player_client": ["mweb", "tv", "web_safari"]}},
    }
    host = (urlparse(payload.url).hostname or "").lower().rstrip(".")
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        opts["impersonate"] = "chrome"
    return opts


def clean_info(info: dict[str, Any]) -> dict[str, Any]:
    formats = []
    for f in info.get("formats") or []:
        formats.append({
            "format_id": f.get("format_id"), "ext": f.get("ext"),
            "resolution": f.get("resolution") or (f"{f.get('height')}p" if f.get("height") else None),
            "width": f.get("width"), "height": f.get("height"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "vcodec": f.get("vcodec"), "acodec": f.get("acodec"),
            "fps": f.get("fps"), "format_note": f.get("format_note"),
            "format": f.get("format"),
        })
    entries = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        entries.append({"id": entry.get("id"), "title": entry.get("title"), "url": entry.get("webpage_url") or entry.get("url"), "duration": entry.get("duration"), "thumbnail": entry.get("thumbnail"), "playlist_index": entry.get("playlist_index")})
    playlist_count = info.get("playlist_count") or len(entries)
    return {
        "id": info.get("id"), "title": info.get("title"),
        "webpage_url": info.get("webpage_url"), "extractor": info.get("extractor_key") or info.get("extractor"),
        "description": info.get("description"), "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"), "uploader": info.get("uploader"),
        "channel": info.get("channel"), "view_count": info.get("view_count"),
        "upload_date": info.get("upload_date"), "formats": formats,
        "subtitles": {lang: [{"ext": item.get("ext"), "name": item.get("name")} for item in items]
                      for lang, items in (info.get("subtitles") or {}).items()},
        "automatic_captions": {lang: [{"ext": item.get("ext"), "name": item.get("name")} for item in items]
                               for lang, items in (info.get("automatic_captions") or {}).items()},
        "playlist": info.get("_type") in {"playlist", "multi_video"} or bool(info.get("entries")),
        "entries": entries[:200], "playlist_count": playlist_count,
        "entries_truncated": len(entries) > 200 or (playlist_count and playlist_count > 200),
    }


@app.get("/api/health")
def health():
    import shutil
    try:
        with socket.create_connection(("127.0.0.1", 4416), timeout=0.5):
            youtube_pot_provider = True
    except OSError:
        youtube_pot_provider = False
    return {"ok": True, "service": "VYRO Web", "version": app.version,
            "engine": yt_dlp.version.__version__,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "aria2c": shutil.which("aria2c") is not None,
            "node": shutil.which("node") is not None,
            "youtube_pot_provider": youtube_pot_provider}


@app.post("/api/extract")
def extract(payload: ExtractRequest):
    url = validate_url(payload.url)
    opts = common_options(payload)
    opts.update({"skip_download": True, "extract_flat": "in_playlist" if payload.include_playlist else False})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise HTTPException(status_code=422, detail="لم يتم العثور على معلومات للوسائط")
        return clean_info(info)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"تعذر تحليل الرابط: {str(exc)[:500]}") from exc


def run_download(job_id: str, payload: DownloadRequest) -> None:
    url = validate_url(payload.url)
    # Match DownloadUtil.OUTPUT_TEMPLATE_ID in the Android source.
    output_template = str(DOWNLOAD_DIR / ("%(title).200B [%(id)s].%(ext)s"))
    if payload.audio_only:
        output_template = str(DOWNLOAD_DIR / "Audio" / "%(title).200B [%(id)s].%(ext)s")
    else:
        output_template = str(DOWNLOAD_DIR / "Video" / "%(title).200B [%(id)s].%(ext)s")
    if payload.subdirectory_extractor:
        output_template = str(Path(output_template).parent / "%(extractor_key)s" / Path(output_template).name)
    if payload.playlist and payload.subdirectory_playlist_title:
        output_template = str(Path(output_template).parent / "%(playlist)s" / Path(output_template).name)
    completed_paths: list[str] = []
    progress_finished_paths: list[str] = []
    cancel_event = cancel_events.setdefault(job_id, threading.Event())
    last_persist = [0.0]

    class DownloadCancelled(Exception):
        pass

    def hook(data: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("ألغى المستخدم مهمة التنزيل")
        status = data.get("status")
        with jobs_lock:
            job = jobs.get(job_id)
            if not job:
                return
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes") or 0
                job.update({"status": "downloading", "downloaded_bytes": done, "total_bytes": total,
                            "percent": round(done * 100 / total, 1) if total else None,
                            "speed": data.get("speed"), "eta": data.get("eta"),
                            "filename": stored_path(data.get("filename") or "") if data.get("filename") else ""})
            elif status == "finished":
                finished_name = stored_path(data.get("filename") or "") if data.get("filename") else ""
                job.update({"status": "processing", "percent": 100, "filename": finished_name})
                if finished_name:
                    progress_finished_paths.append(finished_name)
        if time.time() - last_persist[0] >= 1.5:
            last_persist[0] = time.time()
            persist_job(job_id)

    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "outtmpl": output_template,
        "progress_hooks": [hook],
        "postprocessor_hooks": [],
        "continuedl": True,
        "concurrent_fragment_downloads": payload.concurrent_fragments,
        "noplaylist": not payload.playlist,
        "writethumbnail": (
            payload.write_thumbnail or payload.embed_thumbnail
            or (payload.audio_only and payload.embed_metadata)
        ),
        "allow_multiple_audio_streams": payload.merge_audio_streams,
        "writesubtitles": payload.subtitles,
        "writeautomaticsub": payload.auto_subtitles,
        "subtitleslangs": [x.strip() for x in payload.subtitle_languages.split(",") if x.strip()],
        "restrictfilenames": payload.restrict_filenames,
        "updatetime": not payload.no_mtime,
    }
    metadata_actions = []
    if payload.audio_only and payload.embed_metadata:
        # Android's audio path embeds artwork and enriches common music tags.
        from yt_dlp.postprocessor.metadataparser import MetadataParserPP
        interpret = MetadataParserPP.Actions.INTERPRET
        metadata_actions.extend([
            (interpret, "%(release_year,upload_date)s", "%(meta_date)s"),
            (interpret, "%(album,playlist,title)s" if payload.playlist else "%(album,title)s", "%(meta_album)s"),
        ])
        if payload.playlist:
            metadata_actions.append(
                (interpret, "%(track_number,playlist_index)d", "%(meta_track)s")
            )
        if payload.crop_artwork:
            opts["postprocessor_args"] = {"ThumbnailsConvertor+ffmpeg_o": [
                "-c:v", "mjpeg", "-vf", "crop=if(gt(ih,iw),iw,ih):if(gt(iw,ih),ih,iw)"
            ]}
    if payload.use_aria2c:
        import shutil
        if not shutil.which("aria2c"):
            raise ValueError("aria2c غير مثبت على الخادم")
        opts["external_downloader"] = {"default": "aria2c"}
        opts["external_downloader_args"] = {"aria2c": ["-c", "--summary-interval=1", "-x", "8", "-s", "8"]}
    if payload.use_download_archive:
        opts["download_archive"] = str(DATA_DIR / "download-archive.txt")
    if payload.playlist_items:
        if not re.fullmatch(r"[0-9,\-\s]+", payload.playlist_items):
            raise ValueError("صيغة تحديد عناصر قائمة التشغيل غير صحيحة")
        opts["playlist_items"] = payload.playlist_items
    remux_video = payload.remux_video
    if not payload.audio_only and payload.subtitles and payload.embed_subtitles and not remux_video:
        # Android switches to MKV when embedding downloaded subtitles.
        remux_video = "mkv"
    if remux_video in {"mkv", "mp4", "webm", "mov"}:
        opts["merge_output_format"] = remux_video
    if payload.subtitle_format:
        if not re.fullmatch(r"[A-Za-z0-9.+-]{1,32}", payload.subtitle_format):
            raise ValueError("صيغة الترجمات غير صالحة")
        opts["subtitlesformat"] = payload.subtitle_format
    if payload.format_sort:
        # Accept the same yt-dlp sort fields used by Android DownloadUtil,
        # including vcodec/acodec and abr~192 quality preferences.
        allowed_sort = {
            "res", "fps", "codec", "size", "br", "asr", "ext", "quality",
            "proto", "vcodec", "acodec", "height", "width", "lang",
            "source", "channels", "container", "hdr", "filesize", "tbr",
            "abr", "vbr", "hasvid", "hasaud",
        }
        sort_pattern = re.compile(
            r"[-+]?(?P<field>[A-Za-z]+)(?:(?::|~)(?P<value>[A-Za-z0-9_.+-]+))?"
        )
        for item in payload.format_sort:
            match = sort_pattern.fullmatch(item)
            if not match or match.group("field") not in allowed_sort:
                raise ValueError("خيارات ترتيب الصيغ غير صالحة")
        opts["format_sort"] = list(payload.format_sort)
    if payload.download_sections:
        if payload.split_chapters:
            raise ValueError("لا يمكن الجمع بين تقسيم الفصول والمقاطع الزمنية في المهمة نفسها")
        if len(payload.download_sections) > 50 or any(len(x) > 100 for x in payload.download_sections):
            raise ValueError("عدد أو طول مقاطع التنزيل يتجاوز الحد المسموح")
        if any(not re.fullmatch(r"\*?\d{1,6}(?:\.\d{1,3})?-\d{1,6}(?:\.\d{1,3})?", x) for x in payload.download_sections):
            raise ValueError("صيغة مقاطع الفيديو غير صحيحة")
        # Android prefixes each explicit clip range with '*' for time-based
        # sections. Normalize the browser's shorthand to the same syntax.
        from yt_dlp.utils import download_range_func
        ranges = []
        for section in payload.download_sections:
            normalized = section[1:] if section.startswith("*") else section
            start, end = map(float, normalized.split("-", 1))
            if end <= start:
                raise ValueError("يجب أن يكون وقت نهاية المقطع أكبر من وقت بدايته")
            ranges.append([start, end])
        opts["download_ranges"] = download_range_func([], ranges)
    if payload.output_template:
        # Only permit yt-dlp metadata placeholders and a conservative filename alphabet.
        if len(payload.output_template) > 240 or any(c in payload.output_template for c in ("/", "\\", "..")):
            raise ValueError("قالب اسم الملف غير مسموح")
        if not re.fullmatch(r"[A-Za-z0-9 _().%{}\[\]-]+", payload.output_template):
            raise ValueError("قالب اسم الملف يحتوي أحرفاً غير مسموحة")
        output_template = str(Path(output_template).parent / payload.output_template)
    elif payload.download_sections:
        output_template = str(Path(output_template).parent / "%(title).200B [%(section_start)d-%(section_end)d].%(ext)s")
    if payload.split_chapters and not payload.audio_only:
        chapter_template = str(Path(output_template).parent / "%(title).200B" / "%(section_number)d - %(section_title).200B.%(ext)s")
        default_template = str(Path(output_template).parent / "%(title).200B" / "%(title).200B [%(id)s].%(ext)s")
        opts["outtmpl"] = {"default": default_template, "chapter": chapter_template}
    else:
        opts["outtmpl"] = output_template
    if payload.format_id:
        # yt-dlp format selectors are expressions, not shell commands. Keep the
        # accepted alphabet narrow and reject option-like / path-like input.
        if len(payload.format_id) > 160 or not re.fullmatch(r"[A-Za-z0-9_.,+*/()\[\]{}:=!&|<>^$% -]+", payload.format_id):
            raise ValueError("اختيار صيغة التنزيل غير صالح")
    if payload.audio_only:
        if payload.audio_format not in {None, "", "mp3", "m4a", "wav", "opus", "flac", "aac", "alac"}:
            raise ValueError("صيغة الصوت غير مدعومة")
        if payload.audio_quality not in {"0", "1", "2", "3", "4"}:
            raise ValueError("جودة الصوت غير صالحة")
        opts["format"] = payload.format_id or "bestaudio/best"
        if not payload.audio_format and not payload.format_id:
            abr_preference = {"1": "abr~192", "2": "abr~128", "3": "abr~64", "4": "abr~32"}.get(payload.audio_quality)
            if abr_preference:
                opts["format_sort"] = list(opts.get("format_sort", [])) + [abr_preference]
    elif payload.format_id:
        opts["format"] = payload.format_id
    else:
        opts["format"] = "bv*+ba/b"
    if payload.sponsorblock:
        allowed_categories = {"sponsor", "intro", "outro", "selfpromo", "preview", "filler", "interaction", "music_offtopic", "poi_highlight", "chapter"}
        if not payload.sponsorblock_categories or any(x not in allowed_categories for x in payload.sponsorblock_categories):
            raise ValueError("فئات SponsorBlock غير صالحة")
    if payload.new_title:
        if any(ch in payload.new_title for ch in "\r\n"):
            raise ValueError("العنوان الجديد غير صالح")
        from yt_dlp.postprocessor.metadataparser import MetadataParserPP
        metadata_actions.append((MetadataParserPP.Actions.REPLACE, "title", ".+", payload.new_title))
    # YoutubeDL's embedded Python API expects postprocessors explicitly;
    # CLI-only convenience flags are not automatically expanded here.
    postprocessors: list[dict[str, Any]] = []
    if metadata_actions:
        postprocessors.append({"key": "MetadataParser", "actions": metadata_actions, "when": "pre_process"})
    if payload.sponsorblock:
        postprocessors.append({"key": "SponsorBlock", "categories": payload.sponsorblock_categories, "when": "after_filter"})
    if payload.convert_subtitles in {"srt", "vtt", "ass", "lrc"}:
        postprocessors.append({"key": "FFmpegSubtitlesConvertor", "format": payload.convert_subtitles, "when": "before_dl"})
    if payload.write_thumbnail or (payload.audio_only and payload.embed_metadata):
        thumbnail_format = "png" if payload.write_thumbnail else "jpg"
        postprocessors.append({"key": "FFmpegThumbnailsConvertor", "format": thumbnail_format, "when": "before_dl"})
    if payload.audio_only:
        postprocessors.append({"key": "FFmpegExtractAudio", "preferredcodec": payload.audio_format or "best"})
    elif remux_video in {"mkv", "mp4", "webm", "mov"}:
        postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": remux_video})
    if not payload.audio_only and payload.embed_subtitles:
        postprocessors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": payload.keep_subtitle_files})
    if payload.embed_metadata or (payload.embed_chapters and not payload.audio_only and not payload.download_sections):
        postprocessors.append({
            "key": "FFmpegMetadata",
            "add_metadata": payload.embed_metadata,
            "add_chapters": payload.embed_chapters and not payload.audio_only and not bool(payload.download_sections),
        })
    if payload.embed_thumbnail or (payload.audio_only and payload.embed_metadata):
        postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": payload.write_thumbnail})
    if payload.split_chapters and not payload.audio_only:
        postprocessors.append({"key": "FFmpegSplitChapters"})
    opts["postprocessors"] = postprocessors

    def stored_path(value: str) -> str:
        try:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = DOWNLOAD_DIR / candidate
            resolved = candidate.resolve()
            return resolved.relative_to(DOWNLOAD_DIR.resolve()).as_posix()
        except (OSError, ValueError):
            return Path(value).name

    def after_move(data: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("ألغى المستخدم مهمة التنزيل")
        if data.get("status") != "finished":
            return
        info = data.get("info_dict") or {}
        filepath = info.get("filepath") or data.get("filepath")
        if filepath:
            completed_paths.append(stored_path(filepath))

    opts["postprocessor_hooks"].append(after_move)
    try:
        if cancel_event.is_set():
            raise DownloadCancelled("ألغى المستخدم مهمة التنزيل")
        extra = common_options(payload)
        opts.update(extra)
        if payload.auto_subtitles and not payload.auto_translated_subtitles:
            extractor_args = dict(opts.get("extractor_args") or {})
            youtube_args = dict(extractor_args.get("youtube") or {})
            youtube_args["skip"] = ["translated_subs"]
            extractor_args["youtube"] = youtube_args
            opts["extractor_args"] = extractor_args
        with yt_dlp.YoutubeDL(opts) as ydl:
            return_code = ydl.download([url])
        if return_code not in (None, 0):
            raise RuntimeError(f"انتهى محرك yt-dlp برمز خطأ: {return_code}")
        if cancel_event.is_set():
            raise DownloadCancelled("ألغى المستخدم مهمة التنزيل")
        # Prefer post-processed/moved paths. The progress hook's `finished`
        # filename can be a temporary pre-merge file, so use it only as fallback.
        if not completed_paths:
            completed_paths.extend(progress_finished_paths)
        completed_paths = list(dict.fromkeys(completed_paths))
        with jobs_lock:
            completion_update = {"status": "completed", "percent": 100, "finished_at": time.time(),
                                 "files": completed_paths}
            if not completed_paths:
                completion_update["warning"] = "لم يُنشئ المحرك ملفاً جديداً؛ قد تكون العناصر موجودة مسبقاً في سجل التنزيلات."
            jobs[job_id].update(completion_update)
        # The web UI intentionally has no completed-download log/history.
        # Keep only the active job snapshot needed for live progress and recovery;
        # the browser's native downloads list is the user's download history.
        persist_job(job_id)
    except DownloadCancelled as exc:
        with jobs_lock:
            jobs[job_id].update({"status": "cancelled", "error": str(exc), "finished_at": time.time()})
        persist_job(job_id)
    except Exception as exc:
        with jobs_lock:
            if cancel_event.is_set():
                jobs[job_id].update({"status": "cancelled", "error": "ألغى المستخدم مهمة التنزيل", "finished_at": time.time()})
            else:
                jobs[job_id].update({"status": "failed", "error": str(exc)[:700], "finished_at": time.time()})
        persist_job(job_id)
    finally:
        cancel_events.pop(job_id, None)


def submit_download_job(job_id: str, payload: DownloadRequest) -> None:
    future = executor.submit(run_download, job_id, payload)

    def capture_worker_exception(done):
        error = done.exception()
        if error is not None:
            with jobs_lock:
                job = jobs.get(job_id)
                if job:
                    job.update({"status": "failed", "error": str(error)[:700], "finished_at": time.time()})
            persist_job(job_id)
            cancel_events.pop(job_id, None)

    future.add_done_callback(capture_worker_exception)


def recover_pending_jobs() -> None:
    with db() as conn:
        rows = conn.execute("SELECT id, data FROM jobs WHERE status='queued' ORDER BY updated_at ASC").fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row["data"])
            request_data = snapshot.get("request")
            if not snapshot.get("restart_recoverable") or not isinstance(request_data, dict):
                continue
            payload = DownloadRequest(**request_data)
        except Exception as exc:
            with db() as conn:
                conn.execute(
                    "UPDATE jobs SET status='failed', data=?, updated_at=? WHERE id=?",
                    (json.dumps({"id": row["id"], "status": "failed", "error": f"تعذرت استعادة المهمة: {str(exc)[:300]}"}, ensure_ascii=False), time.time(), row["id"]),
                )
            continue
        with jobs_lock:
            jobs[row["id"]] = snapshot
            cancel_events[row["id"]] = threading.Event()
        submit_download_job(row["id"], payload)


# Start eligible SQLite-persisted jobs after the executor and worker function exist.
recover_pending_jobs()


@app.post("/api/download", status_code=202)
def create_download(payload: DownloadRequest):
    validate_url(payload.url)
    job_id = uuid.uuid4().hex
    restart_recoverable = True
    job_snapshot = {
        "id": job_id, "status": "queued", "percent": 0, "created_at": time.time(),
        "files": [], "url": payload.url, "title": payload.media_title,
        "extractor": payload.extractor, "restart_recoverable": restart_recoverable,
    }
    if restart_recoverable:
        job_snapshot["request"] = payload.model_dump()
    with jobs_lock:
        jobs[job_id] = job_snapshot
        cancel_events[job_id] = threading.Event()
    persist_job(job_id)
    submit_download_job(job_id, payload)
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    with db() as conn:
        rows = conn.execute("SELECT id, data FROM jobs ORDER BY updated_at DESC LIMIT 200").fetchall()
    persisted = {row["id"]: json.loads(row["data"]) for row in rows}
    with jobs_lock:
        persisted.update(jobs)
        return sorted(persisted.values(), key=lambda j: j.get("created_at", 0), reverse=True)

@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="المهمة غير موجودة أو لا تعمل في هذه الجلسة")
        if job.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
            raise HTTPException(status_code=409, detail="لا يمكن إلغاء مهمة منتهية")
        event = cancel_events.get(job_id)
        if event:
            event.set()
        job["status"] = "cancelling"
    persist_job(job_id)
    return {"id": job_id, "status": "cancelling"}

@app.get("/api/history")
def download_history(limit: int = 100):
    limit = max(1, min(limit, 500))
    with db() as conn:
        rows = conn.execute("SELECT id,job_id,title,source_url,extractor,filename,completed_at FROM download_history ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(row) for row in rows]

@app.get("/api/history/export")
def export_history(format: str = "json"):
    with db() as conn:
        rows = conn.execute("SELECT title,source_url,extractor,filename,completed_at FROM download_history ORDER BY completed_at DESC").fetchall()
    if format == "txt":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("\n".join(row["source_url"] for row in rows if row["source_url"]))
    if format != "json":
        raise HTTPException(status_code=422, detail="صيغة التصدير المدعومة: json أو txt")
        return JSONResponse([dict(row) for row in rows], headers={"Content-Disposition": "attachment; filename=vyro-history.json"})

@app.delete("/api/history/{entry_id}")
def delete_history_entry(entry_id: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM download_history WHERE id=?", (entry_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="عنصر السجل غير موجود")
    return {"deleted": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            return dict(job)
    with db() as conn:
        row = conn.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="المهمة غير موجودة")
    return json.loads(row["data"])


@app.get("/api/files/{filename:path}")
def download_file(filename: str):
    relative = Path(filename)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    path = (DOWNLOAD_DIR / relative).resolve()
    if DOWNLOAD_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    return FileResponse(path, filename=path.name)


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
