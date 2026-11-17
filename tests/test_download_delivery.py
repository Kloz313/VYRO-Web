from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

import backend.main as main


def test_job_download_waits_and_returns_single_file(monkeypatch, tmp_path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"video-data")
    monkeypatch.setattr(main, "DOWNLOAD_DIR", tmp_path)
    job_id = "download-single-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "completed", "files": ["video.mp4"]}
    response = TestClient(main.app).get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 200
    assert response.content == b"video-data"
    assert "attachment" in response.headers.get("content-disposition", "")


def test_job_download_zips_playlist(monkeypatch, tmp_path):
    (tmp_path / "one.mp4").write_bytes(b"one")
    (tmp_path / "two.mp4").write_bytes(b"two")
    monkeypatch.setattr(main, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    job_id = "download-playlist-fixture"
    with main.jobs_lock:
        main.jobs[job_id] = {"id": job_id, "status": "completed", "files": ["one.mp4", "two.mp4"]}
    response = TestClient(main.app).get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 200
    assert "VYRO-playlist.zip" in response.headers.get("content-disposition", "")
    archive_path = tmp_path / "result.zip"
    archive_path.write_bytes(response.content)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["one.mp4", "two.mp4"]
        assert archive.read("one.mp4") == b"one"
        assert archive.read("two.mp4") == b"two"


def test_download_route_waits_for_job_to_be_created(monkeypatch, tmp_path):
    import threading
    import time

    file_path = tmp_path / "late-video.mp4"
    file_path.write_bytes(b"late-video")
    monkeypatch.setattr(main, "DOWNLOAD_DIR", tmp_path)
    job_id = "wait-for-job-fixture"

    def publish_completed_job():
        with main.jobs_lock:
            main.jobs[job_id] = {"id": job_id, "status": "completed", "files": ["late-video.mp4"]}

    timer = threading.Timer(0.15, publish_completed_job)
    timer.start()
    try:
        response = TestClient(main.app).get(f"/api/jobs/{job_id}/download")
    finally:
        timer.join(timeout=2)
    assert response.status_code == 200
    assert response.content == b"late-video"


def test_create_download_accepts_browser_reserved_uuid(monkeypatch):
    import uuid

    job_id = str(uuid.uuid4())
    monkeypatch.setattr(main, "persist_job", lambda _job_id: None)
    monkeypatch.setattr(main, "submit_download_job", lambda _job_id, _payload: None)
    response = main.create_download(main.DownloadRequest(
        url="https://8.8.8.8/video", client_job_id=job_id
    ))
    assert response["id"] == job_id
