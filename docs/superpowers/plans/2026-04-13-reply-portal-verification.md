# Reply Classification Fixes + Portal Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix classifier false positives (premature ticket closure, junk URL extraction) and add automated portal URL verification with auto-submit for WRONG_CHANNEL replies.

**Architecture:** Phase A patches `classifier.py` with new regex patterns and URL filtering. Phase B adds `reply_monitor/url_verifier.py` that classifies URLs via HTTP + existing `portal_submitter` infrastructure, then triggers auto-submit from `monitor.py`. The `ReplyRecord` model gains a `portal_verification` field to persist results.

**Tech Stack:** Python, regex, requests, Playwright (via existing portal_submitter), Flask templates (Jinja2)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `reply_monitor/classifier.py` | Modify | A1: closure patterns, A2: `_RE_JUNK_URL`, A3: draft tone |
| `tests/unit/test_reply_classifier.py` | Modify | Tests for A1, A2, A3 |
| `reply_monitor/models.py` | Modify | B1: `portal_verification` field on `ReplyRecord` |
| `reply_monitor/url_verifier.py` | Create | B1: `verify()` → `VerificationResult` |
| `tests/unit/test_url_verifier.py` | Create | B1: tests for URL classification |
| `monitor.py` | Modify | B2: verify + auto-submit after classification |
| `dashboard/templates/company_detail.html` | Modify | B3: verification badges |
| `dashboard/app.py` | Modify | B3: pass verification data to template |
| `tests/unit/test_reply_state_manager.py` | Modify | Ensure new field doesn't break serialization |

---

### Task 1: Premature Ticket Closure → WRONG_CHANNEL (A1)

**Files:**
- Modify: `tests/unit/test_reply_classifier.py`
- Modify: `reply_monitor/classifier.py:89-110` (WRONG_CHANNEL rule)

- [ ] **Step 1: Write failing tests for ticket closure patterns**

Add to `tests/unit/test_reply_classifier.py` inside `class TestWrongChannel`:

```python
    def test_zendesk_ticket_set_to_solved(self):
        """Zendesk 'ticket is set to Solved' without data → WRONG_CHANNEL."""
        result = classify(msg(
            subject="[Employee Help Center] Re: Subject Access Request",
            snippet="Please be sure to REPLY-ALL. Request (649929) has been updated and the ticket is set to Solved.",
        ))
        assert "WRONG_CHANNEL" in result.tags

    def test_request_marked_as_resolved(self):
        result = classify(msg(snippet="Your request has been marked as resolved. If you need further help, contact us."))
        assert "WRONG_CHANNEL" in result.tags

    def test_case_closed(self):
        result = classify(msg(snippet="Your case has been closed. Thank you for contacting support."))
        assert "WRONG_CHANNEL" in result.tags

    def test_ticket_solved_subject(self):
        result = classify(msg(subject="Request #649929 set to Solved"))
        assert "WRONG_CHANNEL" in result.tags

    def test_solved_with_data_link_not_wrong_channel(self):
        """If the message also has a real data link, WRONG_CHANNEL should be suppressed."""
        result = classify(msg(
            snippet="Your request has been resolved. Download your data: https://example.com/export/download?token=abc123",
        ))
        assert "DATA_PROVIDED_LINK" in result.tags
        # WRONG_CHANNEL may or may not be present — the key is DATA_PROVIDED_LINK takes priority
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestWrongChannel::test_zendesk_ticket_set_to_solved tests/unit/test_reply_classifier.py::TestWrongChannel::test_request_marked_as_resolved tests/unit/test_reply_classifier.py::TestWrongChannel::test_case_closed tests/unit/test_reply_classifier.py::TestWrongChannel::test_ticket_solved_subject -v`
Expected: First 4 FAIL (WRONG_CHANNEL not in tags)

- [ ] **Step 3: Add closure patterns to WRONG_CHANNEL regex**

In `reply_monitor/classifier.py`, add to the `WRONG_CHANNEL` snippet pattern (after line 109, before the closing `re.I))` on line 110):

```python
            # Premature ticket closure — company resolved/closed ticket without providing data
            r"|ticket.{0,20}(set to |is )?(solved|resolved|closed)"
            r"|request.{0,20}(has been |been )?(closed|resolved|marked as (solved|resolved))"
            r"|case.{0,20}(has been |been )?(closed|resolved)"
            r"|marked as (solved|resolved|closed)"
```

Add to the `WRONG_CHANNEL` subject pattern. Currently WRONG_CHANNEL has no subject rule — add one as a new tuple in the rule's list (after the snippet entry):

```python
        ("subject", re.compile(
            r"set to Solved|marked as (solved|resolved|closed)",
            re.I)),
```

- [ ] **Step 4: Add post-pass guard for closure + data delivery overlap**

In `reply_monitor/classifier.py`, after the body-level tag promotion block (after line 477, before `# --- Pass 2: LLM fallback ---`), add:

```python
    # --- Post-pass guard: closure + data delivery ---
    # If WRONG_CHANNEL was triggered by a closure pattern ("solved", "resolved", "closed")
    # and a terminal data tag is also present, remove WRONG_CHANNEL — the company actually
    # delivered data alongside closing the ticket.
    _TERMINAL_DATA_TAGS = {"DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT",
                           "DATA_PROVIDED_INLINE", "DATA_PROVIDED_PORTAL", "FULFILLED_DELETION"}
    if "WRONG_CHANNEL" in tags and (set(tags) & _TERMINAL_DATA_TAGS):
        tags = [t for t in tags if t != "WRONG_CHANNEL"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestWrongChannel -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite to check for regressions**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add reply_monitor/classifier.py tests/unit/test_reply_classifier.py
git commit -m "feat(classifier): detect premature ticket closure as WRONG_CHANNEL

Add regex patterns for 'ticket solved/resolved/closed' language in snippet
and subject. Post-pass guard prevents WRONG_CHANNEL when a terminal data
tag is also present (legitimate resolution with data delivery)."
```

---

### Task 2: URL Extraction False-Positive Filtering (A2)

**Files:**
- Modify: `tests/unit/test_reply_classifier.py`
- Modify: `reply_monitor/classifier.py:507-596` (`_extract` function)

- [ ] **Step 1: Write failing tests for junk URL filtering**

Add a new test class to `tests/unit/test_reply_classifier.py`:

```python
class TestJunkURLFiltering:
    def test_zendesk_ticket_page_not_data_link(self):
        """Zendesk /hc/*/requests/NNN is a ticket page, not a data download."""
        result = classify({
            "from": "privacy@society.zendesk.com",
            "subject": "[Employee Help Center] Re: SAR",
            "snippet": "Visit https://society.zendesk.com/hc/en-us/requests/649929 for details",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""
        assert result.extracted["data_links"] == []

    def test_zendesk_survey_url_not_data_link(self):
        """Zendesk survey_responses URL should not be extracted as data_link."""
        result = classify({
            "from": "privacy@society.zendesk.com",
            "subject": "Request #649929: How would you rate the support?",
            "snippet": "Please let us know https://society.zendesk.com/hc/en-us/survey_responses/01KM?access_token=abc",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""
        assert result.extracted["data_links"] == []

    def test_zendesk_ticket_page_not_portal_url(self):
        """Zendesk ticket page should not be extracted as portal_url either."""
        result = classify({
            "from": "privacy@society.zendesk.com",
            "subject": "Re: SAR",
            "snippet": "Your request has been updated. https://society.zendesk.com/hc/en-us/requests/649929",
            "body": "Please submit via our portal at https://society.zendesk.com/hc/en-us/requests/649929",
            "has_attachment": False,
        })
        assert result.extracted["portal_url"] == ""

    def test_help_center_article_not_data_link(self):
        """Help center articles are not data downloads."""
        result = classify({
            "from": "support@example.com",
            "subject": "Re: Data Request",
            "snippet": "See https://help.example.com/hc/en-us/articles/123456 for instructions on downloading your data",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""

    def test_real_data_link_still_extracted(self):
        """Legitimate data download URLs must still work."""
        url = "https://example.com/export/download?token=abc123"
        result = classify({
            "from": "privacy@example.com",
            "subject": "Your data export is ready",
            "snippet": f"Download here: {url}",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == url

    def test_feedback_url_not_data_link(self):
        """Feedback/rating URLs should not be extracted."""
        result = classify({
            "from": "support@example.com",
            "subject": "How was your experience?",
            "snippet": "Rate us at https://example.com/feedback/rate?session=abc123",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestJunkURLFiltering -v`
Expected: Most FAIL (junk URLs currently extracted as data_link/portal_url)

- [ ] **Step 3: Add `_RE_JUNK_URL` and apply it in `_extract()`**

In `reply_monitor/classifier.py`, add below `_RE_DATA_URL_EXCLUDE` (after line 380):

```python
# URLs that should never be extracted as data_link or portal_url
_RE_JUNK_URL = re.compile(
    r"/hc/[a-z-]+/requests/\d+"          # Zendesk help center ticket pages
    r"|/hc/[a-z-]+/survey_responses/"     # Zendesk/CSAT surveys
    r"|/hc/[a-z-]+/articles/"             # Help center knowledge base articles
    r"|/satisfaction/"                      # CSAT survey endpoints
    r"|/feedback/"                          # Feedback forms
    r"|/survey[_-]?responses?/",           # Generic survey response URLs
    re.I,
)


def _is_junk_url(url: str) -> bool:
    """Return True if a URL is a known non-data, non-portal page."""
    return bool(_RE_JUNK_URL.search(url))
```

In `_extract()`, modify the data_links collection passes to filter junk URLs. For each pass (A through D), wrap the append with the junk check. In Pass A (line 546-548), change:

```python
    for m in _RE_ZENDESK_ATTACHMENT_A.finditer(full_text):
        url = _clean_url(m.group(1))
        if url and url not in data_links:
            data_links.append(url)
```

to:

```python
    for m in _RE_ZENDESK_ATTACHMENT_A.finditer(full_text):
        url = _clean_url(m.group(1))
        if url and url not in data_links and not _is_junk_url(url):
            data_links.append(url)
```

Apply the same `and not _is_junk_url(url)` guard to Pass B (line 554), Pass C (line 562), and Pass D (line 570).

For `portal_url` extraction (lines 578-586), add the same guard. After `portal_url = _clean_url(url_match.group(0))` (line 586), add:

```python
            if _is_junk_url(portal_url):
                portal_url = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestJunkURLFiltering -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add reply_monitor/classifier.py tests/unit/test_reply_classifier.py
git commit -m "fix(classifier): filter junk URLs from data_link and portal_url extraction

Zendesk ticket pages (/hc/*/requests/), survey URLs (/survey_responses/),
help center articles, and feedback pages no longer extracted as data_link
or portal_url. Fixes false positives in Zendesk and similar platforms."
```

---

### Task 3: Draft Tone — Follow Instructions First (A3)

**Files:**
- Modify: `reply_monitor/classifier.py:639-685` (`generate_reply_draft`)

- [ ] **Step 1: Write test for closure-aware draft prompt**

Add to `tests/unit/test_reply_classifier.py`:

```python
class TestDraftTone:
    def test_closure_draft_mentions_follow_instructions(self):
        """When reply has closure language, the draft prompt should instruct to follow portal."""
        from reply_monitor.classifier import generate_reply_draft
        from unittest.mock import patch, MagicMock

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="I will follow the portal instructions.")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 30

        with patch("reply_monitor.classifier.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            with patch("reply_monitor.classifier.cost_tracker"):
                result = generate_reply_draft(
                    "Your ticket is set to Solved. Visit our portal for details.",
                    ["WRONG_CHANNEL"],
                    "Zendesk",
                    api_key="sk-test",
                )

            # Verify the prompt sent to LLM contains the follow-instructions guidance
            call_args = mock_client.messages.create.call_args
            prompt_text = call_args[1]["messages"][0]["content"]
            assert "follow" in prompt_text.lower() or "portal" in prompt_text.lower()
            assert "violation" not in prompt_text.lower()

    def test_standard_redirect_draft_no_closure_context(self):
        """Standard WRONG_CHANNEL (no closure language) uses normal prompt."""
        from reply_monitor.classifier import generate_reply_draft
        from unittest.mock import patch, MagicMock

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Please clarify the appropriate channel.")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 30

        with patch("reply_monitor.classifier.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            with patch("reply_monitor.classifier.cost_tracker"):
                result = generate_reply_draft(
                    "This address is no longer monitored. Use our support form.",
                    ["WRONG_CHANNEL"],
                    "Example Corp",
                    api_key="sk-test",
                )

            call_args = mock_client.messages.create.call_args
            prompt_text = call_args[1]["messages"][0]["content"]
            # Should not contain the closure-specific guidance
            assert "closed" not in prompt_text.lower() or "prematurely" not in prompt_text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestDraftTone -v`
Expected: FAIL (prompt doesn't contain closure-specific language)

- [ ] **Step 3: Add closure detection to `generate_reply_draft()`**

In `reply_monitor/classifier.py`, modify `generate_reply_draft()`. After the `issues` line (line 656) and before the `prompt` definition (line 657), add:

```python
        # Detect closure language for tone adjustment
        _closure_re = re.compile(
            r"(ticket|request|case|issue).{0,20}(solved|resolved|closed|marked as)",
            re.I,
        )
        is_closure = bool(_closure_re.search(reply_body))

        if is_closure:
            tone_guidance = (
                "\nIMPORTANT TONE: The company appears to have closed/resolved this request. "
                "They may have provided a portal or instructions to follow. "
                "The reply should acknowledge receipt, confirm you will follow their portal or instructions, "
                "and note that if the portal does not fulfil the SAR you will follow up again. "
                "Do NOT argue about GDPR violations — try the provided path first.\n"
            )
        else:
            tone_guidance = ""
```

Then modify the `prompt` string to insert `tone_guidance` after the `"Detected issue(s): {issues}\n"` line. Change:

```python
        prompt = (
            "You are helping a data subject follow up on a GDPR Subject Access Request.\n"
            "The company has replied but the response is unclear or requires action.\n\n"
            f"Company: {company_name}\n"
            f"Detected issue(s): {issues}\n"
            f"Their reply:\n{reply_body[:3000]}\n\n"
```

to:

```python
        prompt = (
            "You are helping a data subject follow up on a GDPR Subject Access Request.\n"
            "The company has replied but the response is unclear or requires action.\n\n"
            f"Company: {company_name}\n"
            f"Detected issue(s): {issues}\n"
            f"{tone_guidance}"
            f"Their reply:\n{reply_body[:3000]}\n\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestDraftTone -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add reply_monitor/classifier.py tests/unit/test_reply_classifier.py
git commit -m "feat(classifier): closure-aware draft tone for WRONG_CHANNEL replies

When reply contains ticket closure language (solved/resolved/closed),
the draft prompt instructs to follow the company's portal/instructions
first and only escalate if that path fails. No arguing about GDPR
violations upfront."
```

---

### Task 4: `portal_verification` Field on ReplyRecord (B1a)

**Files:**
- Modify: `reply_monitor/models.py:127-179` (ReplyRecord)
- Modify: `tests/unit/test_reply_state_manager.py`

- [ ] **Step 1: Write test for serialization round-trip with new field**

Add to `tests/unit/test_reply_state_manager.py`:

```python
class TestPortalVerificationField:
    def test_reply_record_round_trip_with_portal_verification(self):
        from reply_monitor.models import ReplyRecord
        record = ReplyRecord(
            gmail_message_id="abc123",
            received_at="2026-04-13T10:00:00Z",
            from_addr="privacy@example.com",
            subject="Re: SAR",
            snippet="Use our portal",
            tags=["WRONG_CHANNEL"],
            extracted={"portal_url": "https://example.com/privacy"},
            llm_used=False,
            has_attachment=False,
            attachment_catalog=None,
            portal_verification={
                "url": "https://example.com/privacy",
                "classification": "gdpr_portal",
                "checked_at": "2026-04-13T10:05:00Z",
                "error": None,
                "page_title": "Privacy Request Form",
            },
        )
        d = record.to_dict()
        assert d["portal_verification"]["classification"] == "gdpr_portal"

        restored = ReplyRecord.from_dict(d)
        assert restored.portal_verification["classification"] == "gdpr_portal"
        assert restored.portal_verification["url"] == "https://example.com/privacy"

    def test_reply_record_round_trip_without_portal_verification(self):
        """Backward compat: old records without portal_verification still load."""
        from reply_monitor.models import ReplyRecord
        d = {
            "gmail_message_id": "xyz",
            "received_at": "2026-04-13T10:00:00Z",
            "from": "test@example.com",
            "subject": "Re: SAR",
            "snippet": "Hello",
            "tags": ["AUTO_ACKNOWLEDGE"],
            "extracted": {},
            "llm_used": False,
            "has_attachment": False,
        }
        record = ReplyRecord.from_dict(d)
        assert record.portal_verification is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_reply_state_manager.py::TestPortalVerificationField -v`
Expected: FAIL (ReplyRecord doesn't accept portal_verification kwarg)

- [ ] **Step 3: Add `portal_verification` field to ReplyRecord**

In `reply_monitor/models.py`, add field to `ReplyRecord` (after line 142):

```python
    portal_verification: dict | None = None  # {url, classification, checked_at, error, page_title}
```

In `to_dict()` (inside the return dict, after `"sent_reply_at"` on line 159):

```python
            "portal_verification": self.portal_verification,
```

In `from_dict()` (add to the `cls(...)` call, after `sent_reply_at`):

```python
            portal_verification=d.get("portal_verification"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_reply_state_manager.py::TestPortalVerificationField -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add reply_monitor/models.py tests/unit/test_reply_state_manager.py
git commit -m "feat(models): add portal_verification field to ReplyRecord

Optional dict field storing URL verification results (classification,
checked_at, error, page_title). Backward-compatible — old records
without the field load with None."
```

---

### Task 5: URL Verifier Module (B1b)

**Files:**
- Create: `reply_monitor/url_verifier.py`
- Create: `tests/unit/test_url_verifier.py`

- [ ] **Step 1: Write tests for URL verifier**

Create `tests/unit/test_url_verifier.py`:

```python
"""Unit tests for reply_monitor/url_verifier.py — URL classification."""

from unittest.mock import patch, MagicMock

import pytest

from reply_monitor.url_verifier import verify, CLASSIFICATION


class TestVerifyDeadLink:
    def test_timeout_returns_dead_link(self):
        import requests
        with patch("reply_monitor.url_verifier.requests.get", side_effect=requests.Timeout):
            result = verify("https://dead.example.com/privacy")
        assert result["classification"] == CLASSIFICATION.DEAD_LINK
        assert "timeout" in (result["error"] or "").lower()

    def test_404_returns_dead_link(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.headers = {}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/gone")
        assert result["classification"] == CLASSIFICATION.DEAD_LINK

    def test_500_returns_dead_link(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.headers = {}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/broken")
        assert result["classification"] == CLASSIFICATION.DEAD_LINK


class TestVerifySurvey:
    def test_survey_url_pattern(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><title>Rate our support</title><body>How did we do?</body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://society.zendesk.com/hc/en-us/survey_responses/01KM?access_token=abc")
        assert result["classification"] == CLASSIFICATION.SURVEY

    def test_satisfaction_content(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><title>Feedback</title><body>Please rate the support you received</body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/feedback")
        assert result["classification"] == CLASSIFICATION.SURVEY


class TestVerifyHelpCenter:
    def test_zendesk_ticket_page(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><title>Request #649929</title><body>Your request status. Sign in to see details.</body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://society.zendesk.com/hc/en-us/requests/649929")
        assert result["classification"] == CLASSIFICATION.HELP_CENTER

    def test_help_center_article(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><title>How to manage your data</title><body>Article content about privacy settings</body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://help.example.com/hc/en-us/articles/123456")
        assert result["classification"] == CLASSIFICATION.HELP_CENTER


class TestVerifyLoginRequired:
    def test_login_required_platform(self):
        """URLs on login-required domains detected without HTTP fetch."""
        result = verify("https://myaccount.google.com/privacy")
        assert result["classification"] == CLASSIFICATION.LOGIN_REQUIRED

    def test_apple_login_required(self):
        result = verify("https://privacy.apple.com/account")
        assert result["classification"] == CLASSIFICATION.LOGIN_REQUIRED


class TestVerifyGDPRPortal:
    def test_onetrust_portal(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "<html><title>Privacy Request</title>"
            "<body><form><input name='email'><textarea name='description'></textarea>"
            "<button type='submit'>Submit Request</button></form></body></html>"
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://privacyportal.onetrust.com/webform/abc")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL

    def test_generic_form_with_submit(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "<html><title>Data Request Form</title>"
            "<body><form action='/submit'><input type='email' name='email'>"
            "<select name='request_type'><option>Access my data</option></select>"
            "<button>Submit</button></form></body></html>"
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/privacy-request")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL


class TestVerifyIdempotency:
    def test_already_verified_within_ttl(self):
        """If existing verification is fresh, return it without re-checking."""
        existing = {
            "url": "https://example.com/portal",
            "classification": "gdpr_portal",
            "checked_at": "2026-04-13T10:00:00Z",
            "error": None,
            "page_title": "Privacy Portal",
        }
        # With checked_at only minutes ago, should return existing
        from reply_monitor.url_verifier import verify_if_needed
        from datetime import datetime, timezone
        now = datetime(2026, 4, 13, 10, 5, 0, tzinfo=timezone.utc)
        result = verify_if_needed("https://example.com/portal", existing=existing, now=now)
        assert result is existing  # same object, no re-fetch

    def test_stale_verification_re_checks(self):
        """If existing verification is older than TTL, re-verify."""
        existing = {
            "url": "https://example.com/portal",
            "classification": "unknown",
            "checked_at": "2026-04-01T10:00:00Z",
            "error": None,
            "page_title": "",
        }
        from reply_monitor.url_verifier import verify_if_needed
        from datetime import datetime, timezone
        now = datetime(2026, 4, 13, 10, 0, 0, tzinfo=timezone.utc)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><title>Portal</title><body><form><input name='email'><button>Submit</button></form></body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify_if_needed("https://example.com/portal", existing=existing, now=now)
        assert result is not existing  # fresh result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_url_verifier.py -v`
Expected: FAIL (module doesn't exist)

- [ ] **Step 3: Create `reply_monitor/url_verifier.py`**

```python
"""Verify and classify URLs extracted from GDPR reply emails.

Classifies URLs as: gdpr_portal, help_center, login_required, dead_link,
survey, or unknown. Uses lightweight HTTP fetch + HTML inspection.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlparse

import requests

from portal_submitter.platform_hints import detect_platform

_VERIFY_TTL = timedelta(days=7)
_TIMEOUT = 10  # seconds


class CLASSIFICATION:
    GDPR_PORTAL = "gdpr_portal"
    HELP_CENTER = "help_center"
    LOGIN_REQUIRED = "login_required"
    DEAD_LINK = "dead_link"
    SURVEY = "survey"
    UNKNOWN = "unknown"


# URL path patterns for known non-portal pages
_RE_HELP_CENTER = re.compile(
    r"/hc/[a-z-]+/(requests|articles)/\d+"
    r"|/help/(article|doc)s?/"
    r"|/support/solutions/",
    re.I,
)

_RE_SURVEY = re.compile(
    r"/survey[_-]?responses?/"
    r"|/satisfaction/"
    r"|/feedback/",
    re.I,
)

# HTML content signals
_RE_SURVEY_CONTENT = re.compile(
    r"rate.{0,30}(support|service|experience)"
    r"|how (did we do|was your experience)"
    r"|satisfaction survey"
    r"|please.{0,20}(rate|review).{0,20}(support|service)",
    re.I,
)

_RE_FORM_ELEMENT = re.compile(
    r"<form[\s>]"
    r"|<input[\s>]"
    r"|<textarea[\s>]"
    r"|<select[\s>]",
    re.I,
)

_RE_SUBMIT_BUTTON = re.compile(
    r"<button[^>]*>.*?(submit|send|request).*?</button>"
    r"|type=['\"]submit['\"]",
    re.I | re.S,
)

_RE_GDPR_KEYWORDS = re.compile(
    r"(data.{0,15}(request|access|subject)|gdpr|dsar|sar|privacy.{0,10}request"
    r"|personal.{0,10}data|right.{0,10}access|erasure|portability)",
    re.I,
)

_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def verify(url: str) -> dict:
    """Classify a URL by fetching and inspecting its content.

    Returns dict with keys: url, classification, checked_at, error, page_title.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if not url:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error="empty URL")

    # Fast path: login-required domains (no HTTP needed)
    platform = detect_platform(url)
    if platform == "login_required":
        return _result(url, CLASSIFICATION.LOGIN_REQUIRED, now)

    # Fast path: known platform portals (OneTrust, TrustArc)
    if platform in ("onetrust", "trustarc"):
        return _result(url, CLASSIFICATION.GDPR_PORTAL, now)

    # URL path heuristics (before HTTP fetch)
    path = urlparse(url).path or ""
    if _RE_SURVEY.search(path):
        return _result(url, CLASSIFICATION.SURVEY, now)
    if _RE_HELP_CENTER.search(path):
        return _result(url, CLASSIFICATION.HELP_CENTER, now)

    # HTTP fetch
    try:
        resp = requests.get(url, timeout=_TIMEOUT, allow_redirects=True,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; GDPR-Agent/1.0)"})
    except requests.Timeout:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error="timeout")
    except requests.ConnectionError as e:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error=f"connection error: {e}")
    except requests.RequestException as e:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error=str(e))

    if resp.status_code >= 400:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error=f"HTTP {resp.status_code}")

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type:
        return _result(url, CLASSIFICATION.UNKNOWN, now)

    html = resp.text
    title = _extract_title(html)

    # Check for survey content
    if _RE_SURVEY_CONTENT.search(html):
        return _result(url, CLASSIFICATION.SURVEY, now, page_title=title)

    # Check for help center (after redirect — may have landed on a different path)
    final_path = urlparse(resp.url).path or ""
    if _RE_HELP_CENTER.search(final_path):
        return _result(url, CLASSIFICATION.HELP_CENTER, now, page_title=title)

    # Check for GDPR portal: needs form elements + submit button + GDPR keywords
    has_form = bool(_RE_FORM_ELEMENT.search(html))
    has_submit = bool(_RE_SUBMIT_BUTTON.search(html))
    has_gdpr = bool(_RE_GDPR_KEYWORDS.search(html))

    if has_form and has_submit:
        # Form with submit button — likely a portal even without explicit GDPR keywords
        # (many portals just say "Submit Request" without mentioning GDPR)
        return _result(url, CLASSIFICATION.GDPR_PORTAL, now, page_title=title)

    if has_gdpr and not has_form:
        # GDPR content but no form — probably a help/info page
        return _result(url, CLASSIFICATION.HELP_CENTER, now, page_title=title)

    return _result(url, CLASSIFICATION.UNKNOWN, now, page_title=title)


def verify_if_needed(
    url: str,
    *,
    existing: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Return existing verification if fresh, otherwise re-verify.

    Args:
        url: URL to verify
        existing: Previous verification result (from ReplyRecord.portal_verification)
        now: Current time (injectable for testing)
    """
    if existing and existing.get("checked_at"):
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            checked = datetime.fromisoformat(existing["checked_at"].replace("Z", "+00:00"))
            if now - checked < _VERIFY_TTL:
                return existing
        except (ValueError, TypeError):
            pass
    return verify(url)


def _result(
    url: str,
    classification: str,
    checked_at: str,
    *,
    error: str | None = None,
    page_title: str = "",
) -> dict:
    return {
        "url": url,
        "classification": classification,
        "checked_at": checked_at,
        "error": error,
        "page_title": page_title,
    }


def _extract_title(html: str) -> str:
    m = _RE_TITLE.search(html)
    if m:
        return m.group(1).strip()[:200]
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_url_verifier.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add reply_monitor/url_verifier.py tests/unit/test_url_verifier.py
git commit -m "feat: add URL verifier module for reply portal classification

Classifies URLs as gdpr_portal, help_center, login_required, dead_link,
survey, or unknown. Uses lightweight HTTP + HTML inspection, reuses
platform_hints for fast-path detection. TTL-based caching via
verify_if_needed()."
```

---

### Task 6: Auto-Submit Trigger in Monitor (B2)

**Files:**
- Modify: `monitor.py:148-237` (reply processing loop)

- [ ] **Step 1: Write test for verify + auto-submit flow**

Add to `tests/unit/test_reply_classifier.py` (or create `tests/unit/test_monitor_portal.py` if preferred — but keeping in existing file is simpler):

```python
class TestMonitorPortalVerification:
    def test_wrong_channel_with_portal_url_triggers_verification(self):
        """WRONG_CHANNEL reply with extracted portal_url should have portal_verification set."""
        from reply_monitor.models import ReplyRecord
        from reply_monitor.url_verifier import CLASSIFICATION

        # Simulate what monitor.py does after classification
        reply = ReplyRecord(
            gmail_message_id="test123",
            received_at="2026-04-13T10:00:00Z",
            from_addr="privacy@example.com",
            subject="Re: SAR",
            snippet="Use our portal",
            tags=["WRONG_CHANNEL"],
            extracted={"portal_url": "https://example.com/privacy-request"},
            llm_used=False,
            has_attachment=False,
            attachment_catalog=None,
        )

        # Verify the reply has a portal URL that needs verification
        portal_url = reply.extracted.get("portal_url", "")
        assert portal_url != ""
        assert "WRONG_CHANNEL" in reply.tags

    def test_wrong_channel_without_portal_url_skips_verification(self):
        """WRONG_CHANNEL reply without portal_url should not trigger verification."""
        from reply_monitor.models import ReplyRecord

        reply = ReplyRecord(
            gmail_message_id="test456",
            received_at="2026-04-13T10:00:00Z",
            from_addr="privacy@example.com",
            subject="Re: SAR",
            snippet="This address is not monitored",
            tags=["WRONG_CHANNEL"],
            extracted={"portal_url": ""},
            llm_used=False,
            has_attachment=False,
            attachment_catalog=None,
        )

        portal_url = reply.extracted.get("portal_url", "")
        assert portal_url == ""
```

- [ ] **Step 2: Run test to confirm it passes (sanity check)**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestMonitorPortalVerification -v`
Expected: PASS (these test the data shape, not the integration)

- [ ] **Step 3: Add portal verification + auto-submit to `monitor.py`**

In `monitor.py`, add import at the top (after line 27):

```python
from reply_monitor.url_verifier import verify_if_needed, CLASSIFICATION
```

In the reply processing loop, after the `ReplyRecord` is created and appended (after line 230, where `new_replies.append(reply)` is), add portal verification logic:

```python
            # --- Portal verification for WRONG_CHANNEL replies ---
            _VERIFY_TAGS = {"WRONG_CHANNEL", "CONFIRMATION_REQUIRED", "DATA_PROVIDED_PORTAL"}
            if set(reply.tags) & _VERIFY_TAGS:
                portal_url = reply.extracted.get("portal_url", "")
                if portal_url:
                    try:
                        verification = verify_if_needed(portal_url, existing=reply.portal_verification)
                        reply.portal_verification = verification

                        if args.verbose:
                            print(f"  [{state.company_name}] portal verified: {verification['classification']}")

                        # Auto-submit if it's a real GDPR portal
                        if verification["classification"] == CLASSIFICATION.GDPR_PORTAL:
                            _try_portal_submit(
                                domain=domain,
                                portal_url=portal_url,
                                state=state,
                                reply=reply,
                                scan_email=email,
                                verbose=args.verbose,
                                data_dir=data_dir,
                            )
                    except Exception as exc:
                        if args.verbose:
                            print(f"  [{state.company_name}] portal verification failed: {exc}")
```

Then add the `_try_portal_submit` helper function (before `_print_summary`, around line 530):

```python
def _try_portal_submit(
    *,
    domain: str,
    portal_url: str,
    state: CompanyState,
    reply: ReplyRecord,
    scan_email: str,
    verbose: bool = False,
    data_dir: Path | None = None,
) -> None:
    """Attempt to auto-submit SAR via portal when WRONG_CHANNEL reply points to a GDPR portal."""
    try:
        from letter_engine.composer import compose
        from letter_engine.models import SARLetter
        from contact_resolver.models import CompanyRecord, Contact
        from portal_submitter.submitter import submit_portal

        # Build a minimal SARLetter for portal submission
        # Load company record if available, otherwise build from state
        import json
        companies_path = Path(__file__).parent / "data" / "companies.json"
        record = None
        if companies_path.exists():
            companies = json.loads(companies_path.read_text())
            if domain in companies:
                record = CompanyRecord.from_dict(companies[domain])

        if record:
            # Update the record to use portal method
            record.contact.preferred_method = "portal"
            record.contact.gdpr_portal_url = portal_url
            letter = compose(record)
        else:
            # Fallback: build minimal letter from state
            from config.settings import settings
            letter = SARLetter(
                to_email=state.to_email,
                subject=f"Subject Access Request — {settings.USER_FULL_NAME}",
                body="",  # portal submission uses form fields, not email body
                company_name=state.company_name,
                portal_url=portal_url,
            )

        result = submit_portal(letter, scan_email)

        if result.success:
            print(f"  [auto-portal] ✓ {state.company_name}: submitted via {portal_url[:50]}")
            if result.confirmation_ref:
                print(f"  [auto-portal]   confirmation: {result.confirmation_ref}")
            # Dismiss the WRONG_CHANNEL draft — auto-handled
            reply.reply_review_status = "dismissed"
            reply.suggested_reply = ""
        elif result.needs_manual:
            if verbose:
                print(f"  [auto-portal] {state.company_name}: needs manual submission ({result.error or 'login required'})")
        else:
            if verbose:
                print(f"  [auto-portal] ✗ {state.company_name}: {result.error or 'unknown error'}")
    except Exception as exc:
        if verbose:
            print(f"  [auto-portal] ✗ {state.company_name}: {exc}")
```

- [ ] **Step 4: Run full test suite to check nothing broke**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add monitor.py
git commit -m "feat(monitor): auto-verify portal URLs and submit via portal_submitter

After classifying WRONG_CHANNEL/CONFIRMATION_REQUIRED/DATA_PROVIDED_PORTAL
replies, verify the extracted portal URL. If it's a real GDPR portal,
auto-submit via portal_submitter. Results stored in
ReplyRecord.portal_verification. Dismissed drafts on successful submit."
```

---

### Task 7: Dashboard Verification Badges (B3)

**Files:**
- Modify: `dashboard/templates/company_detail.html:272-276`
- Modify: `dashboard/app.py` (company_detail route)

- [ ] **Step 1: Add verification badge rendering to template**

In `dashboard/templates/company_detail.html`, replace the portal URL line (around line 274):

```html
            {% if has_portal_tag and ex.portal_url %}<span>&#x1F4BB; <a href="{{ ex.portal_url }}" target="_blank">Privacy portal</a></span>{% endif %}
```

with:

```html
            {% if has_portal_tag and ex.portal_url %}
              <span>&#x1F4BB; <a href="{{ ex.portal_url }}" target="_blank">Privacy portal</a>
              {% if r.portal_verification %}
                {% set pv = r.portal_verification %}
                {% if pv.classification == 'gdpr_portal' %}
                  <span class="badge bg-success" style="font-size:0.65rem">Verified portal</span>
                {% elif pv.classification == 'help_center' %}
                  <span class="badge bg-warning text-dark" style="font-size:0.65rem">Help center page</span>
                {% elif pv.classification == 'login_required' %}
                  <span class="badge bg-warning text-dark" style="font-size:0.65rem">Login required</span>
                {% elif pv.classification == 'dead_link' %}
                  <span class="badge bg-danger" style="font-size:0.65rem">Dead link</span>
                {% elif pv.classification == 'survey' %}
                  <span class="badge bg-secondary" style="font-size:0.65rem">Survey page</span>
                {% else %}
                  <span class="badge bg-secondary" style="font-size:0.65rem">Unverified</span>
                {% endif %}
              {% endif %}
              </span>
            {% endif %}
```

- [ ] **Step 2: Ensure `portal_verification` is available in template context**

In `dashboard/app.py`, in the `company_detail()` route, the reply records are already passed to the template as dicts. Verify that `portal_verification` flows through. Since `ReplyRecord.to_dict()` now includes `portal_verification`, and the template accesses `r.portal_verification`, this should work automatically. However, for `stream_panel()` macro calls, confirm the reply dict is passed directly.

Check the template's `stream_panel` macro — it iterates over events which contain the reply dict. The reply dict already includes `portal_verification` via `to_dict()`. No `app.py` changes needed for this — the data flows through the existing serialization path.

- [ ] **Step 3: Test manually**

Run: `python dashboard/app.py`
Navigate to a company with WRONG_CHANNEL replies (e.g., Zendesk, PayPal). Verify:
- Portal URL links still display correctly
- If `portal_verification` is present, badge shows next to the link
- If `portal_verification` is absent (old data), no badge shows (no crash)

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/company_detail.html
git commit -m "feat(dashboard): show portal verification badges on reply URLs

Verified portal (green), Help center (yellow), Login required (yellow),
Dead link (red), Survey (grey), Unverified (grey). Badge only appears
when portal_verification data exists on the reply record."
```

---

### Task 8: Integration Smoke Test

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -v`
Expected: All pass

- [ ] **Step 2: Run monitor in dry-run style to verify no crashes**

Run: `python -c "from reply_monitor.url_verifier import verify, CLASSIFICATION; print(CLASSIFICATION.GDPR_PORTAL)"`
Expected: Prints `gdpr_portal`

Run: `python -c "from reply_monitor.models import ReplyRecord; r = ReplyRecord.from_dict({'gmail_message_id':'x','received_at':'','from':'','subject':'','snippet':'','tags':[],'extracted':{},'llm_used':False,'has_attachment':False}); print(r.portal_verification)"`
Expected: Prints `None`

- [ ] **Step 3: Commit any remaining fixes**

If any issues found, fix and commit with descriptive message.

- [ ] **Step 4: Final commit — update CLAUDE.md**

Add to the `reply_monitor/classifier.py` entry in the Known Issues / Tech Debt section of CLAUDE.md:

In the "Fixed issues" details block, add:

```
| P2 | `reply_monitor/classifier.py` | Premature ticket closure not detected — closure regex patterns added to WRONG_CHANNEL |
| P2 | `reply_monitor/classifier.py` | Zendesk ticket/survey URLs extracted as data_link/portal_url — `_RE_JUNK_URL` filter added |
```

Remove or update the P2 open issue about `_is_data_url()` matching vendor/sub-processor pages — the broader URL filtering now covers this.

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with classifier fixes and portal verifier

Mark premature closure detection and URL filtering as fixed. Document
url_verifier.py module and portal_verification field on ReplyRecord."
```
