# VYRO Web — consolidated update validation

Base source commit: `cfccac17dbdfa084c678724acfc51e9744f5c6f3`

## Included changes

- Browser-initiated download request with a server-side wait for the job result.
- Single-file attachment delivery and ZIP packaging for multi-file playlists.
- Fallback download link retained in the progress area.
- Service Worker cache bumped to `vyro-web-v3`.
- Advanced playlist-item selection removed; the basic full-playlist option remains.
- Core video quality, resolution, codec, audio tracks, audio format/quality, subtitle controls, and download/copy buttons remain.
- YouTube player clients adjusted to `mweb` and `web_safari`; BGUtil PO Token server/plugin presence is separately reported by health.
- TikTok Chrome impersonation retained; connection timeout and retry counts increased for transient failures.
- Safe, high-level extractor diagnostics and clearer 403/429/login-required error messages.

## Automated validation

- Python compile check: passed.
- JavaScript syntax checks (`app.js`, `service-worker.js`): passed.
- New focused tests: 8 passed (4 download delivery + 4 platform diagnostics/configuration).
- Full test suite on updated source: 30 passed, 6 failed.
- Full test suite on the untouched uploaded baseline: 22 passed, the same 6 tests failed.
- Therefore, the update introduced no additional test failures; the six failures are pre-existing test/source mismatches concerning old auth/concurrency/proxy/rate-limit/IPv4/cookie expectations.
- `git diff --check`: passed.

## Not verified in this environment

- No live YouTube, TikTok, Instagram, Facebook, or X extraction/download was performed.
- No Docker image build was run.
- No deployment to Blitz was performed.
- The PO Token plugin/server presence checks do not prove that YouTube generated or accepted a token for a particular video.
- Browser-initiated iframe delivery is best-effort; some browser policies may still require the fallback link.

## Platform limitations

- YouTube may require login/verification based on the content or server IP. A PO Token does not guarantee bypassing IP-based bot checks.
- TikTok/Instagram/Facebook/X 403 responses may be caused by platform protection, regional restrictions, datacenter IP reputation, or expired media URLs. Generic retries cannot remove those restrictions.
- No cookies, residential proxy, account session, or third-party bypass service has been added. Visitors are not forced to log in or upload account data.

## Deployment validation still required

After the owner deploys this source, check `/api/health` for both `youtube_pot_provider: true` and `youtube_pot_plugin: true`. Then test public URLs from the actual Blitz container and inspect the new safe diagnostic indicators in job errors. A successful metadata extraction is not proof that the media download completed.
