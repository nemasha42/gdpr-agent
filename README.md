# GDPR Agent

A CLI tool that scans your Gmail inbox, identifies every service that holds your personal data, finds each company's official GDPR contact, and sends Subject Access Requests (SARs) on your behalf — with human approval before anything goes out.

Built in four phases across one Claude Code session. All PII stays local; nothing is auto-sent.

---

## How it works — end to end

```
Gmail inbox
     │
     ▼
[Phase 1] inbox_reader.py
  Fetch up to 500 email headers (sender / subject / date only, no body)
     │
     ▼
[Phase 2] service_extractor.py + company_normalizer.py
  Score each sender domain for HIGH / MEDIUM / LOW confidence
  Deduplicate to one record per domain
  Map domain → human-readable company name
     │
     ▼
[Phase 3] resolver.py  (5-step lookup chain)
  1. Local cache           data/companies.json        (free, instant)
  2. Curated overrides     data/dataowners_overrides.json (free)
  3. datarequests.org      via GitHub API              (free, ~600 ms)
  4. Privacy page scraper  HTML regex on /privacy-policy (free, ~1 s)
  5. Claude Haiku          web_search tool             (~$0.025/company)
     │
     ▼
[Phase 4] composer.py → sender.py
  Fill SAR template (email or postal)
  Show formatted preview
  Ask Y / N
  Send via Gmail API  (requires one-time gmail.send OAuth)
  Record to user_data/sent_letters.json
```

---

## Project structure

```
gdpr-agent/
├── run.py                          # Main pipeline (entry point)
├── auth/
│   └── gmail_oauth.py              # OAuth2: readonly (scan) + send scopes
├── scanner/
│   ├── inbox_reader.py             # Gmail API pagination, headers only
│   ├── service_extractor.py        # Signal detection + deduplication
│   └── company_normalizer.py       # Domain → display name
├── contact_resolver/
│   ├── models.py                   # Pydantic: CompanyRecord, Contact, …
│   ├── resolver.py                 # 5-step orchestrator + local DB
│   ├── privacy_page_scraper.py     # Regex scraper for /privacy URLs
│   ├── llm_searcher.py             # Claude Haiku + web_search fallback
│   └── cost_tracker.py             # Per-session cost table
├── letter_engine/
│   ├── models.py                   # SARLetter dataclass
│   ├── composer.py                 # Template filler
│   ├── sender.py                   # Preview → Y/N → dispatch
│   ├── tracker.py                  # Sent log (user_data/sent_letters.json)
│   └── templates/
│       ├── sar_email.txt           # Email body template
│       └── sar_postal.txt          # Postal letter template (with address block)
├── data/
│   ├── companies.json              # Shared contact DB (committed, no PII)
│   └── dataowners_overrides.json   # Curated records for major services
├── tests/unit/                     # 179 unit tests, 0 real network calls
├── config/settings.py              # .env → Pydantic Settings
└── user_data/                      # gitignored — tokens, sent log
    ├── token.json                  # Gmail readonly token
    ├── token_send.json             # Gmail send token (created on first send)
    └── sent_letters.json           # Audit log of sent SARs
```

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Google Cloud project with Gmail API enabled
- `credentials.json` from Google Cloud Console (OAuth 2.0 Desktop app)

### 2. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure `.env`

Copy `.env.example` to `.env` and fill in:

```env
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
ANTHROPIC_API_KEY=sk-ant-...

USER_FULL_NAME=Jane Doe
USER_EMAIL=jane@example.com
USER_ADDRESS_LINE1=10 Example Street
USER_ADDRESS_CITY=London
USER_ADDRESS_POSTCODE=SW1A 1AA
USER_ADDRESS_COUNTRY=United Kingdom
GDPR_FRAMEWORK=UK GDPR          # or "GDPR" for EU
```

### 4. First run

```bash
python run.py --dry-run
```

This opens a browser for Gmail readonly OAuth, scans your inbox, resolves contacts, and shows letter previews — nothing is sent.

---

## Usage

```bash
# Full run — shows preview and asks Y/N before each letter
python run.py

# Preview only, no sending
python run.py --dry-run

# Only process high-confidence services (clear sign-up / welcome emails)
python run.py --min-confidence HIGH

# Scan fewer emails (faster)
python run.py --max-emails 100
```

On first email send, a second browser window opens for `gmail.send` OAuth.
The send token is saved to `user_data/token_send.json` and reused silently.

---

## Phase detail

### Phase 1 — Gmail inbox reader

`scanner/inbox_reader.py` paginates the Gmail API using only the `gmail.readonly` scope. It fetches message IDs in bulk (`messages.list`), then retrieves three headers per message (`From`, `Subject`, `Date`) using `messages.get` with `format=metadata`. No email body is ever read.

### Phase 2 — Service detection

`scanner/service_extractor.py` classifies each email subject with a signal phrase lookup:

| Confidence | Example phrases |
|---|---|
| HIGH | "welcome to", "thanks for signing up", "verify your email", "activate" |
| MEDIUM | "your account", "your order", "sign-in", "login" |
| LOW | everything else |

Emails are deduplicated by domain (one record per domain, confidence upgraded if a stronger signal is seen). `company_normalizer.py` strips noise subdomain prefixes (`mail.`, `no-reply.`, `notifications.`, etc.), handles two-part TLDs (`co.uk`, `com.au`), and applies a hardcoded exception map (`facebookmail.com` → `Facebook`, `t.co` → `Twitter/X`, etc.).

### Phase 3 — Contact resolution

`contact_resolver/resolver.py` runs a 5-step chain per domain, stopping at the first success:

1. **Local cache** (`data/companies.json`) — TTL varies by source: datarequests=180d, llm/scrape=90d, manual=365d.
2. **Curated overrides** (`data/dataowners_overrides.json`) — hand-maintained records for services that require specific handling.
3. **datarequests.org** — open-source GDPR contact database (1,800+ companies) fetched via GitHub API. Matches by domain root and company name words; verifies a domain hit against the company's `runs` array.
4. **Privacy page scraper** — tries four well-known URLs (`/privacy-policy`, `/privacy`, `/legal/privacy`, `/gdpr`) and regex-scans the HTML for GDPR-pattern emails (`privacy@`, `dpo@`, `gdpr@`) and portal URLs (paths containing `dsar`, `privacy-request`, `subject-access`).
5. **Claude Haiku + `web_search_20250305`** — last resort. One tool call with `max_uses=1`. Costs ~$0.025. Result is cached immediately so each domain is only looked up once.

All successful lookups are written back to `data/companies.json` (public contact info only — no PII).

Cost tracking prints a summary table at the end of each run:

```
╔══════════════════════════════════════════════════════════════════╗
│  LLM COST SUMMARY                                                │
├──────────────┬──────────┬──────────┬────────────────┬──────────┤
│  Company     │  Input   │  Output  │  Cost (USD)    │ Result   │
├──────────────┼──────────┼──────────┼────────────────┼──────────┤
│  Glassdoor   │  14,231  │  312     │  $0.0251       │ ✓ found  │
│  Google      │  13,440  │  298     │  $0.0239       │ ✓ found  │
│  PayPal      │  15,102  │  341     │  $0.0268       │ ✓ found  │
│  TOTAL       │  42,773  │  951     │  $0.0758       │          │
└──────────────┴──────────┴──────────┴────────────────┴──────────┘
```

### Phase 4 — SAR letter engine

`letter_engine/composer.py` fills one of two templates based on `CompanyRecord.contact.preferred_method`:

- **email** → `sar_email.txt` — concise email body
- **portal** → `sar_email.txt` + prints the portal URL for manual submission
- **postal** → `sar_postal.txt` — full letter with sender address block, date, and recipient address

`sender.py` shows a boxed preview, prompts `[y/N]`, then:
- **email**: sends via Gmail API (`gmail.send` scope)
- **portal**: prints portal URL and body for copy-paste
- **postal**: prints the letter for printing and posting

Every approved letter is recorded to `user_data/sent_letters.json` with timestamp, company, method, and recipient.

---

## Real run results (trader1620 mailbox — March 2026)

13 companies resolved and cached from a real inbox scan:

| Domain | Source | Method | Notes |
|---|---|---|---|
| glassdoor.com | llm_search | email | privacy@glassdoor.com |
| google.com | llm_search | email | Found via web_search |
| paypal.com | llm_search | email | |
| substack.com | privacy_scrape | email | Found at /privacy |
| reflexivity.com | privacy_scrape | email | Found at /privacy-policy |
| polymarket.com | llm_search | email | |
| youtube.com | llm_search | email | Google subsidiary |
| accounts.google.com | llm_search | email | |
| communications.paypal.com | llm_search | email | PayPal subdomain |
| finalroundai.com | llm_search | email | |
| gmail.com | llm_search | email | |
| googlemail.com | llm_search | email | |
| polymarket.intercom-mail.com | llm_search | email | Intercom relay domain |

**LLM cost for this run:** 2 companies resolved via free scraper, 11 via Claude Haiku.
Approximate: 11 × $0.025 = **~$0.28 in Anthropic API costs** for the full contact resolution run.

All 13 records are now cached — subsequent runs cost $0.00 for these companies.

---

## Test coverage

```
tests/unit/
├── test_inbox_reader.py         — Gmail API pagination, header extraction
├── test_company_normalizer.py   — Subdomain stripping, TLD handling, exceptions
├── test_service_extractor.py    — Signal classification, deduplication, date ranges
├── test_resolver.py             — Full 5-step chain, staleness, cache write-back
├── test_privacy_page_scraper.py — HTML regex, email classification, URL extraction
├── test_llm_searcher.py         — JSON extraction, validation, cost recording
└── test_letter_engine.py        — Composer, tracker, sender (Y/N/dry-run/EOF)

Total: 179 tests — 0 real network calls, 0 real API calls, 0 real Gmail calls
```

All tests use dependency injection or `unittest.mock`. The LLM, Gmail API, and HTTP layer are fully mocked.

---

## Cost of building this project with Claude Code

This project was built in a single Claude Code session (claude-sonnet-4-6) across 4 phases.

| Item | Estimate |
|---|---|
| Model | claude-sonnet-4-6 |
| Pricing | $3.00 / M input tokens · $15.00 / M output tokens |
| Estimated input tokens | ~600,000–900,000 (context grows with each turn) |
| Estimated output tokens | ~60,000–80,000 (code + explanations) |
| **Estimated total cost** | **~$2.70–$3.90** |
| Session length | ~2–3 hours |
| Lines of production code | ~1,100 |
| Lines of test code | ~2,000 |

> Note: these are rough estimates — Claude Code does not expose exact per-session token counts in the conversation. The input cost dominates because the full conversation history is re-sent with every message as context grows.

What you got for ~$3–4 in AI API costs:
- A fully working Gmail → SAR pipeline
- 5-tier contact resolution (free paths first, LLM last resort)
- Per-session cost tracking with formatted output
- 179 unit tests with no live dependencies
- Human-in-the-loop sending with audit log
- Caching so each company is only looked up once ever

---

## Hard constraints (design decisions)

| Constraint | Reason |
|---|---|
| Gmail scope: readonly for scanning | Minimum privilege; send uses a separate token |
| No auto-sending | User must approve every SAR letter |
| LLM only for unknown contact lookup + letter fill | Cost control; free paths always tried first |
| All LLM results cached in `data/companies.json` | Never pay twice for the same company |
| All PII in `user_data/` (gitignored) | Tokens and sent log never reach the repo |
| `data/companies.json` committed | Public contact info is shareable and reusable |
