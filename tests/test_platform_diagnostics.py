def test_youtube_does_not_override_yt_dlp_player_client_defaults():
    import backend.main as main
    opts = main.common_options(main.ExtractRequest(url="https://www.youtube.com/watch?v=fixture"))
    assert "extractor_args" not in opts
    assert opts["js_runtimes"] == {"node": {}}
    assert opts["retries"] == 3
    assert opts["extractor_retries"] == 3
    assert opts["fragment_retries"] == 3
    assert opts["socket_timeout"] == 20


def test_tiktok_uses_chrome_impersonation():
    import backend.main as main
    opts = main.common_options(main.ExtractRequest(url="https://www.tiktok.com/@user/video/123"))
    assert opts["impersonate"] == ("impersonate-target", "chrome")


def test_diagnostics_record_po_token_and_youtube_challenge_without_raw_message():
    import backend.main as main
    logger = main.YtDlpDiagnostics()
    logger.debug("[debug] [youtube] [pot] PO Token Providers: bgutil:http-2.0.2 (external)")
    logger.debug("[youtube] [pot:bgutil:http] Generating a gvs PO Token for mweb client")
    logger.warning("Sign in to confirm you're not a bot")
    assert len(logger.events) == 3
    assert all("PO Token" in event or "YouTube" in event for event in logger.events)
    assert "Sign in to confirm" not in " ".join(logger.events)


def test_platform_errors_are_explained_without_claiming_a_bypass():
    import backend.main as main
    assert "قيود عنوان IP" in main.explain_extractor_error("Sign in to confirm you're not a bot")
    assert "HTTP 403" in main.explain_extractor_error("HTTP Error 403: Forbidden")
    assert "HTTP 429" in main.explain_extractor_error("HTTP Error 429: Too Many Requests")


def test_diagnostics_capture_selected_youtube_client_without_raw_logs():
    import backend.main as main
    logger = main.YtDlpDiagnostics()
    logger.debug("[youtube] USFKzR93qkM: Downloading visionos player API JSON")
    assert logger.events == ["اختار YouTube عميل المشغل: visionos."]
    assert "USFKzR93qkM" not in " ".join(logger.events)


def test_diagnostics_capture_tiktok_native_challenge_flow():
    import backend.main as main
    logger = main.YtDlpDiagnostics()
    logger.debug("[TikTok] Solving JS challenge using native Python implementation")
    logger.debug("[TikTok] Downloading webpage with challenge cookie")
    assert logger.events == [
        "بدأ TikTok حل تحدي JavaScript بالتنفيذ الأصلي في Python.",
        "أنشأ yt-dlp challenge cookie وأعاد طلب صفحة TikTok.",
    ]


def test_javascript_challenge_failures_are_explained():
    import backend.main as main
    message = main.explain_extractor_error("Failed to solve JS challenge")
    assert "التحقق البرمجي" in message


def test_extractor_retries_only_transient_failures():
    import backend.main as main
    assert main._is_transient_extractor_error("HTTP Error 503: Service Unavailable") is True
    assert main._is_transient_extractor_error("The read operation timed out") is True
    assert main._is_transient_extractor_error("HTTP Error 429: Too Many Requests") is True
    assert main._is_transient_extractor_error("Sign in to confirm you're not a bot") is False
    assert main._is_transient_extractor_error("HTTP Error 403: Forbidden") is False
    assert main._is_transient_extractor_error("Unsupported URL") is False


def test_extraction_retries_temporary_503_once_then_succeeds(monkeypatch):
    import backend.main as main
    calls = {"count": 0}
    monkeypatch.setattr(main, "EXTRACT_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    class FlakyYoutubeDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download=False):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("HTTP Error 503: Service Unavailable")
            return {"id": "ok", "title": "Recovered", "formats": []}

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FlakyYoutubeDL)
    info = main._extract_info_with_retries(
        "https://www.youtube.com/watch?v=fixture", {}, main.YtDlpDiagnostics(), "test123", "youtube"
    )
    assert info["title"] == "Recovered"
    assert calls["count"] == 2


def test_extraction_does_not_retry_youtube_bot_challenge(monkeypatch):
    import backend.main as main
    calls = {"count": 0}
    monkeypatch.setattr(main, "EXTRACT_MAX_ATTEMPTS", 3)

    class BlockedYoutubeDL:
        def __init__(self, opts):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def extract_info(self, url, download=False):
            calls["count"] += 1
            raise RuntimeError("Sign in to confirm you're not a bot")

    monkeypatch.setattr(main.yt_dlp, "YoutubeDL", BlockedYoutubeDL)
    import pytest
    with pytest.raises(RuntimeError):
        main._extract_info_with_retries(
            "https://www.youtube.com/watch?v=fixture", {}, main.YtDlpDiagnostics(), "test456", "youtube"
        )
    assert calls["count"] == 1
