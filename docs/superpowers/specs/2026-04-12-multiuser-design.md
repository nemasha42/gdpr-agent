# Multiuser Support for GDPR Agent

**Date:** 2026-04-12
**Status:** Design approved, pending implementation
**Approach:** Option A — Directory-per-user with shared OAuth app

## Goals

1. Lightweight onboarding for 2-3 friends (minimal friction)
2. Progressive scan: try with 500 emails before committing to full scan
3. Strict user isolation — each user gets own tokens, letters, reply state
4. Invite-only access via shareable link (admin-generated)
5. Shared OAuth app — one GCP project, users just authorize it
6. Multi-mailbox per user with tab-based switching

## Non-Goals

- Public-facing deployment (localhost only)
- Password-based auth
- User-visible admin panel for browsing other users' data
- Subprocessor wiki or schema wiki (flagged for future)

---

## 1. Auth & Session Layer

### User Registry

`user_data/users.json`:
```json
{
  "nemasha@gmail.com": {
    "name": "Nemasha",
    "role": "admin",
    "created_at": "2026-04-12T10:00:00Z",
    "invite_token": null
  },
  "friend@gmail.com": {
    "name": "Alice",
    "role": "user",
    "created_at": "2026-04-12T15:30:00Z",
    "invite_token": "abc123..."
  }
}
```

Roles: `admin` (can invite, sees `/costs`, filesystem debug access) and `user` (standard access).

### Invite Flow

1. Admin visits `/admin/invite` → enters friend's email → server generates signed token via `itsdangerous.URLSafeTimedSerializer` (salt: `'invite'`)
2. Admin shares link: `http://localhost:5001/join/<token>`
3. Friend clicks link → token validated → token encodes the invited email
4. If user already exists in `users.json`: redirect to `/login` (returning user)
5. If new user: onboarding page → enter name → click "Connect Gmail" → web OAuth flow
6. OAuth callback confirms Gmail address matches invited email → user created in `users.json` → session cookie set → redirect to dashboard
7. Session persists via `remember=True` (30-day cookie). After expiry, user visits any page → redirected to `/login` → "Sign in with Google" → OAuth re-auth → session restored (no invite token needed for returning users).

### Session Management

- `Flask-Login` with `UserMixin` — user loader reads `users.json` (atomic read/write with `fcntl.flock` to prevent corruption from concurrent requests)
- `@app.before_request` sets `g.user` with: `email`, `name`, `role`, `data_dir` (Path)
- `@login_required` on all routes except: `/join/<token>`, `/auth/callback`, `/static/`
- No passwords — identity established via Google OAuth. Re-login = re-do OAuth.

### Flask Config

- `SECRET_KEY` generated once, stored in `user_data/secret_key.txt` (gitignored)
- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_SAMESITE = 'Lax'`
- `REMEMBER_COOKIE_DURATION = timedelta(days=30)`

---

## 2. Data Isolation & Directory Structure

### New Layout

```
user_data/
  users.json                              # user registry
  secret_key.txt                          # Flask SECRET_KEY
  <safe_email>/                           # per-user directory
    tokens/
      <safe_email>_readonly.json
      <safe_email>_send.json
      <other_mailbox>_readonly.json       # additional mailboxes
      <other_mailbox>_send.json
    sent_letters.json
    reply_state.json
    subprocessor_requests.json
    subprocessor_reply_state.json
    scan_state.json
    received/
      <domain>/
data/
  companies.json                          # shared global
  dataowners_overrides.json               # shared global
  cost_log.json                           # shared global, admin-visible only
```

### Path Resolution

```python
USER_DATA_ROOT = Path("user_data")

def user_data_dir(user_email: str) -> Path:
    safe = _safe_email(user_email)
    path = (USER_DATA_ROOT / safe).resolve()
    if not path.is_relative_to(USER_DATA_ROOT.resolve()):
        raise ValueError(f"Path traversal attempt: {user_email}")
    return path
```

All modules that currently hardcode `Path("user_data/...")` will accept a `data_dir: Path` parameter or read from `g.user.data_dir` in Flask context.

### What Stays Global

| File | Reason |
|------|--------|
| `data/companies.json` | Public GDPR contacts — shared cache benefits all users |
| `data/dataowners_overrides.json` | Admin-curated overrides |
| `cost_log.json` | Operational metric, admin-only |

### Migration Plan (Existing Data)

1. Create `user_data/<your_safe_email>/` directory
2. Move `user_data/tokens/` → `user_data/<your_safe_email>/tokens/`
3. Extract your account's entries from the global `reply_state.json` into per-user file
4. Move `sent_letters.json` as-is (all existing records are yours)
5. Same for `subprocessor_requests.json`, `subprocessor_reply_state.json`, `scan_state.json`
6. Move `user_data/received/` → `user_data/<your_safe_email>/received/`
7. Create `user_data/users.json` with your account as admin
8. Old global files deleted after verified migration

---

## 3. OAuth Flow (Web-Based)

### Current State

`auth/gmail_oauth.py` uses `InstalledAppFlow.run_local_server()` — CLI-oriented, opens browser tab with temp HTTP server for callback.

### New Web Flow

1. User clicks "Connect Gmail" on onboarding (or "Add mailbox" later)
2. Server creates `google_auth_oauthlib.flow.Flow`:
   - `redirect_uri = url_for('auth.oauth_callback', _external=True)`
   - `prompt='consent'` (forces refresh token)
   - `access_type='offline'`
3. Onboarding requests `gmail.readonly` only (lower friction)
4. Google redirects to `/auth/callback` → exchange code for tokens → save to `g.user.data_dir / tokens/`
5. Redirect to dashboard (onboarding) or settings (add mailbox)

### Incremental Scopes

- `gmail.readonly` granted at onboarding — enough to scan and browse
- `gmail.send` requested on first letter send: "To send letters, we need additional Gmail permission" → second OAuth flow with send scope
- Matches "try before committing" goal

### CLI Flow Preserved

- `InstalledAppFlow` path stays for `run.py` and `monitor.py`
- New `WebOAuthFlow` class wraps `Flow` for dashboard
- Both write tokens to same per-user `tokens/` directory
- `get_gmail_service(email, data_dir)` gains `data_dir` parameter

### GCP Setup Requirements

- Add `http://localhost:5001/auth/callback` as authorized redirect URI
- Move consent screen from "Testing" to "In Production" (avoids 7-day token expiry)
- No Google verification needed for <100 users (users see "unverified app" clickthrough)

---

## 4. Progressive Scan & Onboarding UX

### Onboarding Steps

1. **Name** — "What's your full name?" (single text field, needed for SAR letters)
2. **Connect Gmail** — "Connect your Gmail to scan for services" → OAuth (readonly)
3. **First scan** — starts automatically after OAuth callback

### Progressive Scan Flow

1. After OAuth, redirect to `/scan` with live progress
2. Backend fetches 500 most recent email headers via Gmail API
3. SSE stream pushes: progress counter + discovered service cards in real-time
4. On batch complete: "Found 34 services in your last 500 emails"
5. Two buttons:
   - **"Scan 500 more"** — resumes via stored `nextPageToken`
   - **"Scan entire mailbox"** — shows estimate first: "Your mailbox has ~12,400 emails. This may take a few minutes."
6. User selects companies (checkboxes) → proceeds to letter review

### Scan State (per-user, per-mailbox)

Stored in `<user_dir>/scan_state.json`, keyed by mailbox safe-email:
```json
{
  "nemasha_at_gmail_com": {
    "next_page_token": "abc123...",
    "emails_scanned": 500,
    "total_estimate": 12400,
    "services_found": ["spotify.com", "amazon.co.uk"],
    "scan_started_at": "2026-04-12T10:00:00Z",
    "status": "paused"
  }
}
```

### SSE Implementation

- Flask SSE via `queue.Queue` per listener (zero extra dependencies)
- HTMX `sse-connect="/scan/stream"` on frontend — no custom JS
- Scan runs in background thread, pushes events
- Page reload: loads current state from `scan_state.json`, reconnects SSE if still running

### Mailbox Size Estimate

- `users.getProfile()` returns `messagesTotal` — one API call, instant
- Displayed before "Scan entire mailbox" button

---

## 5. Multi-Mailbox UX

### Adding a Mailbox

Settings page → "Add another mailbox" → OAuth flow → new tokens saved under same user's directory.

### Tab Bar

```
┌──────────┬─────────────────────┬───────────────────┬─────┐
│  All (47) │ nemasha@gmail (32)  │ work@gmail (15)   │  +  │
└──────────┴─────────────────────┴───────────────────┴─────┘
```

- Sits below main nav, above company cards
- **"All"** is default — merges companies from all mailboxes, small badge per card showing source mailbox
- Individual tabs filter to one mailbox
- **"+"** → add mailbox flow
- State in URL: `?mailbox=all` or `?mailbox=work_at_gmail_com`
- Replaces current account dropdown

### Interaction with Existing Account Logic

- Current `?account=EMAIL` parameter continues under the hood
- Tab bar is visual wrapper that sets this parameter
- `_load_all_states(account)` unchanged — called per mailbox, or per-each for "All"
- "All" view: deduplicates companies by domain (same company from two mailboxes = one card with both badges)

### Pipeline Per Mailbox

- Each mailbox has independent scan state, sent letters, reply threads
- Pipeline page respects active tab selection
- "From" address = active mailbox (no cross-mailbox sending)

---

## 6. Shared Resources & Future Collaboration

### Shared Now

- `data/companies.json` — resolver cache, benefits all users
- `data/dataowners_overrides.json` — admin-curated

### Shared Later (Not Built Now)

- **Subprocessor tree** — merge discovered subprocessor relationships into shared `data/subprocessor_graph.json`
- **Data schema wiki** — merge `schema_builder.py` output into shared `data/schemas/<domain>.json`

### Admin-Only

- `cost_log.json` — visible at `/costs`, admin role only
- `/admin/invite`, `/admin/users` routes

### User Self-Service

- `GET /settings/export` — zip of user's data directory
- `POST /settings/delete-account` — confirmation → delete directory + remove from `users.json` + revoke session

---

## 7. Files Changed (Summary)

### New Files

| File | Purpose |
|------|---------|
| `auth/web_oauth.py` | Web-based OAuth flow (Flow wrapper) |
| `dashboard/auth_routes.py` | Blueprint: `/join`, `/auth/callback`, `/login`, `/logout` |
| `dashboard/admin_routes.py` | Blueprint: `/admin/invite`, `/admin/users` |
| `dashboard/middleware.py` | `@app.before_request` user loading, `@login_required` decorator |
| `dashboard/templates/onboarding.html` | Name + Connect Gmail + first scan |
| `dashboard/templates/scan.html` | Progressive scan with SSE progress |
| `user_data/users.json` | User registry |
| `user_data/secret_key.txt` | Flask SECRET_KEY |
| `scripts/migrate_to_multiuser.py` | One-time migration of existing single-user data |

### Modified Files

| File | Change |
|------|--------|
| `auth/gmail_oauth.py` | Add `data_dir` parameter to `get_gmail_service()`, keep CLI flow |
| `config/settings.py` | Remove `USER_*` fields from global settings (move to per-user) |
| `dashboard/app.py` | Add Flask-Login init, register blueprints, `g.user` injection, mailbox tabs |
| `dashboard/templates/base.html` | Add mailbox tab bar, login/logout nav |
| `dashboard/templates/index.html` | Mailbox badges on cards |
| `letter_engine/composer.py` | Accept user identity (name, email, address) as parameter instead of `settings` |
| `letter_engine/sender.py` | Accept `data_dir` for sent_letters path |
| `letter_engine/tracker.py` | Accept `data_dir` parameter instead of hardcoded path |
| `reply_monitor/state_manager.py` | Accept `data_dir` parameter for state file paths |
| `reply_monitor/monitor.py` | Accept `data_dir` parameter |
| `scanner/inbox_reader.py` | Accept `data_dir` for token lookup |
| `run.py` | Resolve `data_dir` from user email, pass through pipeline |
| `monitor.py` | Resolve `data_dir` from user email |

### Unchanged

- `data/companies.json` — structure unchanged, stays global
- `contact_resolver/` — no user-specific state, reads/writes global `companies.json`
- `reply_monitor/classifier.py` — stateless, no changes needed
- `reply_monitor/fetcher.py` — already receives Gmail service object, no path changes
- `templates/sar_email.txt`, `templates/sar_postal.txt` — template format unchanged
