# GDPR Agent — Code Review

This document walks through every file in the project, explaining what each piece of code does
and why it was written that way. It is meant as a complete reading companion: you can open any
source file alongside this doc and follow along line by line.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Entry points — run.py and monitor.py](#2-entry-points)
3. [auth/ — Gmail OAuth](#3-auth--gmail-oauth)
4. [config/ — Settings](#4-config--settings)
5. [scanner/ — Inbox reading and service detection](#5-scanner)
6. [contact_resolver/ — GDPR contact lookup chain](#6-contact_resolver)
7. [letter_engine/ — Composing and sending SARs](#7-letter_engine)
8. [reply_monitor/ — Monitoring replies](#8-reply_monitor)
9. [dashboard/ — Flask web UI](#9-dashboard)
10. [data/ — Persistent databases](#10-data)
11. [tests/](#11-tests)
12. [Known issues and what was fixed](#12-known-issues-and-fixes)

---

## 1. Architecture Overview

The project has a linear pipeline with a separate long-running monitoring loop:

```
Gmail inbox
    │
    ▼
scanner/inbox_reader.py        ← fetches email metadata (headers only)
    │
    ▼
scanner/service_extractor.py   ← classifies senders as HIGH/MEDIUM/LOW confidence services
    │
    ▼
contact_resolver/resolver.py   ← 5-step chain: cache → overrides → datarequests.org → scrape → LLM
    │
    ▼
letter_engine/composer.py      ← fills sar_email.txt or sar_postal.txt template
    │
    ▼
letter_engine/sender.py        ← shows preview, asks Y/N, sends via Gmail API
    │
    ▼
letter_engine/tracker.py       ← appends to user_data/sent_letters.json with thread ID
    │
    ▼
reply_monitor/                 ← polls Gmail threads for replies, classifies, downloads data
    │
    ▼
dashboard/app.py               ← Flask UI showing all this state
```

**Key invariants:**
- `data/companies.json` contains only public contact info — committed to git, never PII
- `user_data/` is gitignored — OAuth tokens, sent log, reply state, downloaded exports
- LLM is always last resort; free sources are attempted first in every lookup
- Every LLM call is recorded in `user_data/cost_log.json` with token counts and cost

---

## 2. Entry Points

### `run.py` — Full pipeline

```python
def main() -> None:
    args = _parse_args()
```
Parses CLI flags first so LLM cap can be set before anything else runs.

```python
    if not (Path(__file__).parent / "credentials.json").exists():
        sys.exit(1)
```
Credentials.json is the Google Cloud OAuth client file — without it nothing works.
Checked before any network calls so the error message is immediate and clear.

```python
    if args.max_llm_calls is not None:
        cost_tracker.set_llm_limit(args.max_llm_calls)
```
Sets a module-level cap inside `cost_tracker`. Every subsequent LLM call checks
`is_llm_limit_reached()` before firing. Setting it early (before resolver runs) ensures
the cap is respected even if the run exits unexpectedly.

```python
    service, email = get_gmail_service(email_hint=args.gmail)
```
`get_gmail_service` handles OAuth: loads cached token, refreshes if expired, runs browser
flow if no token exists. Returns the service object and the authenticated email address.

```python
    emails = fetch_emails(service, max_results=args.max_emails)
    services = extract_services(emails)
```
Stage 1 and 2: fetch headers → detect services. No LLM, no network beyond Gmail.

```python
    _confidence_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    min_rank = _confidence_rank.get(args.min_confidence, 1)
    services = [s for s in services if _confidence_rank[s["confidence"]] >= min_rank]
```
Filter by confidence. Default is LOW (include everything). `--min-confidence MEDIUM`
skips domains seen only in receipts/newsletters.

```python
    resolver = ContactResolver()
    for s in services:
        record = resolver.resolve(domain, name, verbose=False)
        if record:
            letters.append(compose(record))
```
One `ContactResolver` instance is reused across all domains — it caches the GitHub API
directory listing in-memory so it's fetched only once per run.

```python
    for letter in letters:
        result = preview_and_send(letter, dry_run=args.dry_run, scan_email=email)
```
Interactive loop. Each letter gets a formatted preview in the terminal; the user types y
to send or anything else to skip.

```python
    cost_tracker.print_cost_summary()
```
Always printed at the end regardless of how the run ends — shows per-call breakdown and
cumulative spend from the persistent log.

---

**`_parse_args`** defines five flags:
- `--dry-run` — compose and preview but never call Gmail send API
- `--max-emails N` — cap inbox scan (default 500)
- `--min-confidence` — filter services below threshold
- `--gmail EMAIL` — explicit account (skips interactive account picker)
- `--max-llm-calls N` — budget cap for this run (0 blocks all LLM)

---

### `monitor.py` — Reply monitoring CLI

```python
_STATE_PATH = Path(__file__).parent / "user_data" / "reply_state.json"
MAX_ADDRESS_ATTEMPTS = 3
```
State path is module-level so `_handle_bounce_retries` and `_auto_download_data_links`
can call `save_state` independently. `MAX_ADDRESS_ATTEMPTS` is the bounce retry limit.

```python
_TAG_ABBR: dict[str, str] = {
    "AUTO_ACKNOWLEDGE": "ACK",
    ...
}
```
Abbreviation map for the terminal summary table — full tag names are too wide to fit.

**`main()`:**

```python
    sent_log = get_log()
    if not sent_log:
        print("No sent SARs found...")
        return
```
`get_log()` reads `user_data/sent_letters.json`. If nothing was ever sent there's nothing
to monitor.

```python
    states = load_state(email, path=_STATE_PATH)
```
Deserializes per-domain `CompanyState` objects for this account from reply_state.json.
Each domain has one active state and a list of archived past attempts (bounced ones).

```python
    records_by_domain: dict[str, list[dict]] = {}
    for record in sent_log:
        domain = domain_from_sent_record(record)
        records_by_domain.setdefault(domain, []).append(record)
```
Groups sent records by domain because a single domain may have been sent to multiple times
(first address bounced, retried with a different one).

```python
    for domain, records in records_by_domain.items():
        states[domain] = promote_latest_attempt(
            domain=domain,
            sent_records=records,
            existing_state=states.get(domain),
            deadline_fn=deadline_from_sent,
        )
```
`promote_latest_attempt` ensures the active state always reflects the most recent sent
letter. Older attempts are archived into `past_attempts` so their replies are preserved
but don't interfere with the current monitoring cycle.

```python
    for domain, records in records_by_domain.items():
        existing_ids = {r.gmail_message_id for r in state.replies}
        for pa in state.past_attempts:
            for r in pa.get("replies", []):
                existing_ids.add(r["gmail_message_id"])
```
Collects **all** seen message IDs — from both the current and past attempts. This prevents
re-classifying a newsletter that happened to appear in attempt 1 when we start attempt 2.

```python
        new_messages = fetch_replies_for_sar(service, latest_record, existing_ids, ...)
        for msg in new_messages:
            result = classify(msg, api_key=api_key)
            if msg.get("has_attachment"):
                for part in msg.get("parts", []):
                    cat = handle_attachment(service, msg["id"], part, domain)
```
Per-message pipeline: classify → handle attachments → build ReplyRecord → append to state.

**`_handle_bounce_retries`:**

```python
    tried_emails: set[str] = {state.to_email.lower()} if state.to_email else set()
    for pa in state.past_attempts:
        if pa.get("to_email"):
            tried_emails.add(pa["to_email"].lower())
```
Accumulates every email address ever tried for this domain. Passed to `resolver.resolve`
as `exclude_emails` so it doesn't return the same bounced address again.

```python
        new_record = resolver.resolve(domain, state.company_name, exclude_emails=tried_emails)
```
Re-resolves skipping known-bad addresses. May find a DPO email when the privacy@ bounced,
or a webform when no email works.

```python
        # Archive current attempt into past_attempts
        state.past_attempts.append({
            "to_email": state.to_email,
            ...
            "replies": [r.to_dict() for r in state.replies],
        })
        # Reset active attempt to the new address
        state.replies = []
        state.to_email = letter.to_email
        state.gmail_thread_id = thread_id
        state.sar_sent_at = now
        state.deadline = deadline_from_sent(now)
```
Archives the failed attempt and resets the active state for the new address. The deadline
clock restarts from now (new 30-day window).

**`_auto_download_data_links`:**

```python
    urls: list[str] = reply.extracted.get("data_links") or []
    if not urls and reply.extracted.get("data_link"):
        urls = [reply.extracted["data_link"]]
    for url in urls:
        result = download_data_link(url, domain, api_key=api_key or "")
```
Handles both the legacy single `data_link` field and the newer `data_links` list. Iterates
all URLs so multi-file deliveries (e.g. Substack sending two zips) are all downloaded.

---

## 3. auth/ — Gmail OAuth

### `auth/gmail_oauth.py`

```python
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
```
Two separate scopes, two separate tokens. Read-only is used for scanning and reply
monitoring. Send-only is requested at send time so the user sees exactly what's being
granted in the OAuth consent screen.

```python
_TOKENS_DIR = _PROJECT_ROOT / "user_data" / "tokens"
_LEGACY_TOKEN_PATH = _PROJECT_ROOT / "user_data" / "token.json"
```
The legacy flat token paths are from before multi-account support was added.
They are auto-migrated on first run.

**`_safe_email(email: str) -> str`**

```python
    return email.replace("@", "_at_").replace(".", "_")
```
Converts `user@gmail.com` → `user_at_gmail_com` for filesystem-safe filenames.
Used in both the token directory and the reply_state.json key.

**`_token_files_to_emails`**

```python
    name = p.stem.replace("_readonly", "")
    if "_at_" in name:
        local, domain = name.split("_at_", 1)
        emails.append(f"{local}@{domain.replace('_', '.')}")
```
Reverses `_safe_email` to reconstruct human-readable email addresses from filenames.
Shown to the user when multiple accounts are found.

**`_load_creds`**

```python
    if token_path.exists():
        return Credentials.from_authorized_user_file(str(token_path), scopes)
    return None
```
Loads a cached credential file. Returns None if missing (triggers new OAuth flow).

**`_refresh_or_auth`**

```python
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        return creds
    flow = InstalledAppFlow.from_client_secrets_file(...)
    return flow.run_local_server(port=0, **kwargs)
```
First tries to silently refresh using the refresh token (no browser needed). If that fails
or there's no token, runs the full browser-based OAuth consent flow.

**`get_gmail_service`** — the main function called from run.py and monitor.py.

```python
    # Auto-migrate legacy flat token
    existing_readonly = list(tokens_dir.glob("*_readonly.json"))
    if _LEGACY_TOKEN_PATH.exists() and not existing_readonly:
        ...
        new_path = tokens_dir / f"{_safe_email(email)}_readonly.json"
        new_path.write_text(_LEGACY_TOKEN_PATH.read_text())
        _LEGACY_TOKEN_PATH.unlink()
```
If there's a legacy flat token but no per-account tokens yet: load it, call Gmail API to
get the email address, save under the new name, delete the old file. One-time migration.

```python
    if len(existing_readonly) > 1:
        known = _token_files_to_emails(existing_readonly)
        print("Multiple Gmail accounts found:")
        choice = input("\n  Which account to scan? Enter number or full email: ").strip()
```
Multi-account interactive picker. The user can type `1` or the full email. The `--gmail`
flag bypasses this entirely.

```python
    # Save under the correct email-based filename
    final_path = tokens_dir / f"{_safe_email(email)}_readonly.json"
    if token_path != final_path:
        token_path.unlink(missing_ok=True)
    final_path.write_text(creds.to_json())
```
After authentication we know the actual email address (queried from Gmail API). If the
token was saved under a temporary name (e.g. `_pending_readonly.json`) it's renamed to
the correct account-based filename.

---

## 4. config/ — Settings

### `config/settings.py`

```python
class Settings(BaseSettings):
    google_client_id: str = ""
    google_client_secret: str = ""
    anthropic_api_key: str = ""
    user_full_name: str = ""
    user_email: str = ""
    user_address_line1: str = ""
    user_address_city: str = ""
    user_address_postcode: str = ""
    user_address_country: str = ""
    gdpr_framework: str = "UK GDPR"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
```
Pydantic `BaseSettings` reads from environment variables and .env file. All fields have
defaults so the app doesn't crash on import if .env is missing — it just can't send emails.

The module-level `settings` singleton is imported directly by `composer.py`, `llm_searcher.py`,
and anywhere else config is needed.

---

## 5. scanner/

### `scanner/inbox_reader.py`

Two public functions with different use cases:

**`fetch_emails`** — simple paginator for the initial full-inbox scan:

```python
    while len(emails) < max_results:
        remaining = max_results - len(emails)
        list_response = service.users().messages().list(
            userId="me",
            maxResults=min(_LIST_PAGE_SIZE, remaining),
            pageToken=page_token,
            fields="messages(id),nextPageToken",  # minimal fields — just IDs
        ).execute()
```
`fields="messages(id),nextPageToken"` is a Gmail API partial-response selector. It tells
the API to return only message IDs in the list response, which is much smaller than the
default (which includes labels, sizes, etc.). The actual headers are fetched per-message.

```python
        detail = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
```
`format="metadata"` tells Gmail not to return the body (which could be megabytes). Only
three headers are requested. This keeps the scan fast and respects the `gmail.readonly`
scope (no body access needed or wanted).

**`fetch_new_emails`** — incremental scanner used by the pipeline dashboard:

```python
        page_new_ids = [msg["id"] for msg in raw if msg["id"] not in known_ids]
        if not page_new_ids:
            break  # Early-stop: hit the already-scanned frontier
```
Because Gmail returns messages newest-first, as soon as an entire page is all known IDs
we know we've reached already-scanned mail. No need to page further.

```python
        if progress_callback:
            progress_callback(len(new_emails))
```
Progress callback for the dashboard's live task polling (pipeline step 1 shows a progress
counter updating in real time).

---

### `scanner/service_extractor.py`

```python
_HIGH_SIGNALS: tuple[str, ...] = (
    "thanks for signing up",
    "confirm your account",
    "verify your email",
    ...
)
_MEDIUM_SIGNALS: tuple[str, ...] = (
    "your account",
    "your order",
    "sign-in",
    ...
)
```
Signal phrases for subject-line classification. HIGH = clear account creation / verification.
MEDIUM = transactional but not necessarily sign-up. Everything else is LOW.

```python
def _classify(subject: str) -> tuple[str, str]:
    lower = subject.lower()
    for phrase in _HIGH_SIGNALS:
        if phrase in lower:
            return "HIGH", phrase
    for phrase in _MEDIUM_SIGNALS:
        if phrase in lower:
            return "MEDIUM", phrase
    return "LOW", "transactional"
```
Simple substring match — no regex, no LLM. Returns both the confidence level and the
matching phrase (stored in the service record for debugging).

```python
def _extract_domain(sender: str) -> str | None:
    _, addr = parseaddr(sender)  # handles "Name <email@domain.com>" format
    if "@" not in addr:
        return None
    return addr.split("@", 1)[1].lower().strip()
```
`parseaddr` is Python's standard RFC 5322 parser — correctly handles display names with
special characters, quoted strings, etc.

**`extract_services`:**

```python
    seen: dict[str, dict[str, str]] = {}  # canonical domain → record

    for email in emails:
        domain = _extract_domain(email.get("sender", ""))
        canon = canonical_domain(domain)  # e.g. "mail.spotify.com" → "spotify.com"

        if canon not in seen:
            seen[canon] = {
                "domain": canon,
                "company_name_raw": normalize_domain(domain),
                "confidence": confidence,
                ...
            }
        else:
            record = seen[canon]
            if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[record["confidence"]]:
                record["confidence"] = confidence  # upgrade if stronger evidence found
            # Expand the observed date window
            if date_iso < record["first_seen"]: record["first_seen"] = date_iso
            if date_iso > record["last_seen"]:  record["last_seen"] = date_iso
```
Deduplication: all emails from the same canonical domain (e.g. `mail.spotify.com`,
`news.spotify.com`, `spotify.com`) are merged into one record. The confidence is the
maximum seen across all emails from that domain. Date window tracks oldest and newest
observed email.

```python
    results.sort(key=lambda r: (-_CONFIDENCE_RANK[r["confidence"]], r["domain"]))
```
Sort by confidence descending, then alphabetically. HIGH services shown first in preview.

---

### `scanner/company_normalizer.py`

```python
_SUBDOMAIN_PREFIXES: tuple[str, ...] = (
    "communications.",
    "notifications.",
    "newsletter.",
    ...
    "em.",
    "e.",
)
```
Ordered longest-first so `"no-reply."` matches before a hypothetical `"reply."`. These are
all noise subdomains that don't indicate the company — stripping them reveals the root domain.

```python
def _strip_subdomains(domain: str) -> str:
    for prefix in _SUBDOMAIN_PREFIXES:
        if domain.startswith(prefix):
            return _strip_subdomains(domain[len(prefix):])  # recursive
    return domain
```
Recursive so nested noise is stripped. `"em.news.spotify.com"` → `"news.spotify.com"` →
`"spotify.com"`.

```python
_COMPANY_GROUPS: dict[str, list[str]] = {
    "google.com": ["youtube.com", "gmail.com", "googlemail.com", ...],
    "facebook.com": ["facebookmail.com", "instagram.com"],
    "microsoft.com": ["linkedin.com", "outlook.com", "hotmail.com"],
    ...
}
_CANONICAL: dict[str, str] = {
    alias: canonical
    for canonical, aliases in _COMPANY_GROUPS.items()
    for alias in aliases
}
```
`_CANONICAL` is a flat reverse map built at import time: `{"youtube.com": "google.com",
"instagram.com": "facebook.com", ...}`. Used by `canonical_domain()` for O(1) lookup.

These groupings reflect the real GDPR data controller structure — YouTube, Gmail, and
Google Groups are all under `google.com`'s GDPR contact.

```python
def _root_to_name(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_TLDS:
        name_part = parts[-3]  # "amazon.co.uk" → "amazon"
    elif len(parts) >= 2:
        name_part = parts[-2]  # "spotify.com" → "spotify"
    return name_part.capitalize()
```
Two-part TLD handling: for `amazon.co.uk`, `parts[-2:]` is `["co", "uk"]` which is in
`_TWO_PART_TLDS`, so we take `parts[-3]` = `"amazon"`.

---

## 6. contact_resolver/

### `contact_resolver/models.py`

```python
class Contact(BaseModel):
    dpo_email: str = ""
    privacy_email: str = ""
    gdpr_portal_url: str = ""
    postal_address: PostalAddress = Field(default_factory=PostalAddress)
    preferred_method: Literal["email", "portal", "postal"] = "email"
```
`preferred_method` is the single field that controls which template and dispatch path is
used. It's set by the resolver based on what the source says is the preferred contact method.

`dpo_email` is the Data Protection Officer email (legally required under GDPR for some
organisations). `privacy_email` is the general privacy contact (more common). In practice
we prefer `privacy_email` when composing, but both are stored.

```python
class CompanyRecord(BaseModel):
    source: Literal["datarequests", "llm_search", "user_manual", "dataowners_override", "privacy_scrape"]
    source_confidence: Literal["high", "medium", "low"]
    last_verified: str  # ISO date: YYYY-MM-DD
```
`source` tells us how the record was found (affects staleness TTL). `source_confidence`
affects whether the LLM result is accepted (low-confidence LLM results are dropped).
`last_verified` is compared against today to decide if the record is stale.

```python
class CompaniesDB(BaseModel):
    meta: DBMeta = Field(default_factory=DBMeta)
    companies: dict[str, CompanyRecord] = Field(default_factory=dict)
```
The full `data/companies.json` file is a single `CompaniesDB`. Pydantic handles
serialization and validation. `CompaniesDB.model_validate_json(text)` deserializes and
validates all nested fields in one call.

---

### `contact_resolver/resolver.py`

```python
_STALENESS_DAYS: dict[str, int] = {
    "datarequests": 180,      # open-source DB — changes slowly
    "dataowners_override": 180,
    "privacy_scrape": 90,     # websites change more often
    "llm_search": 90,
    "user_manual": 365,       # user-entered — trust longer
}
```
Different sources age at different rates. LLM results are treated as less reliable than
the open-source datarequests.org database, so they're refreshed twice as often.

**`ContactResolver.__init__`:**

```python
    self._http_get = http_get or http.get
    self._privacy_scrape = privacy_scrape or privacy_page_scraper.scrape_privacy_page
    self._llm_search = llm_search or llm_searcher.search_company
```
Dependency injection: in production, the real functions are used. In tests, mocks are
injected without patching globals. This is why tests don't need `unittest.mock.patch`.

```python
    self._dir_listing: list[dict] | None = None
```
In-memory cache for the GitHub API directory listing (3000+ files). Fetched once per
`ContactResolver` instance, which is one per run. Avoids hitting the rate limit (60
requests/hour unauthenticated) by fetching the listing once and scanning it in memory.

**`resolve` method — the 5-step chain:**

```python
    _excl = {e.lower() for e in (exclude_emails or [])}

    def _excluded(rec: CompanyRecord | None) -> bool:
        if not rec or not _excl:
            return False
        email = rec.contact.privacy_email or rec.contact.dpo_email
        return bool(email) and email.lower() in _excl
```
The `exclude_emails` set is used during bounce retries. Every step checks `_excluded(record)`
before returning. If the record's email is in the exclusion set, the step is treated as a miss
and the chain continues to the next step.

**Step 1: Cache**

```python
        existing = db.companies.get(domain)
        if existing and not self._is_stale(existing):
            if _excluded(existing):
                pass  # Fall through to re-search all sources
            else:
                cost_tracker.record_resolver_result("cache")
                return existing
```
Cache hit returns immediately. If the cached email is excluded (bounced), the cache hit
is intentionally ignored and the chain continues searching for an alternative address.

**Step 2: dataowners_overrides**

```python
    def _search_dataowners(self, domain: str) -> CompanyRecord | None:
        entry = data.get(domain)
        record = CompanyRecord.model_validate(entry)
        record.last_verified = date.today().isoformat()  # reset TTL
        return record
```
`last_verified` is refreshed every time the record is loaded from the override file. This
prevents the `dataowners_override` TTL from ever expiring — these records are manually
maintained and should always be fresh. Without this fix, they would be re-searched after
180 days even though nothing changed (this was P1 tech debt, now fixed).

**Step 3: datarequests.org**

```python
    def _fetch_dir_listing(self) -> list[dict]:
        resp = self._http_get(_GITHUB_API_DIR_URL, timeout=15)
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None and int(remaining) < 10:
            print(f"[resolver] Warning: GitHub API rate limit nearly exhausted ...")
        self._dir_listing = resp.json()
        return self._dir_listing
```
The GitHub API returns rate limit info in headers. When fewer than 10 requests remain
(out of 60/hour unauthenticated), a warning is printed. Adding `GITHUB_TOKEN` to .env
raises the limit to 5000/hour.

```python
def _find_candidate_files(file_listing, domain, company_name):
    domain_root = domain.split(".")[0].lower()   # "spotify.com" → "spotify"
    company_words = [w for w in re.split(r"[^a-z0-9]+", company_name.lower()) if len(w) >= 3]

    for file_info in file_listing:
        slug = name[:-5].lower()  # strip ".json"
        if domain_root in slug or any(word in slug for word in company_words):
            matches.append(file_info)
    return matches[:_MAX_CANDIDATE_FETCHES]
```
Two matching strategies: domain root (fast, direct) and company name words (handles cases
where the slug uses the full name e.g. `"interactive-brokers.json"` for `interactivebrokers.com`).
Words shorter than 3 chars are excluded to avoid spurious matches.

```python
            if domain in entry.get("runs", []):
                return _map_datarequests_entry(entry)
```
The datarequests.org format includes a `runs` array listing all domains the record applies
to. This is the authoritative check — a file named `spotify.json` might apply to
`spotify.com`, `spotify.co.uk`, etc. We verify our domain is in that list.

**Step 4: Privacy page scraper** (see `privacy_page_scraper.py` below)

**Step 5: LLM**

```python
        if cost_tracker.is_llm_limit_reached():
            cost_tracker.record_resolver_result(None)
            return None
```
Checks the cap before making any API call. If the cap is reached, the domain is recorded
as "not found" rather than waiting or erroring.

```python
        record = self._llm_search(company_name, domain)
        if record:
            if record.source_confidence == "low":
                cost_tracker.record_resolver_result(None)
                return None
```
Low-confidence LLM results are silently dropped even if the call succeeded. This happens
when Claude can find the company but not its GDPR contact specifically (returns
`source_confidence: "low"`).

---

### `contact_resolver/privacy_page_scraper.py`

(Not shown in full, but key points from CLAUDE.md and review:)

Tries `/privacy-policy`, `/privacy`, `/legal/privacy`, `/gdpr` in order. Uses simple
`http.get` with a 10-second timeout. Applies regex to extract email addresses:

```python
# Requires 2-char TLD to avoid matching "privacy@localhost" etc. (P3 fix)
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
```
The 2-char TLD requirement is a P3 fix. Without it, strings like `privacy@localhost` or
`data@127` would be accepted.

Returns `source_confidence: "medium"` — scraping is less reliable than datarequests.org
but more reliable than LLM.

---

### `contact_resolver/llm_searcher.py`

```python
_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
```
Haiku is used because it's cheapest (~$0.025/call including web search). Max 1024 tokens
is enough for a JSON response — the real token cost is in the web search tool use.

```python
_SYSTEM_PROMPT = """\
You are a GDPR contact data extractor. Find the company's GDPR/privacy \
contacts and reply with ONLY a valid JSON object — no prose, no markdown fences:
{"company_name":"","legal_entity_name":"","source_confidence":"medium",...}
confidence: high=official contacts clearly stated; \
medium=contacts found indirectly; low=no usable GDPR contact found."""
```
The system prompt forces JSON-only output and defines the confidence levels. Including the
JSON schema directly in the prompt ensures the model knows the exact field names and types.

```python
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
```
`web_search_20250305` is Anthropic's built-in web search tool. `max_uses=2` was raised
from 1 (P1 fix) — allows the model to do an initial search and then a follow-up if the
first result doesn't contain the email address.

**`_extract_json`:**

```python
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    idx = text.find("{")
    if idx == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, idx)
```
`json.JSONDecoder().raw_decode(text, idx)` is the P1 fix. The old code used a greedy
regex `re.search(r"\{.*\}", text, re.S)` which would fail if the model included any
text after the JSON object (e.g. a trailing note). `raw_decode` stops at the first
complete JSON object, ignoring anything after it.

**`_validate_and_build`:**

```python
    has_contact = any(contact_data.get(f, "").strip() for f in _CONTACT_FIELDS)
    if not has_contact:
        confidence = "low"
    if confidence == "low":
        return None
```
Even if the model says `source_confidence: "medium"`, if it failed to return any usable
contact field (dpo_email, privacy_email, or gdpr_portal_url), confidence is overridden to
low and the result is discarded. This prevents returning records with no contact info.

---

### `contact_resolver/cost_tracker.py`

```python
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-sonnet-4-6":         (3.00 / 1_000_000, 15.0 / 1_000_000),
}
_DEFAULT_PRICING: tuple[float, float] = (3.00 / 1_000_000, 15.0 / 1_000_000)
```
Pricing is per-token (input, output) in USD. The default is Sonnet pricing — conservatively
high so unknown models don't undercount. Haiku is 3.75x cheaper on input.

```python
def record_llm_call(...) -> None:
    in_price, out_price = _PRICING.get(model, _DEFAULT_PRICING)
    cost = (input_tokens * in_price) + (output_tokens * out_price)
    call = _LLMCall(...)
    _session_log.append(call)
    _persist(call, purpose=purpose)
```
Called after every LLM call, with actual token counts from `response.usage`. The `purpose`
field distinguishes resolver calls from classifier calls in the persistent log.

**`_persist`:**

```python
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return  # never pollute the real log with test fixtures
    try:
        existing = load_persistent_log()
        existing.append({...})
        if len(existing) > _MAX_LOG_ENTRIES:
            existing = existing[-_MAX_LOG_ENTRIES:]  # rotation: keep most recent (P1 fix)
        _COST_LOG_PATH.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        print(f"[cost_tracker] Warning: cost log not saved: {e}", flush=True)  # P1 fix
```
Three important behaviors:
1. Test guard: `PYTEST_CURRENT_TEST` is set by pytest during test runs — prevents test
   fixtures from polluting the real cost log.
2. Rotation: when the log exceeds 1000 entries, the oldest are dropped. Without this
   (P1 fix), the log would grow without bound.
3. Exception printing: previously, `_persist` swallowed all exceptions silently (P1 fix).
   Now it prints a warning so the user knows if the log couldn't be saved.

---

## 7. letter_engine/

### `letter_engine/composer.py`

```python
def compose(record: CompanyRecord) -> SARLetter:
    method = record.contact.preferred_method
    template_name = "sar_postal.txt" if method == "postal" else "sar_email.txt"
    template = (_TEMPLATES_DIR / template_name).read_text()
```
Template selection is purely based on `preferred_method`. Portal method uses the email
template (the body is copied into the portal form by the user).

```python
    vars: dict[str, str] = {
        "user_full_name": settings.user_full_name,
        ...
        "company_name": record.company_name,
        "company_address": _format_company_address(record),
        "date": date.today().strftime("%d %B %Y"),
    }
    body = template.format(**vars)
```
Simple Python string `.format(**vars)` substitution. The templates use `{user_full_name}`,
`{company_name}`, etc. as placeholders.

```python
    to_email = record.contact.privacy_email or record.contact.dpo_email
```
`privacy_email` is preferred over `dpo_email` for the To: address. Both are stored in case
one bounces and the resolver needs to retry with the other.

---

### `letter_engine/sender.py`

```python
def _dispatch_email(letter: SARLetter, scan_email: str) -> tuple[str, str]:
    msg = MIMEText(letter.body, 'plain', 'utf-8')  # utf-8 explicit (P1 fix)
    msg["to"] = letter.to_email
    msg["subject"] = letter.subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return result.get("id", ""), result.get("threadId", "")
```
`MIMEText(body, 'plain', 'utf-8')` is the P1 fix — the old code defaulted to US-ASCII,
which would fail or mangle non-ASCII characters in names or addresses. Specifying UTF-8
ensures the email is correctly encoded for international characters.

`base64.urlsafe_b64encode(msg.as_bytes())` converts the MIME message to the format Gmail
API expects for the `raw` field.

`threadId` in the response is stored in the sent log — this is the thread ID used by the
reply monitor to find all replies via `threads.get()`.

```python
def send_letter(letter: SARLetter, scan_email: str) -> tuple[bool, str, str]:
    """Send without an interactive Y/N prompt."""
    if letter.method == "email":
        msg_id, thread_id = _dispatch_email(letter, scan_email)
        tracker.record_sent(letter)
        return bool(msg_id), msg_id, thread_id
    # portal / postal — record as sent; user handles submission manually
    tracker.record_sent(letter)
    return True, "", ""
```
`send_letter` is the non-interactive version used by bounce-retry logic in monitor.py.
Portal and postal letters are always "sent" (recorded and returned True) because the
user must handle submission themselves — there's no thread_id to track.

---

### `letter_engine/tracker.py`

Appends to `user_data/sent_letters.json`. Each record includes:
- `company_name`, `to_email`, `subject`
- `sent_at` (ISO datetime)
- `method` (`email`, `portal`, `postal`)
- `gmail_thread_id` (empty for portal/postal)
- `gmail_message_id`

This is the source of truth for the reply monitor — it reads this file to know which
companies to check and which Gmail threads to look in.

---

## 8. reply_monitor/

### `reply_monitor/models.py`

```python
@dataclass
class CompanyState:
    domain: str
    company_name: str
    sar_sent_at: str          # most recent attempt
    to_email: str             # most recent attempt
    gmail_thread_id: str      # most recent attempt thread
    deadline: str             # ISO date — 30 days from sent
    replies: list[ReplyRecord] = field(default_factory=list)
    past_attempts: list[dict] = field(default_factory=list)
    address_exhausted: bool = False
```
`past_attempts` is a list of dicts (not dataclasses) to keep the JSON serialization simple.
Each entry mirrors the top-level state structure: `{to_email, gmail_thread_id, sar_sent_at,
deadline, replies: [...]}`.

`address_exhausted` is set when MAX_ADDRESS_ATTEMPTS bounces have occurred with no
alternative address found. It's a terminal state — the company is shown as ADDRESS_NOT_FOUND
in the dashboard.

```python
@dataclass
class ReplyRecord:
    tags: list[str]
    extracted: dict  # reference_number, confirmation_url, data_link, data_links, portal_url, deadline_extension_days
    llm_used: bool
    has_attachment: bool
    attachment_catalog: dict | None
```
`data_links` (plural) was added as a P2 fix. Previously only `data_link` (singular) was
stored — companies like Substack send multiple zip files per reply, and only the first was
being tracked.

---

### `reply_monitor/fetcher.py`

**Thread-based (authoritative path):**

```python
    thread = service.users().threads().get(
        userId="me",
        id=thread_id,
        format="full",
    ).execute()

    for msg in thread.get("messages", []):
        if msg["id"] in existing_ids:
            continue
        from_header = _get_header(msg, "From")
        if user_email and user_email.lower() in from_header.lower():
            continue  # skip own outgoing messages
```
`threads.get` returns all messages in the Gmail thread — both the sent SAR and all replies.
We skip messages we've already processed (`existing_ids`) and skip the user's own sent
message (the original SAR).

**Search-based (legacy fallback):**

```python
    queries = [
        f"from:{to_email}{date_filter}",   # exact address
        f"from:{domain}{date_filter}",      # whole domain
    ]
```
Two searches: exact address first (most targeted), then whole domain (catches replies from
`privacy@company.com` when we sent to `dpo@company.com`). The `after:` date filter is
set to one day before sent_at so same-day replies are never excluded.

**`_extract_body`:**

```python
    def _strip_html(text: str) -> str:
        # Preserve href URLs before stripping tags
        text = re.sub(
            r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>',
            lambda m: f" {m.group(1)} ",  # insert URL as plain text
            text, flags=re.I,
        )
        text = re.sub(r"<[^>]+>", " ", text)  # strip all tags
```
The key insight: download URLs in HTML emails live in `<a href="...">` tags. Stripping
tags naively would lose the URLs. This code inserts the href URL as plain text before
stripping, so the URL extractors in `classifier.py` can find it.

```python
        # Strip zero-width / invisible Unicode chars
        t = re.sub(r'[\u00ad\u034f\u115f\u1160...]', '', t)
        # Deduplicate consecutive identical lines
        deduped: list[str] = []
        prev = object()
        for line in lines:
            if line != prev:
                deduped.append(line)
                prev = line
        # Collapse 3+ blank lines to 1
        result = re.sub(r'\n{3,}', '\n\n', '\n'.join(deduped))
```
Three normalisation steps after HTML stripping: (1) remove invisible chars that marketing
emails use as tracking pixels / spacers; (2) deduplicate repeated lines (some HTML-to-text
conversions produce duplicate URL lines); (3) collapse excessive blank lines.

---

### `reply_monitor/classifier.py`

The most complex module in the project. Three passes.

**Pass 0: NON_GDPR pre-pass**

```python
_NON_GDPR_FROM_LOCAL = re.compile(
    r"^(news|digest|jobs|marketing|career|noreply-jobs|community|newsletters?)$", re.I)
_NON_GDPR_FROM_LOCAL_WEAK = re.compile(r"^alerts$", re.I)
```
Strong signals (+2 each): email local part is unambiguously a marketing sender.
Weak signal (+1): `alerts@` — could legitimately send GDPR data breach notifications, so
it's scored lower and requires additional signals to be filtered.

```python
def _is_non_gdpr(from_addr, subject, snippet) -> bool:
    signals = 0
    if local and _NON_GDPR_FROM_LOCAL.match(local):    signals += 2
    elif local and _NON_GDPR_FROM_LOCAL_WEAK.match(local): signals += 1
    if _display and _NON_GDPR_DISPLAY_NAME.search(_display): signals += 1
    if _NON_GDPR_SUBJECT.search(subject):              signals += 1
    if _NON_GDPR_SNIPPET.search(snippet):              signals += 1
    if _NON_GDPR_ZERO_WIDTH.search(snippet):           signals += 1
    return signals >= 2
```
Requires at least 2 independent signals. A single `noreply@` is insufficient — that's
also used by companies for legitimate GDPR communications. Only when combined with
newsletter subject line / unsubscribe snippet / zero-width chars is it filtered.

Zero-width chars (`\u200b`, `\u200c`, etc.) are a very reliable newsletter fingerprint —
they're used as invisible email-client spacers and tracking elements. No legitimate GDPR
response would ever contain them.

**Pass 1: Regex tagging**

```python
_RULES: list[tuple[str, list[tuple[str, re.Pattern]]]] = [
    ("BOUNCE_PERMANENT", [
        ("from",    re.compile(r"mailer-daemon@|postmaster@", re.I)),
        ("subject", re.compile(r"delivery status notification|undeliverable|...", re.I)),
        ("snippet", re.compile(r"550[\s.]|5\.1\.1|email account does not exist|...", re.I)),
    ]),
    ...
]
```
Each tag has a list of `(field, pattern)` pairs. A tag fires if any of its patterns match
the corresponding field. The `break` inside the matching loop means only one pattern needs
to match per tag.

```python
    if "BOUNCE_TEMPORARY" in tags and "BOUNCE_PERMANENT" in tags:
        tags.remove("BOUNCE_PERMANENT")
```
If both bounce types fire (4xx code plus general delivery failure language), temporary
takes precedence. A 4xx response means the server accepted the message but there's a
transient issue — not a permanent address failure.

```python
    _is_bounce = "BOUNCE_PERMANENT" in tags or "BOUNCE_TEMPORARY" in tags
    if has_attachment and "DATA_PROVIDED_LINK" not in tags and not _is_bounce:
        tags.append("DATA_PROVIDED_ATTACHMENT")
```
`DATA_PROVIDED_ATTACHMENT` is inferred from `has_attachment=True`. The bounce exclusion is
important: mailer-daemon bounce messages sometimes have system attachments (error reports)
that must not be mistaken for data exports.

**Pass 1.5: URL extraction** (called `_extract`)

```python
    # Pass A: Zendesk expanded format "filename.zip\nURL"
    for m in _RE_ZENDESK_ATTACHMENT_A.finditer(full_text):
        url = _clean_url(m.group(1))
        if url and url not in data_links:
            data_links.append(url)

    if not data_links:
        # Pass B: generic patterns (Glassdoor, path-based, token params)
        for m in _RE_DOWNLOAD_URL.finditer(full_text):
            ...

    if not data_links:
        # Pass C: Zendesk compact inline "Attachment(s): filename.zip - URL"
        ...

    if not data_links:
        # Pass D: context-aware — any URL within 400 chars of data/export keywords
        m = _RE_EXPORT_CONTEXT_URL.search(full_text)
```
Four extraction passes in order of specificity. Pass A is tried first because the Zendesk
expanded format is unambiguous (filename immediately followed by URL on the next line).
Pass D is most permissive — it finds any URL near data-related keywords — and is used only
as a last resort. Each pass falls through only if the previous one found nothing.

**Body-level tag promotion:**

```python
    if body and not _is_bounce:
        if "WRONG_CHANNEL" not in tags and _RE_BODY_WRONG_CHANNEL.search(body):
            tags.append("WRONG_CHANNEL")
```
Self-service deflection responses (e.g. Google saying "your data is already available
through our online tools") often have polite subject lines and unrevealing snippets.
The actual deflection language is in the body. This pass catches what regex on subject/
snippet misses.

**Link-first promotion:**

```python
    if (
        extracted.get("data_link")
        and _is_data_url(extracted["data_link"])
        and "DATA_PROVIDED_LINK" not in tags
        and not _is_bounce
    ):
        tags.append("DATA_PROVIDED_LINK")
```
`_is_data_url` checks URL patterns: file extension (`.zip`, `.json`), path indicators
(`/download/`, `/export/`, `/attachments/token/`), query params (`token=`, `export_id=`).
Only promotes to `DATA_PROVIDED_LINK` if the URL looks like an actual data file — not a
generic privacy policy link that happened to appear near the word "data".

**Pass 2: LLM fallback**

```python
    _LLM_TRIGGER_STATES = {frozenset(), frozenset({"AUTO_ACKNOWLEDGE"})}

    if frozenset(tags) in _LLM_TRIGGER_STATES and api_key:
        cache_key = (from_addr, subject)
        if cache_key in _llm_cache:
            llm_result = _llm_cache[cache_key]
        else:
            llm_result = _llm_classify(message, api_key)
            _llm_cache[cache_key] = llm_result
```
LLM is triggered only when Pass 1 produced no tags or only `AUTO_ACKNOWLEDGE` (which alone
is not informative enough). The `_llm_cache` (P2 fix) prevents re-classifying identical
auto-replies — companies often send the same acknowledgment template for every ticket.

---

### `reply_monitor/state_manager.py`

```python
_TERMINAL_TAGS = frozenset({
    "DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT", "DATA_PROVIDED_PORTAL",
    "FULFILLED_DELETION", "REQUEST_DENIED", "NO_DATA_HELD", "NOT_GDPR_APPLICABLE",
})
_ACTION_TAGS = frozenset({
    "CONFIRMATION_REQUIRED", "IDENTITY_REQUIRED", "MORE_INFO_REQUIRED", "WRONG_CHANNEL",
})
_ACK_TAGS = frozenset({"AUTO_ACKNOWLEDGE", "REQUEST_ACCEPTED", "IN_PROGRESS"})
```
Three disjoint tag groups drive the status computation. Terminal tags mean the SAR is
resolved (data received, denied, or not applicable). Action tags mean the user must do
something. Ack tags mean the company acknowledged but hasn't delivered yet.

**`compute_status`:**

```python
    if state.address_exhausted:
        return "ADDRESS_NOT_FOUND"

    tags_seen: set[str] = set()
    for reply in state.replies:
        if "NON_GDPR" in reply.tags:
            continue  # newsletters invisible to status
        tags_seen.update(reply.tags)
```
NON_GDPR replies are excluded from status computation — a job alert from the same domain
must not affect the SAR status.

```python
    if "BOUNCE_PERMANENT" in tags_seen:
        last_bounce = max(r.received_at for r in state.replies if "BOUNCE_PERMANENT" in r.tags ...)
        last_non_bounce = max(r.received_at for r in state.replies if "BOUNCE_PERMANENT" not in r.tags ...)
        if last_bounce >= last_non_bounce:
            return "BOUNCED"
        tags_seen.discard("BOUNCE_PERMANENT")  # bounce superseded by later reply
```
A bounce is only BOUNCED status if the most recent reply is the bounce. If a human reply
arrived after the bounce (e.g. the company forwarded the bounced email and someone
responded manually), the bounce is superseded and BOUNCED is cleared.

```python
    try:
        deadline = date.fromisoformat(deadline_str)
        if date.today() > deadline and not (tags_seen & _TERMINAL_TAGS):
            return "OVERDUE"
    except (ValueError, AttributeError):
        pass
```
OVERDUE is only returned if no terminal tag is present. A company that replied with data
after the deadline is COMPLETED, not OVERDUE.

**`days_remaining`:**

```python
def days_remaining(sar_sent_at: str | None) -> int:
    if not sar_sent_at:
        return _SAR_DEADLINE_DAYS  # P2 fix: None-safe
    try:
        sent = _parse_iso_date(sar_sent_at)
        deadline = sent + timedelta(days=_SAR_DEADLINE_DAYS)
        return (deadline - date.today()).days
    except Exception:
        return _SAR_DEADLINE_DAYS
```
The P2 fix: previously crashed with `TypeError` when `sar_sent_at` was None (happens for
portal/postal records that have no thread_id and sometimes no sent timestamp).

**`promote_latest_attempt`:**

```python
    sorted_records = sorted(sent_records, key=lambda r: r.get("sent_at", ""))
    latest = sorted_records[-1]
    older = sorted_records[:-1]
```
When multiple letters were sent to the same domain (bounce retries), sort chronologically
and treat the most recent as the active attempt.

```python
    thread_replies: dict[str, list[dict]] = {}
    if existing_state:
        active_thread = existing_state.gmail_thread_id
        if active_thread:
            thread_replies[active_thread] = [r.to_dict() for r in existing_state.replies]
        for pa in existing_state.past_attempts:
            t = pa.get("gmail_thread_id", "")
            if t:
                thread_replies[t] = pa.get("replies", [])
```
Builds a map of `thread_id → replies` from the existing state (both active and archived).
This preserves replies across promotions — if the active attempt is promoted to past, its
replies travel with it.

**`load_state` / `save_state`:**

```python
def _safe_email(email: str) -> str:
    return email.replace("@", "_at_").replace(".", "_")
```
Used as the JSON key in reply_state.json. `"user@gmail.com"` → `"user_at_gmail_com"`.
This allows multi-account state in a single file with no collisions.

```python
    # Load existing data for other accounts
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except ...:
            pass
    existing[key] = {domain: state.to_dict() for domain, state in states.items()}
    path.write_text(json.dumps(existing, indent=2))
```
Save merges into the existing file to preserve other accounts' state. Only the current
account's key is overwritten.

---

## 9. dashboard/

### `dashboard/app.py`

**Context processor:**

```python
@app.context_processor
def _inject_globals():
    from flask import request as _req
    path = _req.path
    if path.startswith("/pipeline"):
        active_tab = "pipeline"
    elif path.startswith("/costs"):
        active_tab = "costs"
    else:
        active_tab = "dashboard"
    return {
        "status_colour": _STATUS_COLOUR,
        "tag_colour": _TAG_COLOUR,
        "active_tab": active_tab,
    }
```
`active_tab` is injected into every template automatically. `base.html` uses it to bold
the current tab in the navbar. The context processor runs on every request.

**`_build_card(domain, state, status) -> dict`:**

Converts a `CompanyState` into a template-friendly dict including:
- `status`, `status_colour`
- `days_remaining`, `deadline_pct` (for the progress bar)
- `tags` (deduplicated across all replies)
- `data_links` (extracted from DATA_PROVIDED_LINK replies for quick access)
- `has_data` (whether any attachment was received and cataloged)

**`/` route — dashboard:**

```python
@app.route("/")
def dashboard():
    account = request.args.get("account", "")
    ...
    states = load_state(account, path=_STATE_PATH)
    cards = []
    for domain, state in states.items():
        status = compute_status(state)
        cards.append(_build_card(domain, state, status))
    cards.sort(key=lambda c: -status_sort_key(c["status"]))
```
Sorted by urgency: OVERDUE first, then ACTION_REQUIRED, BOUNCED, DENIED, COMPLETED, etc.

**`/company/<domain>` — thread view:**

Shows the full reply history for one company. For each reply, fetches the email body on
demand via Gmail API (bodies are not stored in state — only headers and snippets are).

**`/costs` route:**

```python
@app.route("/costs")
def costs():
    records = cost_tracker.load_persistent_log()

    model_totals: dict[str, dict] = {}
    for r in records:
        m = r.get("model", "unknown")
        model_totals[m]["calls"] += 1
        model_totals[m]["cost_usd"] += r.get("cost_usd", 0.0)

    source_totals: dict[str, dict] = {}
    for r in records:
        src = r.get("purpose") or r.get("source") or "unknown"
        source_totals[src]["calls"] += 1

    resolver_calls = [r for r in records if "contact" in (r.get("purpose") or "")]
    avg_resolver = sum(r["cost_usd"] for r in resolver_calls) / len(resolver_calls)
```
Loads the persistent cost log and computes aggregates. `purpose` takes precedence over
`source` for the grouping (newer records have `purpose` set; older records only have
`source`). The averages are passed to the template as defaults for the calculator.

**Pipeline routes** (`/pipeline`, `/pipeline/scan`, `/pipeline/resolve`, etc.):

The pipeline is a multi-step background job system. Each step (scan, resolve, send) is
run as a background thread tracked by a `task_id`. The frontend polls `/api/task/<task_id>`
for status and progress. This allows the dashboard to show a live progress indicator
without blocking the Flask server.

```python
@app.route("/pipeline/scan", methods=["POST"])
def pipeline_scan():
    account = request.form.get("account", "")
    task_id = str(uuid.uuid4())
    thread = threading.Thread(target=_do_scan, args=(task_id, service, account, known_ids))
    thread.daemon = True
    thread.start()
    return jsonify({"task_id": task_id})
```

```python
@app.route("/api/task/<task_id>")
def api_task(task_id: str):
    task = _tasks.get(task_id, {})
    return jsonify(task)
```
`_tasks` is a module-level dict: `{task_id: {status, progress, result, error}}`. Updated
by the background thread, read by the polling frontend.

---

### `dashboard/templates/base.html`

```html
<nav class="navbar navbar-light bg-white border-bottom mb-4 px-4">
    <a class="navbar-brand" href="/">🔒 GDPR SAR Monitor</a>
    <div class="d-flex gap-3 align-items-center">
      <a class="text-muted small text-decoration-none {% if active_tab == 'dashboard' %}fw-bold text-dark{% endif %}" href="/">Dashboard</a>
      <a class="text-muted small text-decoration-none {% if active_tab == 'pipeline' %}fw-bold text-dark{% endif %}" href="/pipeline">Pipeline</a>
      <a class="text-muted small text-decoration-none {% if active_tab == 'costs' %}fw-bold text-dark{% endif %}" href="/costs">Costs</a>
    </div>
    {% block nav_extra %}{% endblock %}
</nav>
```
The `active_tab` variable (injected by the context processor) drives `fw-bold text-dark`
on the current tab. `nav_extra` is overridden by the pipeline template to inject account
selector buttons.

---

### `dashboard/templates/costs.html`

Three sections:

**Summary cards** — three Bootstrap cards side by side:
1. Cost breakdown by model (Haiku vs Sonnet calls and spend)
2. Cost breakdown by purpose (resolver lookups vs classifier fallbacks vs schema analysis)
3. Actual average costs per call type (used as calculator defaults)

**Calculator** — JavaScript-based:
```javascript
function calcCosts() {
    const resolverCalls = Math.ceil(companies * (1 - cacheRate));
    const resolverTotal = resolverCalls * resolverCost;
    const classifierCalls = Math.ceil(replies * classRate);
    const total = resolverTotal + classifierTotal + schemaTotal;
    document.getElementById('cr-total').textContent = '$' + total.toFixed(4);
    document.getElementById('calc-result').style.display = 'block';
}
```
No server round-trip — all calculation is client-side. The inputs are pre-filled with
actual averages from the cost log so the estimate is grounded in real data.

**Call history table** — last 200 records (most recent first), showing timestamp, company,
model, purpose, token counts, cost, and whether the LLM found a contact.

---

## 10. data/

### `data/companies.json`

```json
{
  "meta": {"version": "1.0", "last_updated": "2026-03-19", "total_companies": 247},
  "companies": {
    "spotify.com": {
      "company_name": "Spotify",
      "source": "datarequests",
      "source_confidence": "high",
      "last_verified": "2026-03-19",
      "contact": {
        "privacy_email": "privacy@spotify.com",
        "preferred_method": "email"
      },
      ...
    }
  }
}
```
Committed to git because it contains only publicly available contact information.
The Pydantic model `CompaniesDB` validates the entire file on load.

### `data/dataowners_overrides.json`

Hand-curated records for major platforms (Google, Microsoft, Meta, etc.) where the GDPR
contact is well-known and unlikely to change. These records take precedence over datarequests.org
and are never re-fetched (except for TTL refresh).

---

## 11. tests/

All tests in `tests/unit/` follow one rule: **no real network, Gmail, or Anthropic calls**.

**Dependency injection pattern:**

```python
# In ContactResolver.__init__:
self._http_get = http_get or http.get
self._privacy_scrape = privacy_scrape or privacy_page_scraper.scrape_privacy_page
self._llm_search = llm_search or llm_searcher.search_company

# In tests:
resolver = ContactResolver(
    db_path=tmp_path / "companies.json",
    http_get=lambda url, **kw: mock_response,
    privacy_scrape=lambda domain, name, **kw: None,
    llm_search=lambda name, domain: None,
)
```
Tests inject mock callables without patching globals. This is safer (no leakage between
tests) and clearer (what's mocked is visible in the test code).

**Mock Anthropic response pattern:**

```python
mock_response = MagicMock()
mock_response.usage.input_tokens = 1000   # must be int, not MagicMock
mock_response.usage.output_tokens = 200   # cost_tracker does arithmetic on these
```
`cost_tracker.record_llm_call` does `(input_tokens * in_price)` — if `input_tokens` is a
MagicMock auto-attribute the arithmetic silently returns another MagicMock and cost is
recorded as 0 (no error, no warning). Tests must explicitly set these as integers.

**Classifier tests:**

```python
result = classify({
    "from": "mailer-daemon@googlemail.com",
    "subject": "Delivery Status Notification",
    "snippet": "550 5.1.1 email account does not exist",
    "has_attachment": False,
    "body": "",
})
assert "BOUNCE_PERMANENT" in result.tags
```
Direct function call with a dict — no Gmail API involved. Tests cover each tag type,
edge cases (both bounce types), multi-tag scenarios, and NON_GDPR filtering.

---

## 12. Known Issues and Fixes

All P1 and P2 issues from the tech debt review have been fixed. Here's what was changed
and why each fix matters:

| Fix | File | What changed | Why it mattered |
|-----|------|-------------|----------------|
| P1: Greedy regex | `llm_searcher.py` | `re.search(r"\{.*\}")` → `json.JSONDecoder().raw_decode()` | Old code failed whenever Claude added a trailing comment after the JSON |
| P1: max_uses=1 | `llm_searcher.py` | Raised to 2 | Claude often needs a second search to find the email after an initial discovery search |
| P1: ASCII email | `sender.py` | `MIMEText(body)` → `MIMEText(body, 'plain', 'utf-8')` | Non-ASCII in company names or addresses caused `UnicodeEncodeError` |
| P1: Silent persist | `cost_tracker.py` | `except: pass` → `except Exception as e: print(...)` | Disk full / permissions errors were invisible |
| P1: Unbounded log | `cost_tracker.py` | Rotate at 1000 entries | Log grew forever with no bound |
| P1: GitHub rate limit | `resolver.py` | Warn when `X-RateLimit-Remaining < 10` | Silent failure after 60 lookups in an hour |
| P1: Stale override | `resolver.py` | Refresh `last_verified` on dataowners load | Without this, curated records expired after 180 days and triggered LLM re-lookups |
| P2: alerts@ score | `classifier.py` | +2 → +1 | alerts@ can legitimately send data breach GDPR notices; +2 was filtering them |
| P2: LLM dedup | `classifier.py` | Added `_llm_cache` keyed on `(from_addr, subject)` | Same auto-reply template triggered separate LLM calls for every company |
| P2: days_remaining None | `state_manager.py` | None-safe return of 30 | Portal/postal records without sent_at crashed the dashboard |
| P2: Playwright error | `link_downloader.py` | Print install hint | `playwright install` not run → opaque `FileNotFoundError` |
| P2: Notification shell | `classifier.py` | Link-first promotion + `_is_data_url()` guard | Emails with download URL in body but non-standard subject were missed |
| P2: Zendesk attachments | `classifier.py` | `_RE_ZENDESK_ATTACHMENT_A/B` | Zendesk sends `filename.zip\nURL` blocks not matched by generic patterns |
| P2: Self-service deflection | `classifier.py` | `_RE_BODY_WRONG_CHANNEL` body-level pass | Google's "data available via online tools" response was in body, not snippet |
| P2: Multi-file delivery | `classifier.py` + `monitor.py` | `data_links` list; iterate all URLs | Substack sends 2+ zips; only first was downloaded |
| P3: Schema tokens | `schema_builder.py` | max_tokens 2048 → 4096 | Large export manifests were truncated mid-schema |
| P3: Schema context | `schema_builder.py` | Dynamic per-file truncation | 60KB+ context crashed the API call |
| P3: Email regex TLD | `privacy_page_scraper.py` | Require 2-char TLD | `privacy@localhost` was being accepted |

**Still open:**

- `dashboard/app.py` — zero test coverage (tracked in CLAUDE.md)
- GitHub rate limit for large runs — add `GITHUB_TOKEN` to .env to raise from 60 to
  5000 requests/hour

---

*Generated 2026-03-19. Covers all files present at that date.*
