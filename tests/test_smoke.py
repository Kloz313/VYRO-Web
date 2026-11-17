from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["service"] == "VYRO Web"

def test_rejects_non_http_url():
    response = client.post("/api/extract", json={"url": "file:///etc/passwd"})
    assert response.status_code == 422

def test_rejects_loopback_url():
    response = client.post("/api/extract", json={"url": "http://127.0.0.1/"})
    assert response.status_code == 422

def test_jobs_and_history_endpoints():
    assert client.get("/api/jobs").status_code == 200
    assert client.get("/api/history").status_code == 200
    assert client.get("/api/history/export?format=txt").status_code == 200


def test_extract_response_shape_with_engine_stub():
    response = client.post("/api/extract", json={"url": "https://8.8.8.8/media"})
    assert response.status_code == 200
    assert response.json()["title"] == "Fixture"
    assert response.json()["formats"] == []

def test_clean_info_preserves_subtitle_languages_and_caps_large_playlists():
    from backend.main import clean_info
    info = clean_info({
        "id": "x", "title": "Playlist", "playlist_count": 205,
        "entries": [{"id": str(i), "title": f"Video {i}", "playlist_index": i + 1} for i in range(205)],
        "subtitles": {"ar": [{"ext": "vtt", "name": "Arabic"}]},
        "automatic_captions": {"en": [{"ext": "json3", "name": "English"}]},
    })
    assert len(info["entries"]) == 200
    assert info["entries_truncated"] is True
    assert info["subtitles"]["ar"][0]["ext"] == "vtt"
    assert "en" in info["automatic_captions"]


def test_job_detail_can_read_persisted_job():
    import json, time
    from backend.main import db
    job = {"id": "persisted-test", "status": "completed", "percent": 100, "files": []}
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs(id,status,data,updated_at) VALUES(?,?,?,?)",
            (job["id"], job["status"], json.dumps(job), time.time()),
        )
    response = client.get("/api/jobs/persisted-test")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_basic_auth_gate_when_enabled(monkeypatch):
    import base64
    import backend.main as main
    monkeypatch.setattr(main, "REQUIRE_AUTH", True)
    monkeypatch.setattr(main, "ACCESS_USERNAME", "tester")
    monkeypatch.setattr(main, "ACCESS_PASSWORD", "strong-test-password")
    denied = client.get("/")
    assert denied.status_code == 401
    token = base64.b64encode(b"tester:strong-test-password").decode()
    allowed = client.get("/", headers={"Authorization": f"Basic {token}"})
    assert allowed.status_code == 200
    monkeypatch.setattr(main, "REQUIRE_AUTH", False)


def test_download_concurrency_limit(monkeypatch):
    import backend.main as main
    monkeypatch.setattr(main, "MAX_ACTIVE_JOBS", 1)
    with main.jobs_lock:
        main.jobs["active-fixture"] = {"id": "active-fixture", "status": "downloading"}
    response = client.post("/api/download", json={"url": "https://8.8.8.8/media"})
    assert response.status_code == 429
    with main.jobs_lock:
        main.jobs.pop("active-fixture", None)
    monkeypatch.setattr(main, "MAX_ACTIVE_JOBS", 2)


def test_jobs_endpoint_reads_persisted_job_snapshot():
    import json, time
    from backend.main import db
    job = {"id": "persisted-list-test", "status": "completed", "percent": 100, "files": []}
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs(id,status,data,updated_at) VALUES(?,?,?,?)",
            (job["id"], job["status"], json.dumps(job), time.time()),
        )
    response = client.get("/api/jobs")
    assert response.status_code == 200
    assert any(item["id"] == job["id"] and item["status"] == "completed" for item in response.json())


def test_restart_marks_both_job_column_and_snapshot_interrupted():
    import json, time
    import backend.main as main
    job = {"id": "restart-fixture", "status": "downloading", "percent": 42}
    with main.db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs(id,status,data,updated_at) VALUES(?,?,?,?)",
            (job["id"], "downloading", json.dumps(job), time.time()),
        )
    main.init_db()
    response = client.get(f"/api/jobs/{job['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "interrupted"
    assert "إعادة تشغيل الخادم" in response.json()["error"]


def test_download_engine_defaults_align_with_android(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            for hook in captured["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "filename": "/tmp/vyro-fixture.mp4"})
                hook({"status": "finished", "filename": "/tmp/vyro-fixture.mp4"})
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "android-defaults-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": [], "title": "Fixture", "extractor": "test"}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(url="https://8.8.8.8/media"))
    assert captured["updatetime"] is False
    assert captured["concurrent_fragment_downloads"] == 8
    metadata_pp = next(pp for pp in captured["postprocessors"] if pp["key"] == "FFmpegMetadata")
    assert metadata_pp["add_metadata"] is True and metadata_pp["add_chapters"] is True
    assert captured["subtitleslangs"] == ["en.*", ".*-orig"]
    assert "%(title).200B" in captured["outtmpl"]
    assert main.jobs[job_id]["status"] == "completed"
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_audio_only_default_keeps_native_audio_and_maps_quality(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "audio-native-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(url="https://8.8.8.8/audio", audio_only=True))
    audio_pp = next(pp for pp in captured["postprocessors"] if pp["key"] == "FFmpegExtractAudio")
    assert audio_pp["preferredcodec"] == "best"
    metadata_pp = next(pp for pp in captured["postprocessors"] if pp["key"] == "FFmpegMetadata")
    assert metadata_pp["add_metadata"] is True and metadata_pp["add_chapters"] is False
    assert "FFmpegThumbnailsConvertor" in [pp["key"] for pp in captured["postprocessors"]]
    assert "EmbedThumbnail" in [pp["key"] for pp in captured["postprocessors"]]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)

    # Android's source-audio quality preference maps to yt-dlp's abr sorter.
    job_id = "audio-quality-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/audio", audio_only=True, audio_quality="2"
    ))
    assert captured["format_sort"] == ["abr~128"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_android_format_sort_syntax_and_time_sections_are_accepted(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "android-options-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/media",
        format_sort=["vcodec:av01", "acodec:aac", "abr~192"],
        download_sections=["10-90", "*120-180"],
    ))
    assert captured["format_sort"] == ["vcodec:av01", "acodec:aac", "abr~192"]
    assert captured["download_ranges"]({}, None) == [
        {"start_time": 10.0, "end_time": 90.0},
        {"start_time": 120.0, "end_time": 180.0},
    ]
    assert not any(pp.get("add_chapters") for pp in captured["postprocessors"] if pp["key"] == "FFmpegMetadata")
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_download_uses_postprocessed_filename_not_temporary_filename(monkeypatch):
    import backend.main as main

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            for hook in self.opts["progress_hooks"]:
                hook({"status": "finished", "filename": "/tmp/source.f137.mp4"})
            for hook in self.opts["postprocessor_hooks"]:
                hook({"status": "finished", "info_dict": {"filepath": "/tmp/final-video.mp4"}})
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "postprocessed-file-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(url="https://8.8.8.8/media"))
    assert main.jobs[job_id]["status"] == "completed"
    assert main.jobs[job_id]["files"] == ["final-video.mp4"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_cancel_requested_during_postprocessing_is_not_marked_completed(monkeypatch):
    import backend.main as main

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            for hook in self.opts["postprocessor_hooks"]:
                main.cancel_events[job_id].set()
                hook({"status": "finished", "info_dict": {"filepath": "/tmp/final-video.mp4"}})
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "cancel-postprocessing-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "processing", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(url="https://8.8.8.8/media"))
    assert main.jobs[job_id]["status"] == "cancelled"
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_chapter_split_and_audio_metadata_options(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "chapter-split-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/video", split_chapters=True,
        subdirectory_extractor=True, subdirectory_playlist_title=True, playlist=True,
        new_title="Changed title",
    ))
    assert isinstance(captured["outtmpl"], dict)
    assert "chapter" in captured["outtmpl"]
    assert "%(section_number)d" in captured["outtmpl"]["chapter"]
    assert "%(extractor_key)s" in captured["outtmpl"]["default"]
    assert "%(playlist)s" in captured["outtmpl"]["default"]
    metadata_pp = next(pp for pp in captured["postprocessors"] if pp["key"] == "MetadataParser")
    assert any(action[1:] == ("title", ".+", "Changed title") for action in metadata_pp["actions"])
    with main.jobs_lock:
        main.jobs.pop(job_id, None)

    job_id = "audio-metadata-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/audio", audio_only=True, playlist=True,
        crop_artwork=True,
    ))
    metadata_pp = next(pp for pp in captured["postprocessors"] if pp["key"] == "MetadataParser")
    actions = metadata_pp["actions"]
    assert any(action[1:] == ("%(release_year,upload_date)s", "%(meta_date)s") for action in actions)
    assert any(action[1:] == ("%(track_number,playlist_index)d", "%(meta_track)s") for action in actions)
    assert "ThumbnailsConvertor+ffmpeg_o" in captured["postprocessor_args"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_download_file_supports_safe_subdirectories(tmp_path, monkeypatch):
    import backend.main as main
    target = main.DOWNLOAD_DIR / "Video" / "fixture.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fixture")
    response = client.get("/api/files/Video/fixture.mp4")
    assert response.status_code == 200
    assert response.content == b"fixture"
    assert client.get("/api/files/../vyro-web.sqlite3").status_code in {404, 405}


def test_proxy_rejects_private_network_targets():
    import backend.main as main
    from fastapi import HTTPException
    try:
        main.common_options(main.ExtractRequest(
            url="https://8.8.8.8/media", proxy_url="http://127.0.0.1:8080"
        ))
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("private proxy target should be rejected")


def test_subtitle_embedding_and_rate_limit_use_python_api_postprocessors(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "subtitle-pp-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/video", subtitles=True, embed_subtitles=True,
        convert_subtitles="srt", rate_limit="2M",
    ))
    keys = [pp["key"] for pp in captured["postprocessors"]]
    assert captured["writesubtitles"] is True
    assert "FFmpegSubtitlesConvertor" in keys
    assert "FFmpegEmbedSubtitle" in keys
    assert "FFmpegVideoRemuxer" in keys
    assert captured["ratelimit"] == 2 * 1024 * 1024
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_ipv4_flag_maps_to_python_api_source_address():
    import backend.main as main
    opts = main.common_options(main.ExtractRequest(
        url="https://8.8.8.8/media", force_ipv4=True
    ))
    assert opts["source_address"] == "0.0.0.0"
    assert "force_ipv4" not in opts


def test_nonzero_ytdlp_return_code_marks_job_failed(monkeypatch):
    import backend.main as main

    class FakeYoutubeDL:
        def __init__(self, opts):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def download(self, urls):
            return 1

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "nonzero-return-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(url="https://8.8.8.8/media"))
    assert main.jobs[job_id]["status"] == "failed"
    assert "رمز خطأ" in main.jobs[job_id]["error"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_restart_requeues_jobs_with_safe_persisted_request():
    import json, time
    import backend.main as main
    payload = main.DownloadRequest(url="https://8.8.8.8/recoverable")
    snapshot = {
        "id": "recoverable-restart-fixture", "status": "downloading", "percent": 42,
        "restart_recoverable": True, "request": payload.model_dump(exclude={"cookies_text"}),
    }
    with main.db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs(id,status,data,updated_at) VALUES(?,?,?,?)",
            (snapshot["id"], "downloading", json.dumps(snapshot), time.time()),
        )
    main.init_db()
    with main.db() as conn:
        row = conn.execute("SELECT status,data FROM jobs WHERE id=?", (snapshot["id"],)).fetchone()
    recovered_snapshot = json.loads(row["data"])
    assert row["status"] == "queued"
    assert recovered_snapshot["status"] == "queued"
    assert recovered_snapshot["resumed_after_restart"] is True
    assert recovered_snapshot["request"]["url"] == payload.url
    main.recover_pending_jobs()
    deadline = time.time() + 2
    while time.time() < deadline and main.jobs.get(snapshot["id"], {}).get("status") not in {"completed", "failed"}:
        time.sleep(0.01)
    assert main.jobs[snapshot["id"]]["resumed_after_restart"] is True
    assert main.jobs[snapshot["id"]]["status"] == "completed"
    with main.jobs_lock:
        main.jobs.pop(snapshot["id"], None)


def test_cookie_contents_are_not_persisted_with_job_request():
    import backend.main as main
    response = main.create_download(main.DownloadRequest(
        url="https://8.8.8.8/cookie-session", cookies_text="# Netscape HTTP Cookie File\nsecret-cookie"
    ))
    job_id = response["id"]
    snapshot = main.jobs[job_id]
    assert snapshot["restart_recoverable"] is False
    assert "request" not in snapshot
    assert "secret-cookie" not in str(snapshot)
    import time
    deadline = time.time() + 2
    while time.time() < deadline and main.jobs.get(job_id, {}).get("status") not in {"completed", "failed"}:
        time.sleep(0.01)
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_android_audio_quality_includes_32_kbps_preference(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts): captured.update(opts)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def download(self, urls): return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "audio-ultra-low-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/audio", audio_only=True, audio_quality="4"
    ))
    assert captured["format_sort"] == ["abr~32"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_completed_downloads_are_not_added_to_visible_history(monkeypatch):
    import backend.main as main

    class FakeYoutubeDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def download(self, urls):
            for hook in self.opts["postprocessor_hooks"]:
                hook({"status": "finished", "info_dict": {"filepath": "/tmp/no-history-output.mp4"}})
            return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    with main.db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM download_history").fetchone()[0]
    job_id = "no-history-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": [], "title": "No history", "extractor": "test"}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(url="https://8.8.8.8/media"))
    with main.db() as conn:
        after = conn.execute("SELECT COUNT(*) FROM download_history").fetchone()[0]
    assert after == before
    assert main.jobs[job_id]["status"] == "completed"
    assert main.jobs[job_id]["files"] == ["no-history-output.mp4"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)


def test_frontend_has_no_download_history_or_task_panels():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "frontend"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    assert 'id="history"' not in html
    assert 'id="downloads"' not in html
    assert "refreshHistory" not in js
    assert "refreshJobs" not in js
    assert "downloadProgress" in js
    assert "/api/jobs/${encodeURIComponent(jobId)}" in js



def test_android_video_codec_and_resolution_sort_preferences(monkeypatch):
    import backend.main as main
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, opts): captured.update(opts)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def download(self, urls): return 0

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    job_id = "video-sort-preferences-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "queued", "files": []}
        main.cancel_events[job_id] = main.threading.Event()
    main.run_download(job_id, main.DownloadRequest(
        url="https://8.8.8.8/video", format_sort=["proto", "vcodec:h264", "ext", "res:2160"]
    ))
    assert captured["format_sort"] == ["proto", "vcodec:h264", "ext", "res:2160"]
    with main.jobs_lock:
        main.jobs.pop(job_id, None)
