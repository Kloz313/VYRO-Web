def test_youtube_uses_po_token_recommended_client_and_retry_defaults():
    import backend.main as main
    opts = main.common_options(main.ExtractRequest(url="https://www.youtube.com/watch?v=fixture"))
    assert opts["extractor_args"]["youtube"]["player_client"] == ["mweb", "web_safari"]
    assert opts["retries"] == 3
    assert opts["extractor_retries"] == 3
    assert opts["fragment_retries"] == 3
    assert opts["socket_timeout"] == 20


def test_tiktok_uses_chrome_impersonation():
    import backend.main as main
    opts = main.common_options(main.ExtractRequest(url="https://www.tiktok.com/@user/video/123"))
    assert opts["impersonate"] == "chrome"


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
