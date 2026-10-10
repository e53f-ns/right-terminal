# RT 2026-10-10 compatible update

This is a tested PATCH PACKAGE for the running Windows RT installation, not a standalone trading application and not proof of profitability. Do not replace the entire repository with this folder.

## Live-compatible files
- analysis/claude_brain.py: decisive SMC instructions without relaxing gates; HOLD wait-condition journal fields; finite prices/confidence bounds; reject inverted brackets and inadequate structural RR rather than swapping SL/TP or extending TP; typed macro-calendar status in the prompt.
- data/news_feed.py: keep timestamp of last successful fetch on failure; fresh/stale/unknown status; publication/source/available URL provenance.
- data/macro_calendar.py: explicit unavailable vs available_unverified calendar; UTC event times. Does NOT independently verify calendar dates or coverage.

Existing .env/risk/size/leverage/session settings unchanged. Runtime matching source hashes must be checked. Deploy only when LOCAL AND UPSCALE positions are flat. Back up exact old files, compile and run offline tests on Windows, restart only after all tests pass. Do not change an open position's code underneath it.

## Tested locally
- compatible actual parser/source: 12 unittest tests passed. Running same tests against old source fails (7 failures, 1 error), verifying regression coverage.
- news_grounding: 20 unittest tests passed, fake network and clocks.
- strict_plan: 19 tests passed; offline-only module, NOT installed by this package.

## Not provided / blockers
Full strict freshness/structural fact-ID validator needs real collector metadata and executable Upscale quote adapter. Do not activate that separate offline candidate: missing metadata intentionally rejects entries. This package does not prove every market input fresh/authentic, or fix real-position reconciliation/persistence/open-close acknowledgement issues identified in audit.
A recently fetched RSS feed does not imply recently published or true news. Current package queries when:3d (last three days), capped to selected headlines, not complete multi-day coverage. SL/TP requests do not prove server-side protection; read back protective orders separately.

## No secrets / state
No .env, keys, API auth, broker position/account state, trade logs or user account identifiers are included. Synthetic tests are labelled synthetic and do not call models or broker APIs.
