# Pipeline Stages — Detailed Documentation

> Back to @ARCHITECTURE.md for the system overview and module map.

---

## Stage 1 — Scanner

**What it does:** Identifies which companies have received personal data by examining Gmail message headers, deduplicated to a single canonical entry per company.

**How it works:** `inbox_reader.py` fetches up to 500 email headers using the Gmail API's `messages.list` + `messages.get(format="metadata")` combination. It never fetches message bodies — only `From`, `Subject`, and `Date` headers — which keeps the OAuth scope to `gmail.readonly` and avoids handling potentially sensitive content. The returned list is handed to `service_extractor.py`, which runs a series of regex heuristics against the sender address and subject line to classify each email as a HIGH, MEDIUM, or LOW confidence service signal. HIGH confidence means the email is clearly a transactional/welcome message (e.g. subject matches "welcome to", "your order", "invoice"); MEDIUM covers newsletters and notifications; LOW covers everything else. The extractor deduplicates senders by canonical domain (via `company_normalizer.canonical_domain()`), keeping the highest confidence level seen and recording `first_seen`/`last_seen` date ranges.

`company_normalizer.py` handles the non-obvious mapping from raw email domains to canonical registrable domains. It strips known noise subdomains (`noreply.`, `accounts.`, `support.`, `mail.`, `notifications.`, etc.) and handles two-part country-code TLDs (`.co.uk`, `.com.au`) to avoid treating `amazon.co.uk` and `amazon.com` as different companies. It also maintains a hardcoded alias table: `t.co` → Twitter/X, `youtube.com` → google.com, `instagram.com` → facebook.com, `ibkr.com` → interactivebrokers.com, and others. The result is a clean list of `{domain, company_name_raw, confidence, first_seen, last_seen}` dicts.

**Key assumptions:** The Gmail inbox belongs to a single person who has consented to scanning. Email headers alone are sufficient to identify services — body content is never read. The `From` header's domain is a reliable proxy for the company's identity.

**Known limitations:** The confidence classification is entirely heuristic and has no feedback loop — a transactional email from an unusual sender (e.g. a legal firm's automated system) may be misclassified as LOW. The alias table is manually maintained and will miss new alias relationships. Subdomains are stripped by a fixed allowlist, so an unusual subdomain like `eu-mail.example.com` would yield `eu-mail.example.com` as the canonical domain rather than `example.com`.

---

## Stage 2 — Resolver

**What it does:** For each discovered company domain, finds the correct GDPR/privacy contact details (email address, portal URL, or postal address) using a five-step lookup chain that escalates from free to paid.

**How it works:** `resolver.py` implements `ContactResolver.resolve(domain, company_name)` as a chain of five steps, each of which returns immediately on success. This prevents unnecessary API calls.

**Step 1 — Local cache** (`data/companies.json`): A JSON file committed to the repo that stores `CompanyRecord` objects keyed by domain. If a record exists and its `last_verified` date is within the TTL for its source (180 days for datarequests/overrides, 90 days for scraper/LLM results, 365 days for manually entered records), it is returned immediately. On cache miss or stale record, the chain continues. Every successful resolution from any downstream step writes back to this cache.

**Step 2 — Dataowners overrides** (`data/dataowners_overrides.json`): A hand-curated file of high-confidence records for major services (Google, Meta, Apple, Spotify, etc.) where the correct contact is well-known. When a match is found, `last_verified` is set to today before returning, ensuring the cached copy stays fresh within its 180-day TTL. This prevents an infinite stale-loop where a static date in the overrides file would cause the cache to expire and re-read the same static date on every run.

**Step 3 — datarequests.org via GitHub API**: The [datarequests.org](https://www.datarequests.org) project maintains an open-source database of GDPR contacts at `https://github.com/datenanfragen/data/tree/master/companies`. The resolver fetches the directory listing via GitHub's API (unauthenticated, 60 req/hour limit) and caches the listing in memory for the session. It then searches for JSON files whose slugs contain the domain's second-level label or any word from the company name. For each candidate file it downloads and checks whether the target domain appears in the entry's `runs` array — this avoids false matches where a company slug matches by name but not by domain. A successful match is converted from datarequests' format to a `CompanyRecord`.

**Step 4 — Privacy page scraper**: `privacy_page_scraper.py` attempts four well-known URLs in order: `/privacy-policy`, `/privacy`, `/legal/privacy`, `/gdpr`. On the first HTTP 200 response, it strips HTML tags and applies regex patterns to find email addresses whose local part is one of `privacy`, `dpo`, `gdpr`, `legal`, `dataprotection`, `data-protection`, or `dataprivacy` (with a required minimum 2-character TLD and exclusion of internal hostnames like `localhost`, `internal`, `staging`). It separately looks for URLs containing GDPR-specific path segments (`/dsar`, `/data-request`, `/privacy-request`, `/gdpr-request`, `/subject-access`). A record is returned with `source_confidence="medium"` if either an email or portal URL is found.

**Step 5 — LLM web search**: `llm_searcher.py` calls Claude Haiku with the `web_search_20250305` tool (`max_uses=2`) and a system prompt that instructs it to find GDPR contacts and reply with only a JSON object matching the `CompanyRecord` schema. The response is parsed with `json.JSONDecoder().raw_decode()`, starting from the first `{` in the response text — this handles cases where the model emits preamble before the JSON. Only records with at least one populated contact field (`dpo_email`, `privacy_email`, or `gdpr_portal_url`) and a non-low confidence rating are returned. If the LLM call limit set via `--max-llm-calls` is reached, this step is skipped and the resolver returns `None`.

**Key assumptions:** Domains are registrable domains (e.g. `spotify.com`), not full hostnames. The company name passed in is a raw display name that may not match the legal entity name. The GitHub API is available and unauthenticated access is sufficient (60 req/hour).

**Known limitations:** The GitHub API rate limit (60 unauthenticated requests/hour) will be exhausted mid-run at 500+ companies. The resolver warns when `X-RateLimit-Remaining < 10` but does not pause or retry. Adding `GITHUB_TOKEN` to `.env` would raise this to 5,000/hour but is not currently implemented. The scraper cannot handle JavaScript-rendered pages or Cloudflare-protected privacy pages. The 5-step chain is sequential, not concurrent — a 500-company run with many LLM calls will be slow.

---

## Stage 3 — Letter Engine

**What it does:** Composes a formal SAR letter for each resolved company and dispatches it via Gmail, a web portal instruction, or a postal instruction, after showing the user a preview and asking for confirmation.

**How it works:** `composer.py` reads either `templates/sar_email.txt` or `templates/sar_postal.txt` based on `CompanyRecord.contact.preferred_method` and performs simple string substitution of `{company_name}`, `{user_full_name}`, `{user_address_*}`, `{date}`, and `{gdpr_framework}` placeholders. The templates are Article 15 GDPR requests that enumerate the standard set of rights: categories of data, processing purposes, recipients, retention periods, and data origins. The result is a `SARLetter` dataclass.

`sender.py` prints a formatted preview box and prompts the user with `[y/N]`. On approval, it dispatches based on method: for `email`, it constructs a `MIMEText` message with explicit UTF-8 encoding (to avoid corruption of non-ASCII characters in names or addresses) and sends via the Gmail API's `messages.send` endpoint, capturing `message_id` and `thread_id` for later reply monitoring. For `portal` and `postal`, it prints instructions for the user to complete manually. All three paths call `tracker.record_sent()` to append the letter to `user_data/sent_letters.json`.

The `--dry-run` flag skips actual dispatch but still records the user's `y` decision. This is used for testing the pipeline without spending Gmail quota.

**Key assumptions:** The user reviews each letter before sending. There is no batch-send or auto-send mode (the pipeline is explicitly interactive). Portal and postal letters cannot be tracked for replies because no Gmail thread ID is generated.

**Known limitations:** Portal and postal letters enter the monitoring system with an empty `gmail_thread_id`, which means the monitor cannot poll for replies. These companies will stay in `PENDING` status indefinitely unless portal automation succeeds. The Y/N prompt makes fully automated runs impossible — this is intentional but limits large-scale use.

For portal automation details, see @docs/portal-automation.md.

### Subprocessor Disclosure Request Path

A parallel composition path for subprocessor disclosure letters: `compose_subprocessor_request(record, *, to_email_override="") → SARLetter | None` uses `templates/subprocessor_request_email.txt` / `subprocessor_request_postal.txt` (cites CJEU C-154/21 and EDPB Opinion 22/2024, requests AI providers, data brokers, advertising platforms by name). Logged to `user_data/subprocessor_requests.json` via `record_subprocessor_request(letter, domain)`.

`to_email_override` is a fallback email (typically the SAR `to_email`) used when the record has no privacy/dpo email — the dashboard passes `sar_state.to_email` so disclosure requests can be sent even when only a generic contact address is known. Returns `None` if no usable email contact exists and method is not postal.

**Critical invariant:** `send_letter(record=False)` must be used for all SP disclosure request sends. SP letters are tracked in `subprocessor_requests.json`, never `sent_letters.json`. If SP letters leak into `sent_letters.json`, `promote_latest_attempt()` will corrupt SAR state (wrong thread_id, lost replies).

---

## Stage 4 — Monitor

For detailed monitor documentation (classifier, fetcher, state_manager, link_downloader, schema_builder), see @docs/reply-monitor.md.

---

## Stage 5 — Subprocessors

**What it does:** Discovers third-party data processors (subprocessors) for each SAR company by scraping public subprocessor pages and falling back to LLM web search.

**How it works:** `contact_resolver/subprocessor_fetcher.py` implements `fetch_subprocessors(company_name, domain)` returning a `SubprocessorRecord`.

Strategy: (1) Scrape known paths (`/sub-processors`, `/vendors`, etc.) with `requests` for both bare and `www.` domain. (2) `_extract_page_content()` extracts `<table>` elements first (subprocessor pages nearly always use tables), then falls back to a keyword-anchored text window, then full stripped text — a page must yield ≥500 chars of plain text (`_MIN_PLAIN_TEXT`) to be considered non-empty. (3) Playwright fallback for JS-rendered SPAs. (4) Claude Haiku call — `web_search` tool only attached when no scraped content was found (saves output tokens for JSON).

The background task (`_fetch_all_subprocessors`) in `dashboard/app.py` only skips a domain if it has `fetch_status="ok"` within the 30-day TTL — `not_found` and `error` records are always retried.

`write_subprocessors(domain, record)` persists a `SubprocessorRecord` into `data/companies.json`. If the domain has no existing entry it creates a minimal stub (`source="llm_search"`, `source_confidence="low"`) so subprocessors are stored for all SAR domains regardless of whether contact resolution succeeded. Never skip-on-missing — without stubs, subprocessors silently don't persist for domains only in reply_state.json.

**Known limitations:** Subprocessor pages are frequently behind logins or behind JavaScript frameworks that Playwright cannot always render. The 30-day TTL means stale subprocessor data can persist. The `web_search` tool adds cost when scraping fails.

---

## LLM Call Sites in Pipeline Stages

### Call site 1: `contact_resolver/llm_searcher.py` — `_extract_with_websearch()`

**Why LLM is used here:** Steps 1–4 (cache, overrides, datarequests.org, privacy page scraper) fail for companies with non-standard privacy page URLs, JavaScript-rendered pages, Cloudflare protection, or simply no public GDPR contact information. A human searching the web would typically find the contact by reading the company's privacy policy or a help article. The LLM replicates this — it can navigate multiple pages via the `web_search_20250305` tool, handle redirects, and extract structured information from prose.

**Prompt strategy:** Structured extraction. The system prompt forces JSON-only output matching the `CompanyRecord` schema exactly. The user message is simply `"GDPR contacts for {company_name} ({domain})"`. The model is given up to 2 web search calls (`max_uses=2`) to find the answer. The system prompt defines what `confidence: high/medium/low` means in context.

**Fallback:** If the API call raises `anthropic.APIError`, the function returns `None`. If the response contains no parseable JSON or the parsed record has no contact fields, `_validate_and_build()` returns `None`. If the model returns `source_confidence: "low"`, the resolver treats it as a miss and returns `None`. In all cases, the company is skipped (no letter is sent).

**Cost:** ~$0.025 per call (roughly 500 input tokens at $0.80/M + 200 output tokens at $4.00/M + web search overhead). At 500 companies with a cold cache, a worst-case full-miss scenario costs ~$12.50. In practice, after the cache warms, LLM calls drop to single digits per run. The `--max-llm-calls N` flag caps this at runtime.

---

### Call site 6: `contact_resolver/subprocessor_fetcher.py` — `fetch_subprocessors()`

**Why LLM is used here:** Subprocessor pages have no standardised format — some are tables, some are prose, some are behind JavaScript SPAs. The LLM can extract structured subprocessor data from any page format. The `web_search` tool is attached only when no scraped content was found, to minimise output token cost.

**Prompt strategy:** Structured extraction. The LLM receives scraped page content (or web search results) and returns a structured `SubprocessorRecord` with provider names, categories, and jurisdictions.

**Fallback:** If the API call fails, the domain gets `fetch_status="error"` and will be retried on the next background fetch.

**Cost:** ~$0.030–0.050 per company. At 500 companies, cold fetch costs $15–25. Free on re-fetch within 30-day TTL.
