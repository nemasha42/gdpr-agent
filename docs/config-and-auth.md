# Configuration & Auth

> Back to @ARCHITECTURE.md for the system overview.

---

## Environment Variables

All configuration is loaded from a `.env` file at the project root by `config/settings.py` using `python-dotenv`. The `Settings` Pydantic model is instantiated at import time as a module-level singleton (`settings = get_settings()`), meaning a missing `.env` file or missing variable causes a silent empty-string default for most fields.

**Required variables:**

| Variable | Used by | Silent if missing? |
|---|---|---|
| `GOOGLE_CLIENT_ID` | `auth/gmail_oauth.py` | Crashes OAuth flow with an opaque error |
| `GOOGLE_CLIENT_SECRET` | `auth/gmail_oauth.py` | Same |
| `ANTHROPIC_API_KEY` | All LLM call sites | LLM steps silently return `None`; rest of pipeline works |
| `USER_FULL_NAME` | SAR letter templates | Letter body contains empty string — looks broken |
| `USER_EMAIL` | SAR letter body | Letter body contains empty string |
| `USER_ADDRESS_LINE1` / `CITY` / `POSTCODE` / `COUNTRY` | SAR letter templates (postal) | Postal letters have missing address |
| `GDPR_FRAMEWORK` | SAR letter templates | Defaults to `"UK GDPR"` in `get_settings()` |

**`credentials.json`:** Must be present at the project root. `run.py` checks for it at startup and exits with a clear message if absent. Obtained from the Google Cloud Console as an OAuth 2.0 client ID JSON.

**`user_data/tokens/`:** Created automatically on first OAuth run. Tokens are long-lived refresh tokens; they expire only if the user revokes access or the Google project is deleted.

**What breaks silently:** Missing `ANTHROPIC_API_KEY` causes all LLM steps to silently return `None`. A run with an empty API key will successfully scan the inbox, resolve via cache/datarequests/scraper, compose letters, and send them — but companies that require LLM lookup will be silently skipped. The cost summary will show zero LLM calls, which is the only hint that something is wrong.

---

## Auth Subsystem (`auth/gmail_oauth.py`)

Centralised OAuth2 logic. Tokens are stored per-account in `user_data/tokens/{email}_readonly.json` and `{email}_send.json`. Auto-migrates legacy flat `token.json`/`token_send.json` on first run.

**Service cache:** In-memory TTL cache (5 minutes) keyed by `(email, scope, tokens_dir)` avoids redundant disk loads and OAuth refreshes — `_cache_get()`/`_cache_put()`/`clear_service_cache()`. When the email hint is provided and credentials were loaded from disk, the `getProfile` API call is skipped (saves one round-trip per service construction).

**OAuth call logger:** Every `get_gmail_service()`, `get_gmail_send_service()`, and `check_send_token_valid()` call appends a TSV line to `user_data/oauth_calls.log` with a monotonic counter, UTC timestamp, function name, reason (cache_hit/disk_load/browser_auth/etc.), email, and caller location. Thread-safe via `_log_lock`. The log is append-only — never truncate or rotate.

**Batched OAuth:** The `_reextract_missing_links()` helper in `dashboard/blueprints/monitor_bp.py` shares a single `get_gmail_service()` call across all pending re-extractions instead of one per reply.

**Gmail send tokens** (`*_send.json`) can be revoked by Google independently of readonly tokens. Symptoms: letters show "ready" forever, send task completes with 0 sent, no error shown. Diagnosis: run `check_send_token_valid(email)` or visit `/pipeline/reauth-send`. The dashboard pre-flight check in `pipeline_send()` (in `dashboard/blueprints/pipeline_bp.py`) calls `_send_token_valid()` before launching the background task.
