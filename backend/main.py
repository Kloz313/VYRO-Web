from __future__ import annotations

import asyncio
import ipaddress
import importlib.metadata
import json
import logging
import os
import sqlite3
import re
import socket
import threading
import tempfile
import zipfile
from contextlib import contextmanager, asynccontextmanager
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
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
FILE_RETENTION_HOURS = max(1, min(int(os.getenv("VYRO_FILE_RETENTION_HOURS", "24")), 168))
FILE_RETENTION_SECONDS = FILE_RETENTION_HOURS * 60 * 60


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


# Extraction is independent from the download worker pool. Keep it bounded so
# concurrent browser requests do not create a burst of YouTube player requests.
EXTRACT_MAX_CONCURRENT = _bounded_int_env("VYRO_EXTRACT_WORKERS", 2, 1, 8)
EXTRACT_MAX_ATTEMPTS = _bounded_int_env("VYRO_EXTRACT_ATTEMPTS", 2, 1, 3)
EXTRACT_RETRY_BASE_SECONDS = 1.5
extract_semaphore = threading.BoundedSemaphore(EXTRACT_MAX_CONCURRENT)
extract_logger = logging.getLogger("mediaweb.extract")

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


@asynccontextmanager
async def app_lifespan(_app):
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="VYRO Web API", version="0.3.0", description="Web edition of VYRO Downloader", lifespan=app_lifespan)

# Public service: no account gate, per-IP request quota, or per-user download quota.
# Requests enter the worker queue; throughput is bounded only by host resources.
executor = ThreadPoolExecutor(max_workers=max(1, min(int(os.getenv("VYRO_DOWNLOAD_WORKERS", "2")), 16)))
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = threading.RLock()
cancel_events: dict[str, threading.Event] = {}


class ExtractRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    include_playlist: bool = False


class DownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    client_job_id: str | None = Field(default=None, exclude=True)
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


class YtDlpDiagnostics:
    """Collect only safe, high-level extractor signals; never retain raw URLs or tokens."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def _record(self, message: str) -> None:
        text = str(message).lower()
        event = None
        if "po token providers: none" in text:
            event = "لم يتعرف yt-dlp على موفر PO Token."
        elif "po token providers:" in text and "bgutil" in text:
            event = "تم تحميل إضافة BGUtil الخاصة برموز PO Token."
        elif "generating a" in text and "po token" in text:
            event = "طلب yt-dlp توليد PO Token لهذا الطلب."
        elif "po token" in text and ("generated" in text or "successfully" in text):
            event = "أبلغ yt-dlp عن اكتمال توليد PO Token."
        elif "downloading" in text and "player api json" in text:
            # Record the selected YouTube player client, but never the raw log line.
            match = re.search(r"downloading\s+([a-z0-9_-]+)\s+player api json", text)
            if match:
                event = f"اختار YouTube عميل المشغل: {match.group(1)}."
        elif "solving js challenge" in text:
            if "native python" in text:
                event = "بدأ TikTok حل تحدي JavaScript بالتنفيذ الأصلي في Python."
            else:
                event = "بدأ yt-dlp حل تحدي JavaScript الخاص بـ TikTok."
        elif "downloading webpage with challenge cookie" in text:
            event = "أنشأ yt-dlp challenge cookie وأعاد طلب صفحة TikTok."
        elif ("failed to solve js challenge" in text or "challenge solving failed" in text
              or "unable to solve js challenge" in text):
            event = "فشل yt-dlp في حل تحدي JavaScript الخاص بالمنصة."
        elif "sign in to confirm" in text or "login_required" in text or "confirm you're not a bot" in text:
            event = "طلب YouTube تسجيل الدخول أو التحقق من أن الطلب ليس آلياً."
        elif "http error 403" in text or "http 403" in text:
            event = "رفضت المنصة أحد الطلبات برمز HTTP 403."
        elif "http error 429" in text or "http 429" in text:
            event = "فرضت المنصة تحديداً مؤقتاً للطلبات (HTTP 429)."
        if event and event not in self.events:
            self.events.append(event)
            del self.events[12:]

    def debug(self, message: str) -> None:
        self._record(message)

    def warning(self, message: str) -> None:
        self._record(message)

    def error(self, message: str) -> None:
        self._record(message)


def explain_extractor_error(error: Exception | str) -> str:
    message = str(error)
    lower = message.lower()
    if "sign in to confirm" in lower or "login_required" in lower or "confirm you're not a bot" in lower:
        return ("طلب YouTube تسجيل الدخول أو التحقق من أن الطلب ليس آلياً. "
                "وجود مزود PO Token لا يضمن تجاوز قيود عنوان IP أو تحديات المنصة.")
    if "http error 403" in lower or "http 403" in lower or "403 forbidden" in lower:
        return ("رفضت المنصة الطلب (HTTP 403). قد يرتبط ذلك بحماية المنصة أو قيود عنوان IP "
                "أو انتهاء صلاحية رابط الوسائط؛ ولا يوجد حل عام مضمون لهذا الرفض.")
    if "http error 429" in lower or "http 429" in lower or "too many requests" in lower:
        return "فرضت المنصة تحديداً مؤقتاً للطلبات (HTTP 429). انتظر قليلاً ثم أعد المحاولة."
    if "unsupported url" in lower or "no suitable extractor" in lower:
        return "لم يتمكن yt-dlp من التعرف على رابط المحتوى أو لا يدعم هذا النوع من الروابط حالياً."
    if ("javascript runtime" in lower or "yt-dlp-ejs" in lower
            or "js challenge" in lower or "challenge solving failed" in lower):
        return ("تعذر إكمال التحقق البرمجي الذي تتطلبه المنصة. تحقق من توفر مكونات JavaScript "
                "وتوافق إصدار yt-dlp، وقد يكون التحدي مرتبطاً أيضاً بقيود المنصة أو عنوان IP.")
    if "no video" in lower or "did not contain any video" in lower:
        return "لم يعثر yt-dlp على فيديو قابل للاستخراج في هذا الرابط."
    return f"تعذر إكمال العملية باستخدام yt-dlp: {message[:350]}"


def common_options(payload: BaseModel) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 20,
        "retries": 3,
        "extractor_retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "noplaylist": not bool(getattr(payload, "include_playlist", False) or getattr(payload, "playlist", False)),
        # Node.js + yt-dlp-ejs are installed in the container for YouTube JS challenges.
        "js_runtimes": {"node": {}},
        # Do not force YouTube player_client here. Android leaves this to yt-dlp's
        # own defaults; the supplied successful device log selected visionos.
        # A fixed list can suppress newer/default clients and change format availability.
    }
    host = (urlparse(payload.url).hostname or "").lower().rstrip(".")
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        # Keep Chrome impersonation scoped to TikTok and use yt-dlp's typed API.
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts["impersonate"] = ImpersonateTarget.from_str("chrome")
    return opts


def clean_format(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_id": item.get("format_id"),
        "format": item.get("format"),
        "format_note": item.get("format_note"),
        "ext": item.get("ext"),
        "resolution": item.get("resolution") or (f"{item.get('height')}p" if item.get("height") else None),
        "width": item.get("width"),
        "height": item.get("height"),
        "fps": item.get("fps"),
        "vcodec": item.get("vcodec"),
        "acodec": item.get("acodec"),
        "video_ext": item.get("video_ext"),
        "audio_ext": item.get("audio_ext"),
        "filesize": item.get("filesize"),
        "filesize_approx": item.get("filesize_approx"),
        "vbr": item.get("vbr"),
        "abr": item.get("abr"),
        "tbr": item.get("tbr"),
    }


def clean_info(info: dict[str, Any]) -> dict[str, Any]:
    formats = [clean_format(item) for item in (info.get("formats") or []) if isinstance(item, dict)]
    requested_formats = [clean_format(item) for item in (info.get("requested_formats") or []) if isinstance(item, dict)]
    requested_downloads = []
    for item in info.get("requested_downloads") or []:
        if not isinstance(item, dict):
            continue
        normalized = clean_format(item)
        normalized["filename"] = item.get("filename")
        normalized["requested_formats"] = [clean_format(part) for part in (item.get("requested_formats") or []) if isinstance(part, dict)]
        requested_downloads.append(normalized)
    chapters = [
        {"title": chapter.get("title"), "start_time": chapter.get("start_time"), "end_time": chapter.get("end_time")}
        for chapter in (info.get("chapters") or []) if isinstance(chapter, dict)
    ]
    entries = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        entries.append({"id": entry.get("id"), "title": entry.get("title"), "url": entry.get("webpage_url") or entry.get("url"), "duration": entry.get("duration"), "thumbnail": entry.get("thumbnail"), "playlist_index": entry.get("playlist_index")})
    playlist_count = info.get("playlist_count") or len(entries)
    return {
        "id": info.get("id"), "title": info.get("title"), "fulltitle": info.get("fulltitle"),
        "webpage_url": info.get("webpage_url"), "original_url": info.get("original_url"),
        "extractor": info.get("extractor_key") or info.get("extractor"),
        "extractor_key": info.get("extractor_key"),
        "description": info.get("description"), "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"), "duration_string": info.get("duration_string"),
        "uploader": info.get("uploader"), "uploader_id": info.get("uploader_id"),
        "channel": info.get("channel"), "view_count": info.get("view_count"),
        "like_count": info.get("like_count"), "comment_count": info.get("comment_count"),
        "upload_date": info.get("upload_date"), "availability": info.get("availability"),
        "live_status": info.get("live_status"), "tags": info.get("tags") or [],
        "format": info.get("format"), "format_id": info.get("format_id"),
        "ext": info.get("ext"), "vcodec": info.get("vcodec"), "acodec": info.get("acodec"),
        "filename": info.get("filename"),
        "formats": formats, "requested_formats": requested_formats,
        "requested_downloads": requested_downloads, "chapters": chapters,
        "subtitles": {lang: [{"ext": item.get("ext"), "name": item.get("name"), "protocol": item.get("protocol")}
                              for item in items]
                      for lang, items in (info.get("subtitles") or {}).items()},
        "automatic_captions": {lang: [{"ext": item.get("ext"), "name": item.get("name"), "protocol": item.get("protocol")}
                                       for item in items]
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
    try:
        importlib.metadata.version("bgutil-ytdlp-pot-provider")
        youtube_pot_plugin = True
    except importlib.metadata.PackageNotFoundError:
        youtube_pot_plugin = False
    return {"ok": True, "service": "VYRO Web", "version": app.version,
            "engine": yt_dlp.version.__version__,
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "aria2c": shutil.which("aria2c") is not None,
            "node": shutil.which("node") is not None,
            "youtube_pot_provider": youtube_pot_provider,
            "youtube_pot_plugin": youtube_pot_plugin,
            "youtube_pot_ready": youtube_pot_provider and youtube_pot_plugin,
            "extract_concurrency": EXTRACT_MAX_CONCURRENT,
            "extract_attempts": EXTRACT_MAX_ATTEMPTS}


def _platform_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host == "youtube.com" or host.endswith(".youtube.com") or host in {"youtu.be", "youtube-nocookie.com"}:
        return "youtube"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    return host[:80] or "unknown"


def _is_transient_extractor_error(error: Exception | str) -> bool:
    """Retry only transport/server throttling failures, never platform challenges."""
    message = str(error).lower()
    permanent_markers = (
        "sign in to confirm", "login_required", "confirm you're not a bot",
        "http error 403", "http 403", "403 forbidden", "unsupported url",
        "no suitable extractor", "private video", "video unavailable",
    )
    if any(marker in message for marker in permanent_markers):
        return False
    transient_markers = (
        "http error 429", "http 429", "too many requests",
        "http error 500", "http error 502", "http error 503", "http error 504",
        "http 500", "http 502", "http 503", "http 504",
        "timed out", "timeout", "connection reset", "connection aborted",
        "remote end closed", "temporary failure in name resolution",
        "name or service not known", "network is unreachable", "unexpected eof",
        "ssl: eof", "server disconnected", "incomplete read",
    )
    return any(marker in message for marker in transient_markers)


def _safe_error_summary(error: Exception | str) -> str:
    """Keep useful extractor wording while removing URLs and credential-like values."""
    message = str(error)
    message = re.sub(r"https?://[^\s\"'<>]+", "[URL]", message, flags=re.IGNORECASE)
    message = re.sub(
        r"(?i)(po[_-]?token|visitor[_-]?data|authorization|cookie|signature|sig)=([^&\s]+)",
        r"\1=[REDACTED]", message,
    )
    return re.sub(r"\s+", " ", message).strip()[:240]


def _retry_delay_seconds(error: Exception | str, attempt: int) -> float:
    message = str(error).lower()
    if "429" in message or "too many requests" in message:
        return min(5.0 * (2 ** (attempt - 1)), 15.0)
    return min(EXTRACT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), 6.0)


def _extract_info_with_retries(url: str, opts: dict[str, Any], diagnostics: YtDlpDiagnostics,
                               request_id: str, platform: str) -> dict[str, Any] | None:
    """Bound concurrency and retry only transient transport/throttling failures."""
    started = time.monotonic()
    with extract_semaphore:
        for attempt in range(1, EXTRACT_MAX_ATTEMPTS + 1):
            try:
                extract_logger.info(
                    "extract_attempt_start request_id=%s platform=%s attempt=%s/%s engine=%s",
                    request_id, platform, attempt, EXTRACT_MAX_ATTEMPTS, yt_dlp.version.__version__,
                )
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                if not info:
                    raise RuntimeError("لم يتم العثور على معلومات للوسائط")
                extract_logger.info(
                    "extract_success request_id=%s platform=%s attempt=%s duration_ms=%s diagnostics=%s",
                    request_id, platform, attempt, int((time.monotonic() - started) * 1000),
                    json.dumps(diagnostics.events[:8], ensure_ascii=False),
                )
                return info
            except Exception as exc:
                retryable = _is_transient_extractor_error(exc)
                if retryable and attempt < EXTRACT_MAX_ATTEMPTS:
                    delay = _retry_delay_seconds(exc, attempt)
                    extract_logger.warning(
                        "extract_retry request_id=%s platform=%s attempt=%s/%s error_type=%s error_summary=%s wait_seconds=%.1f diagnostics=%s",
                        request_id, platform, attempt, EXTRACT_MAX_ATTEMPTS, type(exc).__name__,
                        _safe_error_summary(exc), delay,
                        json.dumps(diagnostics.events[:8], ensure_ascii=False),
                    )
                    time.sleep(delay)
                    continue
                extract_logger.error(
                    "extract_failed request_id=%s platform=%s attempts=%s duration_ms=%s error_type=%s error_summary=%s retryable=%s diagnostics=%s",
                    request_id, platform, attempt, int((time.monotonic() - started) * 1000),
                    type(exc).__name__, _safe_error_summary(exc), retryable,
                    json.dumps(diagnostics.events[:8], ensure_ascii=False),
                )
                raise
    return None


@app.post("/api/extract")
def extract(payload: ExtractRequest):
    url = validate_url(payload.url)
    request_id = uuid.uuid4().hex[:12]
    platform = _platform_for_url(url)
    diagnostics = YtDlpDiagnostics()
    opts = common_options(payload)
    opts.update({"skip_download": True, "extract_flat": "in_playlist" if payload.include_playlist else False,
                 "logger": diagnostics, "verbose": True})
    try:
        info = _extract_info_with_retries(url, opts, diagnostics, request_id, platform)
        if not info:
            raise HTTPException(status_code=422, detail="لم يتم العثور على معلومات للوسائط")
        return clean_info(info)
    except HTTPException:
        raise
    except Exception as exc:
        detail = explain_extractor_error(exc)
        if diagnostics.events:
            detail += "\n" + "\n".join(diagnostics.events[:4])
        raise HTTPException(status_code=422, detail=detail) from exc


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
        diagnostics = YtDlpDiagnostics()
        extra.update({"logger": diagnostics, "verbose": True})
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
        extract_logger.info(
            "download_success job_id=%s platform=%s engine=%s file_count=%s diagnostics=%s",
            job_id, _platform_for_url(url), yt_dlp.version.__version__, len(completed_paths),
            json.dumps(diagnostics.events[:8], ensure_ascii=False),
        )
        with jobs_lock:
            completion_update = {"status": "completed", "percent": 100, "finished_at": time.time(),
                                 "files": completed_paths}
            if diagnostics.events:
                completion_update["diagnostics"] = diagnostics.events[:8]
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
        extract_logger.error(
            "download_failed job_id=%s platform=%s engine=%s error_type=%s diagnostics=%s",
            job_id, _platform_for_url(url), yt_dlp.version.__version__, type(exc).__name__,
            json.dumps(diagnostics.events[:8], ensure_ascii=False) if "diagnostics" in locals() else "[]",
        )
        with jobs_lock:
            if cancel_event.is_set():
                jobs[job_id].update({"status": "cancelled", "error": "ألغى المستخدم مهمة التنزيل", "finished_at": time.time()})
            else:
                failure = explain_extractor_error(exc)
                update = {"status": "failed", "error": failure, "finished_at": time.time()}
                if "diagnostics" in locals() and diagnostics.events:
                    update["diagnostics"] = diagnostics.events[:8]
                jobs[job_id].update(update)
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
    if payload.client_job_id:
        try:
            job_id = str(uuid.UUID(payload.client_job_id))
        except (ValueError, AttributeError):
            raise HTTPException(status_code=422, detail="معرّف مهمة التنزيل غير صالح")
        with jobs_lock:
            if job_id in jobs:
                raise HTTPException(status_code=409, detail="معرّف مهمة التنزيل مستخدم مسبقاً")
        with db() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                raise HTTPException(status_code=409, detail="معرّف مهمة التنزيل مستخدم مسبقاً")
    else:
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
    path = resolve_download_path(filename)
    return FileResponse(path, filename=path.name)


def resolve_download_path(filename: str) -> Path:
    relative = Path(filename)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    path = (DOWNLOAD_DIR / relative).resolve()
    if DOWNLOAD_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="الملف غير موجود")
    return path


@app.get("/api/jobs/{job_id}/download")
async def download_job_result(job_id: str):
    # The browser opens this request immediately after the visitor presses
    # Download. Keep the response pending until yt-dlp finishes, then return
    # one attachment (or a ZIP for playlists) without requiring another click.
    loop = asyncio.get_running_loop()
    start_time = loop.time()
    missing_job_deadline = start_time + 20
    deadline = start_time + 3 * 60 * 60
    job: dict[str, Any] | None = None
    while loop.time() < deadline:
        with jobs_lock:
            current = jobs.get(job_id)
            job = dict(current) if current else None
        if job is None:
            with db() as conn:
                row = conn.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row:
                try:
                    job = json.loads(row["data"])
                except (TypeError, json.JSONDecodeError):
                    job = None
        if job is None:
            if loop.time() >= missing_job_deadline:
                raise HTTPException(status_code=404, detail="مهمة التنزيل غير موجودة")
            await asyncio.sleep(0.25)
            continue
        status = job.get("status")
        if status == "completed":
            break
        if status in {"failed", "cancelled", "interrupted"}:
            raise HTTPException(status_code=409, detail=job.get("error") or "تعذر تجهيز الملف للتنزيل")
        await asyncio.sleep(1)
    else:
        raise HTTPException(status_code=504, detail="انتهت مهلة انتظار تجهيز الملف")

    files = list(dict.fromkeys(job.get("files") or []))
    if not files:
        raise HTTPException(status_code=404, detail=job.get("warning") or "لم يتم إنشاء ملف للتنزيل")
    paths = [(name, resolve_download_path(name)) for name in files]
    if len(paths) == 1:
        name, path = paths[0]
        return FileResponse(path, filename=path.name)

    # A playlist becomes one archive so the browser only needs one download.
    temp = tempfile.NamedTemporaryFile(prefix="vyro-playlist-", suffix=".zip", dir=DATA_DIR, delete=False)
    archive_path = Path(temp.name)
    temp.close()
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for relative_name, path in paths:
                archive.write(path, arcname=Path(relative_name).as_posix())
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return FileResponse(
        archive_path, filename="VYRO-playlist.zip",
        background=BackgroundTask(lambda: archive_path.unlink(missing_ok=True)),
    )


def cleanup_expired_data(now: float | None = None, retention_seconds: int | None = None) -> dict[str, int]:
    """Remove old terminal job snapshots and media files; never touch active jobs."""
    current = now if now is not None else time.time()
    retention = retention_seconds if retention_seconds is not None else FILE_RETENTION_SECONDS
    cutoff = current - retention
    terminal = {"completed", "failed", "cancelled", "interrupted"}
    active_paths: set[str] = set()
    removed_job_ids: set[str] = set()
    with jobs_lock:
        for job_id, job in list(jobs.items()):
            if job.get("status") not in terminal:
                filename = job.get("filename")
                if filename:
                    try:
                        active_paths.add((DOWNLOAD_DIR / filename).resolve().as_posix())
                    except (OSError, ValueError):
                        pass
                continue
            finished = job.get("finished_at") or job.get("created_at") or current
            if finished < cutoff:
                jobs.pop(job_id, None)
                removed_job_ids.add(job_id)
    with db() as conn:
        old_rows = conn.execute(
            "SELECT id FROM jobs WHERE status IN ('completed','failed','cancelled','interrupted') AND updated_at < ?",
            (cutoff,),
        ).fetchall()
        if old_rows:
            conn.executemany("DELETE FROM jobs WHERE id=?", [(row["id"],) for row in old_rows])
            removed_job_ids.update(row["id"] for row in old_rows)
        conn.execute("DELETE FROM download_history WHERE completed_at < ?", (cutoff,))
    removed_files = 0
    for path in DOWNLOAD_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.resolve().as_posix() in active_paths or path.stat().st_mtime >= cutoff:
                continue
            path.unlink(missing_ok=True)
            removed_files += 1
        except OSError:
            continue
    for path in DATA_DIR.glob("vyro-playlist-*.zip"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed_files += 1
        except OSError:
            continue
    for directory in sorted((p for p in DOWNLOAD_DIR.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    return {"jobs": len(removed_job_ids), "files": removed_files}


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(cleanup_expired_data)
        except Exception:
            # Cleanup is best-effort and must never stop the API service.
            pass
        await asyncio.sleep(3600)


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
