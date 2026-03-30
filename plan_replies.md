# Reply Monitor: Coverage Review & Reply Form

## Context

After sending a SAR, the system must reliably detect every reply and guide the user to the right
next step. Three things are missing:
1. Several reply types lack automatic (regex) classifiers and fall through to LLM or HUMAN_REVIEW
2. When a company asks a question or blocks progress, there is no way to reply from the dashboard
3. No clear answer to "can I miss a reply?" — worth documenting explicitly

---

## Part 1 — Can We Miss a Reply?

**Current approach:** thread-based lookup (`threadId:xxx` in Gmail API) fetches every message in
the SAR's Gmail thread, regardless of sender address. Ticketing systems (Zendesk, Freshdesk,
ServiceNow) always reply *within* the same thread, so these are caught reliably.

**One gap:** if a company sends a *fresh* email (new subject, new thread) rather than replying,
we miss it. This is uncommon but happens when a separate GDPR team initiates contact.

**Mitigation (low cost):** for any domain in PENDING/ACTION_REQUIRED status past day 10,
add a secondary domain-search pass (reuse existing `_fetch_by_search` from `fetcher.py`)
as a safety-net background check during `/refresh`. The fetcher already deduplicates by message ID,
so noise is controlled. Add to `monitor.py` `_maybe_domain_search_fallback()` called from `main()`.

**Spam risk:** Gmail search `in:anywhere` already scans spam folder, so no extra work needed there.

---

## Part 2 — New Automatic Classifiers (no LLM)

Add these three new regex rules to `_RULES` in `reply_monitor/classifier.py` and to `REPLY_TAGS`
in `reply_monitor/models.py`:

### A. `COMPLAINT_RIGHTS_MENTIONED`
Company mentions supervisory authority / right to complain. Appears in denials and "not applicable"
letters. Signals user should know they can escalate to the ICO/CNIL/etc.

```python
("COMPLAINT_RIGHTS_MENTIONED", [
    ("snippet", re.compile(
        r"supervisory authority|information commissioner|lodge a complaint"
        r"|right to complain|data protection authority"
        r"|\bICO\b|\bCNIL\b|\bGDPR complaint\b",
        re.I,
    )),
]),
```

→ state_manager.py: add to `_ACTION_TAGS` so it surfaces as ACTION_REQUIRED (user should know about escalation path).

### B. `PROCESSING_FEE_REQUESTED`
Company asks for payment before processing. Under GDPR Art. 12 this is only valid for
manifestly unfounded/excessive requests; user may want to challenge it.

```python
("PROCESSING_FEE_REQUESTED", [
    ("snippet", re.compile(
        r"administrative fee|processing fee|charge.{0,30}request"
        r"|fee.{0,30}(process|fulfil|fulfill)"
        r"|payment.{0,30}required.{0,30}(request|access)",
        re.I,
    )),
]),
```

→ state_manager.py: add to `_ACTION_TAGS`.

### C. `THIRD_PARTY_PROCESSOR`
Company says they are a data processor, not a controller — redirects user to another entity.

```python
("THIRD_PARTY_PROCESSOR", [
    ("snippet", re.compile(
        r"(act|acting) as a data processor|not the data controller"
        r"|you should contact.{0,30}(controller|provider|employer)"
        r"|direct your request to",
        re.I,
    )),
]),
```

→ state_manager.py: add to `_ACTION_TAGS` (user needs to find the real controller).

### Existing tags that already cover user's listed scenarios

| User scenario | Existing tag(s) |
|---|---|
| Link(s) to download | `DATA_PROVIDED_LINK` + `data_links` list |
| File attached | `DATA_PROVIDED_ATTACHMENT` |
| Instructions to extract via account | `DATA_PROVIDED_PORTAL` + `WRONG_CHANNEL` |
| Address unsuitable | `BOUNCE_PERMANENT` + `WRONG_CHANNEL` |
| Don't know GDPR | `NOT_GDPR_APPLICABLE` + `NO_DATA_HELD` |

---

## Part 3 — LLM Prompt: Add next_step + suggested_reply

**File:** `reply_monitor/classifier.py` → `_llm_classify()`

Extend the LLM JSON schema with two new optional fields:

```
"next_step": one of ["download_data", "find_portal", "search_alternate_contact",
                      "reply_required", "no_action"]
"suggested_reply_text": string or null — draft reply body when next_step == "reply_required"
```

Guidance text additions to the prompt:
- `reply_required` → company is blocking progress and needs an explicit response
- `no_action` → acknowledgement / informational only
- `suggested_reply_text` → formal tone, first-person, ready to send; null for no_action

**Storage:** add `next_step: str | None` and `suggested_reply_text: str | None` to
`ReplyRecord.extracted` dict (already a free-form dict, no schema migration needed).

---

## Part 4 — Dashboard Reply Form

Only shows when a reply is genuinely needed ("not just a thank you note").

### Trigger tags → urgent reply needed
- `IDENTITY_REQUIRED` — must provide ID or request stalls
- `CONFIRMATION_REQUIRED` — must click/reply or request won't proceed
- `MORE_INFO_REQUIRED` — must clarify or request won't proceed
- `PROCESSING_FEE_REQUESTED` (new) — should challenge the fee
- `COMPLAINT_RIGHTS_MENTIONED` (new) — optional escalation letter
- `THIRD_PARTY_PROCESSOR` (new) — needs to find controller (no email reply, but shown)
- `HUMAN_REVIEW` — unknown situation; user must read and decide

### Reply text generation (template-based, free)

New module: `letter_engine/reply_templates.py`

```python
def get_suggested_reply(tag: str, context: dict) -> tuple[str, str]:
    """Return (subject_prefix, body) for the given action tag.
    context keys: company_name, original_subject, sent_at, user_name
    """
```

Templates for each tag:
- `IDENTITY_REQUIRED` → "Re: [subject]" + "Dear [company], Following your request for identity
  verification, please find evidence of identity attached. I confirm this is a valid Subject Access
  Request. Regards, [name]"
- `CONFIRMATION_REQUIRED` → "Re: [subject]" + "Dear [company], I confirm my Subject Access Request
  submitted on [date]. Please proceed with the request. Regards, [name]"
- `MORE_INFO_REQUIRED` → "Re: [subject]" + "Dear [company], Thank you for your response. To assist
  with your query: [USER TO FILL IN]. Regards, [name]"
- `PROCESSING_FEE_REQUESTED` → "Re: [subject]" + formal GDPR Art. 12 challenge text
- `HUMAN_REVIEW` → show `suggested_reply_text` from LLM if available; else blank textarea with hint

Context comes from `CompanyState` + `config.settings`.

### New sender function

**File:** `letter_engine/sender.py`

```python
def send_reply_in_thread(
    account_email: str,
    to_email: str,
    subject: str,
    body: str,
    thread_id: str,
    in_reply_to_msg_id: str = "",
) -> tuple[bool, str]:
    """Send a reply email in an existing Gmail thread. Returns (success, new_message_id)."""
    # Uses get_gmail_send_service(account_email) — already imported
    # Sets msg["In-Reply-To"] = in_reply_to_msg_id if present
    # Sets body={"raw": ..., "threadId": thread_id}
```

### New dashboard routes

**File:** `dashboard/app.py`

```
GET  /company/<domain>/reply   — render reply form
POST /company/<domain>/reply   — send reply, redirect to /company/<domain>
```

GET handler:
1. Load `CompanyState` for domain
2. Identify triggering tag from latest ACTION_REQUIRED reply
3. Call `get_suggested_reply(tag, context)` for subject/body prefill
4. If HUMAN_REVIEW and LLM `suggested_reply_text` available, use that instead
5. Render `reply_form.html`

POST handler:
1. Read `subject`, `body`, `to_email` from form
2. Call `send_reply_in_thread(account, to_email, subject, body, thread_id, in_reply_to_msg_id)`
3. On success: redirect to `/company/<domain>?reply_sent=1`
4. On failure: re-render form with error message

### New template

**File:** `dashboard/templates/reply_form.html`

Extends `base.html`. Form fields:
- To (read-only, shows company email)
- Subject (editable, prefilled with "Re: [original subject]")
- Body (editable `<textarea>`, prefilled from template/LLM)
- Send button + Cancel button (back to company detail)
- Small notice: "This will be sent from your Gmail account [email]"

### "Reply" button on company_detail

**File:** `dashboard/templates/company_detail.html`

Add a "Reply" button that links to `/company/<domain>/reply` visible only when
`status == "ACTION_REQUIRED"` and tag is in the urgent-reply set (not WRONG_CHANNEL / THIRD_PARTY_PROCESSOR which need a different action).

---

## Part 5 — Next Steps: Other Actions in Dashboard

In addition to "Reply", the company detail page already shows `action_hint` and `action_hint_url`.
Extend `_ACTION_HINTS` in `dashboard/app.py` for the new tags:

```python
"COMPLAINT_RIGHTS_MENTIONED": "You have the right to complain to your supervisory authority (e.g. ICO)",
"PROCESSING_FEE_REQUESTED":   "Challenge the fee — under GDPR Art. 12 requests are free unless excessive",
"THIRD_PARTY_PROCESSOR":      "Find and contact the data controller directly",
```

Also ensure existing next-step logic works for:
- `DATA_PROVIDED_LINK` → "Download data" (already exists)
- `WRONG_CHANNEL` → "Submit via portal" with extracted portal_url (already exists)
- `BOUNCE_PERMANENT` → "New address search" (already exists via auto-retry in monitor)

---

## Files to Change

| File | Change |
|---|---|
| `reply_monitor/models.py` | Add 3 new tags to `REPLY_TAGS` |
| `reply_monitor/classifier.py` | Add 3 new `_RULES` entries |
| `reply_monitor/state_manager.py` | Add 3 new tags to `_ACTION_TAGS` |
| `reply_monitor/fetcher.py` | Add `_fetch_by_search` as secondary pass for old/stuck records |
| `reply_monitor/classifier.py` | Extend `_llm_classify()` prompt with `next_step` + `suggested_reply_text` |
| `letter_engine/sender.py` | Add `send_reply_in_thread()` |
| `letter_engine/reply_templates.py` | New file — template-based reply drafts |
| `dashboard/app.py` | New `/company/<domain>/reply` routes, `_ACTION_HINTS` additions |
| `dashboard/templates/reply_form.html` | New template |
| `dashboard/templates/company_detail.html` | Add Reply button |
| `tests/unit/test_classifier.py` | Tests for 3 new tags |
| `tests/unit/test_reply_sender.py` | Tests for `send_reply_in_thread()` |

---

## Verification

1. `python -m pytest tests/unit/ -q` — all tests pass
2. Run `python monitor.py --verbose` — new tags appear in summary table
3. Open dashboard, navigate to a company with `ACTION_REQUIRED` status:
   - Reply button visible
   - Form loads with correct prefill based on tag
   - Send dispatches email and redirects back with success notice
4. Manually create a test reply email with "we need to charge a small fee" text → classify it → verify `PROCESSING_FEE_REQUESTED` tag fires
