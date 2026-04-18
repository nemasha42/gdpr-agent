# Customer-Naming Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove customer-specific naming from test names, production regex, and variables — tests and code should describe patterns, not individual customers.

**Architecture:** 4 production regex changes in `classifier.py` (remove 3 redundant customer-specific alternatives, generalize 1 URL pattern). 18 test renames across 6 files. 1 variable rename. 7 test deletions (duplicates after generalization). Category D (comments/docstrings) skipped per user decision. Category E (fixture data) stays for fidelity.

**Tech Stack:** Python, pytest, regex

**Acceptance criteria:**
- No customer names in test function names
- No customer domain patterns in production regex (hrtechprivacy, glassdoor domain-specific)
- No customer-named variables
- Full test suite: 675 passed + 1 known failure (682 - 7 deleted)
- Platform names (Zendesk, Ketch, OneTrust, etc.) remain untouched

**Reference:** `docs/customer-naming-audit.md` has the full audit with line numbers.

---

### Task 1: Remove customer-specific regex alternatives from classifier.py (B1, B2, B4)

**Files:**
- Modify: `reply_monitor/classifier.py:157` (B1)
- Modify: `reply_monitor/classifier.py:206` (B2)
- Modify: `reply_monitor/classifier.py:299` (B4)

These three regex alternatives are redundant — each has generic counterparts already in the same pattern that cover the behavioral signal.

- [ ] **Step 1: Remove B1 — `hrtechprivacy\.com/confirm` from CONFIRMATION_REQUIRED snippet regex**

In `reply_monitor/classifier.py`, line 157, delete the alternative. Lines 154–156 already cover the behavioral signal.

Before (lines 153–159):
```python
                re.compile(
                    r"will not begin processing.{0,40}until you have confirmed"
                    r"|confirm.{0,20}request.{0,20}button"
                    r"|confirm request"
                    r"|hrtechprivacy\.com/confirm"
                    r"|click.{0,30}confirm",
                    re.I,
                ),
```

After:
```python
                re.compile(
                    r"will not begin processing.{0,40}until you have confirmed"
                    r"|confirm.{0,20}request.{0,20}button"
                    r"|confirm request"
                    r"|click.{0,30}confirm",
                    re.I,
                ),
```

- [ ] **Step 2: Remove B2 — `requests\.hrtechprivacy\.com` from WRONG_CHANNEL snippet regex**

In `reply_monitor/classifier.py`, line 206, delete the alternative. Surrounding patterns (`please submit via`, `privacy portal`) already cover the signal.

Before (lines 204–207):
```python
                    r"|please submit via|privacy portal|dsar portal"
                    r"|online form at|submit your request at"
                    r"|requests\.hrtechprivacy\.com"
                    r"|use our (online|web) (form|portal|tool)"
```

After:
```python
                    r"|please submit via|privacy portal|dsar portal"
                    r"|online form at|submit your request at"
                    r"|use our (online|web) (form|portal|tool)"
```

- [ ] **Step 3: Remove B4 — `glassdoor\.com/dyd/download\?token=` from DATA_PROVIDED snippet regex**

In `reply_monitor/classifier.py`, line 299, delete the alternative. The generic `_RE_DOWNLOAD_URL` at line 431 already captures the same URL pattern.

Before (lines 296–300):
```python
                re.compile(
                    r"data file is now available for download"
                    r"|download your.{0,30}personal data"
                    r"|download link will expire"
                    r"|glassdoor\.com/dyd/download\?token="
                    r"|access your.{0,20}data.{0,20}link"
```

After:
```python
                re.compile(
                    r"data file is now available for download"
                    r"|download your.{0,30}personal data"
                    r"|download link will expire"
                    r"|access your.{0,20}data.{0,20}link"
```

- [ ] **Step 4: Run classifier tests to verify no regressions**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py -v`
Expected: All pass — tests that used to hit these alternatives still pass via generic patterns.

- [ ] **Step 5: Commit**

```bash
git add reply_monitor/classifier.py
git commit -m "refactor: remove 3 customer-specific regex alternatives from classifier

Generic counterparts already cover the same behavioral signals.
B1: hrtechprivacy.com/confirm (redundant with confirm...request...button)
B2: requests.hrtechprivacy.com (redundant with 'please submit via')
B4: glassdoor.com/dyd/download (redundant with _RE_DOWNLOAD_URL)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Generalize `_RE_CONFIRM_URL` in classifier.py (B3)

**Files:**
- Modify: `reply_monitor/classifier.py:428-429`

The current pattern matches only one customer's URL shape. Generalize to match any domain with a `/confirm/` path segment.

- [ ] **Step 1: Generalize `_RE_CONFIRM_URL`**

In `reply_monitor/classifier.py`, lines 428–429:

Before:
```python
_RE_CONFIRM_URL = re.compile(
    r"https://requests\.hrtechprivacy\.com/confirm/[\w/-]+", re.I
)
```

After:
```python
_RE_CONFIRM_URL = re.compile(
    r"https?://\S+/confirm/[\w/-]+", re.I
)
```

Used only at line 799 in `_extract()` to search `full_text`. The existing test A5 fixture URL (`https://requests.hrtechprivacy.com/confirm/abc-123-xyz`) still matches this generalized pattern.

- [ ] **Step 2: Run classifier tests**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestConfirmationRequired -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add reply_monitor/classifier.py
git commit -m "refactor: generalize _RE_CONFIRM_URL to match any /confirm/ path

Was locked to a single customer's URL shape. Now matches any
https?://domain/confirm/... URL pattern.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Update A6 fixture and rename 11 tests in test_reply_classifier.py

**Files:**
- Modify: `tests/unit/test_reply_classifier.py` — 11 renames + 1 fixture change

A6 fixture must change because B2 removed the regex alternative that matched the old snippet.

- [ ] **Step 1: Update A6 fixture snippet and rename (line 221–223)**

The old snippet `"Submit via requests.hrtechprivacy.com/submit"` only matched via the now-removed `requests\.hrtechprivacy\.com` alternative. Replace with text that matches generic patterns `please submit via` and `privacy portal`.

Before:
```python
    def test_hrtechprivacy_url(self):
        result = classify(msg(snippet="Submit via requests.hrtechprivacy.com/submit"))
        assert "WRONG_CHANNEL" in result.tags
```

After:
```python
    def test_third_party_privacy_portal_url(self):
        result = classify(
            msg(
                snippet="Please submit via our privacy portal at https://privacy.example.com/submit"
            )
        )
        assert "WRONG_CHANNEL" in result.tags
```

- [ ] **Step 2: Rename remaining 10 tests (function name only, no body changes)**

| Line | Old name | New name |
|------|----------|----------|
| 52 | `test_google_group_rejection` | `test_group_permission_bounce` |
| 87 | `test_google_ticket_format` | `test_numeric_case_id_auto_acknowledged` |
| 93 | `test_substack_request_received` | `test_bracketed_request_received_subject` |
| 127 | `test_hrtechprivacy_url` (TestConfirmationRequired) | `test_confirm_before_processing_snippet` |
| 139 | `test_hrtechprivacy_url_extracted` | `test_confirmation_url_extracted` |
| 237 | `test_finalroundai_snippet` | `test_self_service_portal_mention_is_wrong_channel` |
| 249 | `test_zendesk_ticket_set_to_solved` | `test_ticket_solved_without_data_is_wrong_channel` |
| 330 | `test_data_link_glassdoor` | `test_data_link_proprietary_token_url` |
| 344 | `test_data_link_full_token_with_colons` | `test_data_link_token_with_colon_segments` |
| 459 | `test_google_ticket_ref` | `test_numeric_case_id_ref_extracted` |

- [ ] **Step 3: Run classifier tests**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py -v`
Expected: All pass under new names.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_reply_classifier.py
git commit -m "refactor: rename 11 customer-named classifier tests to describe patterns

Also updates A6 fixture snippet to match generic WRONG_CHANNEL
patterns after B2 regex alternative removal.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Rename and delete tests in test_company_normalizer.py

**Files:**
- Modify: `tests/unit/test_company_normalizer.py` — 4 renames, 4 deletions

After generalization, 4 tests are duplicates testing the same pattern with different company names.

- [ ] **Step 1: Delete 4 duplicate tests**

Delete these entire test functions (including blank lines between them):

1. `test_co_uk_tld_deliveroo` (lines 32–33) — duplicate of `test_co_uk_tld` (line 28)
2. `test_known_exception_substack` (lines 99–100) — duplicate of A14 after rename
3. `test_known_exception_linkedin` (lines 107–108) — duplicate of A16 after rename
4. `test_normalize_instagram_returns_facebook` (lines 155–156) — duplicate of A18 after rename

- [ ] **Step 2: Rename 4 tests**

| Line | Old name | New name |
|------|----------|----------|
| 91 | `test_known_exception_facebookmail` | `test_known_exception_alternate_mail_domain` |
| 95 | `test_known_exception_glassdoor` | `test_known_exception_preserves_casing` |
| 103 | `test_known_exception_github` | `test_known_exception_mixed_case` |
| 147 | `test_normalize_youtube_returns_google` | `test_alias_resolves_to_parent_group` |

- [ ] **Step 3: Run normalizer tests**

Run: `.venv/bin/pytest tests/unit/test_company_normalizer.py -v`
Expected: All remaining tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_company_normalizer.py
git commit -m "refactor: rename 4 and delete 4 customer-named normalizer tests

Renames describe patterns (alternate mail domain, preserves casing,
mixed case, alias resolution). Deletions remove duplicates testing
the same pattern with different company names.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Rename and delete tests in test_portal_submitter.py

**Files:**
- Modify: `tests/unit/test_portal_submitter.py` — 1 rename, 2 deletions

- [ ] **Step 1: Delete 2 duplicate tests and rename 1**

Delete:
- `test_login_required_apple` (lines 83–84) — duplicate of A20 after generalization
- `test_login_required_meta` (lines 86–87) — duplicate of A20 after generalization

Rename:
- `test_login_required_google` (line 77) → `test_login_required_account_portal`

Before (lines 77–87):
```python
    def test_login_required_google(self):
        assert (
            detect_platform("https://myaccount.google.com/data-and-privacy")
            == "login_required"
        )

    def test_login_required_apple(self):
        assert detect_platform("https://privacy.apple.com") == "login_required"

    def test_login_required_meta(self):
        assert detect_platform("https://www.meta.com/dyi") == "login_required"
```

After:
```python
    def test_login_required_account_portal(self):
        assert (
            detect_platform("https://myaccount.google.com/data-and-privacy")
            == "login_required"
        )
```

- [ ] **Step 2: Run portal submitter tests**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py -v`
Expected: All remaining tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_portal_submitter.py
git commit -m "refactor: rename 1 and delete 2 duplicate login_required tests

Remaining test covers account-portal URL detection pattern.
Apple and Meta tests were identical pattern (login_required).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Delete duplicate test in test_url_verifier.py

**Files:**
- Modify: `tests/unit/test_url_verifier.py` — 1 deletion

- [ ] **Step 1: Delete `test_apple_login_required`**

Delete lines 76–78:
```python
    def test_apple_login_required(self):
        result = verify("https://privacy.apple.com/account")
        assert result["classification"] == CLASSIFICATION.LOGIN_REQUIRED
```

`test_login_required_platform` on line 71 already covers the same pattern.

- [ ] **Step 2: Run url verifier tests**

Run: `.venv/bin/pytest tests/unit/test_url_verifier.py -v`
Expected: All remaining tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_url_verifier.py
git commit -m "refactor: delete duplicate login_required URL verifier test

test_login_required_platform already covers the same pattern.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Rename tests in test_auth_routes.py and test_preprocessor.py

**Files:**
- Modify: `tests/unit/test_auth_routes.py:98`
- Modify: `tests/unit/test_preprocessor.py:103`

- [ ] **Step 1: Rename in test_auth_routes.py**

Line 98: `test_login_google_redirects_to_oauth` → `test_login_redirects_to_oauth_provider`

```python
# Before
def test_login_google_redirects_to_oauth(app, tmp_path):

# After
def test_login_redirects_to_oauth_provider(app, tmp_path):
```

- [ ] **Step 2: Rename in test_preprocessor.py**

Line 103: `test_twitter_js_wrapper_unwrapped` → `test_js_variable_assignment_wrapper_unwrapped`

```python
# Before
    def test_twitter_js_wrapper_unwrapped(self, tmp_path):

# After
    def test_js_variable_assignment_wrapper_unwrapped(self, tmp_path):
```

- [ ] **Step 3: Run both test files**

Run: `.venv/bin/pytest tests/unit/test_auth_routes.py tests/unit/test_preprocessor.py -v`
Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_auth_routes.py tests/unit/test_preprocessor.py
git commit -m "refactor: rename 2 customer-named tests in auth_routes and preprocessor

test_login_google_redirects_to_oauth → test_login_redirects_to_oauth_provider
test_twitter_js_wrapper_unwrapped → test_js_variable_assignment_wrapper_unwrapped

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Rename variable in test_resolver.py (C1)

**Files:**
- Modify: `tests/unit/test_resolver.py` — rename `_DATAOWNERS_SPOTIFY` → `_DATAOWNERS_OVERRIDE_ENTRY` at all 5 occurrences

- [ ] **Step 1: Rename all occurrences**

5 occurrences to change:
- Line 41: `_DATAOWNERS_SPOTIFY: dict = {` → `_DATAOWNERS_OVERRIDE_ENTRY: dict = {`
- Line 304: `dataowners=_DATAOWNERS_SPOTIFY` → `dataowners=_DATAOWNERS_OVERRIDE_ENTRY`
- Line 317: `dataowners=_DATAOWNERS_SPOTIFY` → `dataowners=_DATAOWNERS_OVERRIDE_ENTRY`
- Line 331: `_DATAOWNERS_SPOTIFY["spotify.com"]` → `_DATAOWNERS_OVERRIDE_ENTRY["spotify.com"]`
- Line 596: `dataowners=_DATAOWNERS_SPOTIFY` → `dataowners=_DATAOWNERS_OVERRIDE_ENTRY`

Use `replace_all=True` to rename all at once. Fixture data inside the dict stays (Category E).

- [ ] **Step 2: Run resolver tests**

Run: `.venv/bin/pytest tests/unit/test_resolver.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_resolver.py
git commit -m "refactor: rename _DATAOWNERS_SPOTIFY → _DATAOWNERS_OVERRIDE_ENTRY

Variable name now describes the fixture role, not a specific customer.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Full test suite verification

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: **675 passed, 1 failed** (682 original - 7 deleted = 675 + 1 known `test_portal_submitter` settings mock failure).

- [ ] **Step 2: Verify no customer names in test function names**

Run:
```bash
grep -rn "def test_.*\(google\|glassdoor\|spotify\|substack\|deliveroo\|facebook\|instagram\|youtube\|linkedin\|apple\|meta\|twitter\|finalround\|hrtechprivacy\)" tests/unit/*.py
```
Expected: Only matches in legitimate platform reference tests (`test_zendesk_*`, `test_salesforce`).

- [ ] **Step 3: Verify no customer domain patterns in production regex**

Run:
```bash
grep -n "hrtechprivacy\|glassdoor" reply_monitor/classifier.py
```
Expected: Only matches in comments (Category D — intentionally unchanged per user decision).
