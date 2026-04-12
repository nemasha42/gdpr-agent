# Reply Classification Fixes + Portal Verification & Auto-Submit

**Date:** 2026-04-13
**Scope:** Two-phase improvement to reply handling — classifier accuracy (Phase A) then automated portal verification and submission (Phase B).

## Context

The Zendesk case exposed systemic issues: a company marked a SAR ticket as "Solved" after 3 days without providing data. The classifier tagged it `AUTO_ACKNOWLEDGE` (wrong — it's a premature closure). The ticket page URL was extracted as both `data_link` and `portal_url` (false positive). The survey follow-up URL was also extracted as `data_link`. Beyond Zendesk, similar gaps exist for Google, PayPal, FinalRoundAI, and Whop — all deflected to portals or closed tickets, and the system stopped with no follow-through.

**Design principle:** When a company says "use our portal" or closes a ticket, the system should *follow the instructions first* and only escalate if that path fails. No arguing about GDPR violations upfront.

---

## Phase A: Classifier Accuracy Fixes

### A1. Premature Ticket Closure → WRONG_CHANNEL

Add regex patterns to the existing `WRONG_CHANNEL` rule in `classifier.py`:

**Snippet patterns:**
```
ticket is set to Solved
request has been (closed|resolved|marked as solved)
case.{0,20}(closed|resolved)
ticket.{0,20}(resolved|closed|solved)
issue.{0,20}(resolved|closed)
marked.{0,20}(solved|resolved|closed)
```

**Subject patterns:**
```
set to Solved
marked as (solved|resolved|closed)
```

No new tag needed — `WRONG_CHANNEL` semantics cover this: "the company says go elsewhere / this channel is done." The existing `_ACTION_DRAFT_TAGS` inclusion and draft generation handle the downstream effects.

**Guard:** These patterns must NOT fire when the same message also matches `DATA_PROVIDED_*` or `FULFILLED_DELETION` tags — a legitimate "your request is resolved, here's your data" email is not a premature closure. Implemented as a post-pass check: after all `_RULES` are evaluated, if `WRONG_CHANNEL` was added by a closure pattern AND any terminal data tag is also present, remove the `WRONG_CHANNEL` tag. This is more reliable than depending on tag ordering since both are in Pass 1.

### A2. URL Extraction False-Positive Filtering

Add `_RE_JUNK_URL` regex in `classifier.py`, applied as a filter in `_extract()` before assigning `data_link` and `portal_url`:

```python
_RE_JUNK_URL = re.compile(
    r"/hc/[a-z-]+/requests/\d+"          # Zendesk help center ticket pages
    r"|/survey_responses/"                 # Zendesk/satisfaction surveys
    r"|/satisfaction/"                      # CSAT surveys
    r"|/hc/[a-z-]+/articles/"             # Help center articles
    r"|rate.{0,20}(support|service)"       # Rating pages
    r"|/feedback/",                         # Feedback forms
    re.I,
)
```

Applied in `_extract()`:
- Before appending to `data_links`, check `not _RE_JUNK_URL.search(url)`
- Before assigning `portal_url`, check `not _RE_JUNK_URL.search(url)`

This prevents ticket pages, surveys, and KB articles from polluting extracted fields.

### A3. Draft Tone — Follow Instructions First

In `generate_reply_draft()` (`classifier.py`), detect closure language in the reply body and adjust the prompt:

When snippet contains closure signals (`solved|resolved|closed` near `ticket|request|case`):
- Prompt addition: "The company appears to have closed this request and may have provided a portal or instructions. The follow-up should acknowledge receipt, confirm you will follow their portal/instructions, and note that if the portal does not fulfil the SAR you will follow up again. Do NOT argue about GDPR violations — try the provided path first."

When snippet is a standard redirect (no closure language):
- Existing prompt is fine — asks for clarification on which channel to use.

**Files changed:** `reply_monitor/classifier.py`

---

## Phase B: Portal Verification + Auto-Submit

### B1. URL Verifier Module

**New file:** `reply_monitor/url_verifier.py`

```python
class VerificationResult:
    url: str
    classification: str   # gdpr_portal | help_center | login_required | dead_link | survey | unknown
    checked_at: str        # ISO 8601
    error: str | None
    page_title: str        # for dashboard display
```

**Classification logic:**
1. `requests.get(url, timeout=10, allow_redirects=True)` — if 4xx/5xx or timeout → `dead_link`
2. If response is HTML, check for survey/feedback indicators → `survey`
3. If page contains form elements (input fields, textareas, submit buttons), delegate to `portal_submitter.form_analyzer.analyze_form()` for deeper inspection → `gdpr_portal` if form is a GDPR submission form
4. If page requires login (detected by `portal_submitter.platform_hints.detect_platform()` returning `login_required`) → `login_required`
5. If page is a Zendesk/Intercom/Freshdesk help center (KB articles, ticket viewer, no submission form) → `help_center`
6. Otherwise → `unknown`

**Lightweight first pass:** Use `requests.get` for non-JS pages. Only escalate to Playwright if the page has minimal content (likely JS-rendered SPA). Reuses existing `portal_submitter` infrastructure — no new LLM calls for classification when `form_analyzer` cache is warm.

**Storage:** Result stored on `ReplyRecord` as a new optional field:
```python
portal_verification: dict | None  # {url, classification, checked_at, error, page_title}
```

Added to `ReplyRecord` in `reply_monitor/models.py`. Serialized to `reply_state.json`.

### B2. Auto-Submit Trigger

In `monitor.py`, after classifying a new reply:

```
if WRONG_CHANNEL in tags AND portal_url is extracted:
    1. Run url_verifier.verify(portal_url)
    2. Store verification result on the ReplyRecord
    3. If classification == "gdpr_portal":
        - Call portal_submitter.submit_portal(letter, scan_email)
        - Update sent_letters.json with portal_status, confirmation_ref, screenshot
        - Set reply_review_status = "dismissed" (auto-handled, no draft needed)
    4. If classification != "gdpr_portal":
        - Generate reply draft with context about what the URL actually is:
          "login_required" → "portal requires authentication I cannot provide"
          "help_center" → "URL leads to a help center page, not a GDPR submission form"
          "dead_link" → "the provided URL is not accessible"
          "survey" → "URL leads to a satisfaction survey, not a GDPR portal"
          "unknown" → standard WRONG_CHANNEL draft
```

**Concurrency:** Portal submission is async (Playwright). Reuse the existing background task pattern from `dashboard/app.py` portal routes. If monitor is running CLI, run synchronously. If triggered from dashboard refresh, run as background task.

**Idempotency:** Only verify+submit once per URL per reply. Check `portal_verification` field before re-running. Re-verify if `checked_at` is older than 7 days (URLs can change).

### B3. Dashboard Integration

**Company detail page** (`company_detail.html`):
- Next to any portal URL in a reply, show verification badge:
  - `gdpr_portal` → green "Verified portal" + auto-submit status
  - `help_center` → yellow "Help center page"
  - `login_required` → yellow "Login required"
  - `dead_link` → red "Dead link"
  - `survey` → grey "Survey page"
  - `unknown` → grey "Unverified"
- If auto-submit succeeded, show portal submission result inline (reuses existing `portal_status` display from portal automation)
- If auto-submit failed, show failure reason and the generated draft reply

**No new routes needed** — verification data is on the ReplyRecord, rendered in the existing template.

### B4. Trigger Points — All Companies, Not Just Zendesk

The verifier runs on URLs from these contexts:

| Source | Trigger | URL field |
|--------|---------|-----------|
| `WRONG_CHANNEL` reply | New reply classified | `extracted.portal_url` |
| `CONFIRMATION_REQUIRED` reply | New reply classified | `extracted.confirmation_url` or `portal_url` |
| `DATA_PROVIDED_PORTAL` reply | New reply classified | `extracted.portal_url` |
| Contact resolution | `preferred_method=portal` | `contact.gdpr_portal_url` |
| Dashboard manual trigger | User clicks "Verify" button | Any URL on a reply |

For contact resolution portals, verification runs during `portal_submitter.submit_portal()` as a pre-check — if the URL isn't a real portal, return `PortalResult(needs_manual=True)` immediately instead of launching Playwright.

---

## Files Changed

| File | Changes |
|------|---------|
| `reply_monitor/classifier.py` | A1: new WRONG_CHANNEL regex patterns for ticket closure. A2: `_RE_JUNK_URL` filter in `_extract()`. A3: closure-aware draft prompt in `generate_reply_draft()` |
| `reply_monitor/url_verifier.py` | **New.** B1: `verify(url) → VerificationResult`. Lightweight HTTP + optional Playwright. Reuses `form_analyzer` and `platform_hints` |
| `reply_monitor/models.py` | B1: `portal_verification` optional field on `ReplyRecord` |
| `monitor.py` | B2: after classification, run verify + auto-submit for WRONG_CHANNEL replies with portal URLs |
| `dashboard/templates/company_detail.html` | B3: verification badges next to portal URLs, auto-submit status display |
| `dashboard/app.py` | B3: pass verification data to template context |
| `tests/unit/test_reply_classifier.py` | A1-A2: tests for new patterns and URL filtering |
| `tests/unit/test_url_verifier.py` | **New.** B1: mock HTTP/Playwright tests for each classification |

## Out of Scope

- Changing portal_submitter internals — we reuse it as-is
- LLM-based URL classification — too expensive for a pre-check; regex + form detection is sufficient
- Retrying auto-submit failures automatically — surface on dashboard, user decides
