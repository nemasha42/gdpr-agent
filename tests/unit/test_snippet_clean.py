from dashboard.app import _clean_snippet, _dedup_reply_rows, _is_human_friendly

MESSY_INPUTS = [
    ("Hello=20World", "Hello World"),  # QP space
    ("AT&amp;T", "AT&T"),  # HTML entity
    ("click%20here", "click here"),  # URL encoding
    ("50%", "50%"),  # legitimate % — should not change
    ("don\u200bt reply", "dont reply"),  # zero-width
    ("reply=3Dnow", "reply=now"),  # QP equals
]


def test_clean_snippet_decodes_artifacts():
    for raw, expected in MESSY_INPUTS:
        assert (
            _clean_snippet(raw) == expected
        ), f"Input {raw!r}: expected {expected!r}, got {_clean_snippet(raw)!r}"


def test_clean_snippet_output_is_human_friendly():
    for raw, _ in MESSY_INPUTS:
        result = _clean_snippet(raw)
        assert _is_human_friendly(
            result
        ), f"Not human-friendly after cleaning {raw!r}: {result!r}"


def test_legitimate_percent_unchanged():
    assert "50%" in _clean_snippet("Score: 50%")


def test_html_nbsp_becomes_space():
    assert _clean_snippet("hello&nbsp;world") == "hello world"


def test_multiple_spaces_collapsed():
    assert _clean_snippet("hello   world") == "hello world"


def test_empty_string():
    assert _clean_snippet("") == ""


# ---------------------------------------------------------------------------
# _dedup_reply_rows
# ---------------------------------------------------------------------------


def _row(msg_id: str) -> dict:
    return {"gmail_message_id": msg_id, "subject": "Re: SAR"}


class TestDedupReplyRows:
    def test_no_sp_rows_returns_sar_unchanged(self):
        sar = [_row("a"), _row("b")]
        assert _dedup_reply_rows(sar, []) == sar

    def test_shared_ids_removed_from_sar(self):
        """Replies that appear in both SAR and SP state are hidden from the SAR section.

        Root cause: when SAR and SP requests share the same inbox (e.g. support@whop.com),
        the SAR monitor's address-search fallback picks up SP replies and stores them in
        both reply_state.json and subprocessor_reply_state.json. The company_detail page
        then renders both reply_rows and sp_reply_rows, causing the same message to appear
        twice. The fix removes duplicates from reply_rows, keeping them only in sp_reply_rows.
        """
        sar = [_row("shared1"), _row("sar-only"), _row("shared2")]
        sp = [_row("shared1"), _row("shared2"), _row("sp-only")]
        result = _dedup_reply_rows(sar, sp)
        ids = [r["gmail_message_id"] for r in result]
        assert ids == ["sar-only"]

    def test_no_overlap_returns_sar_unchanged(self):
        sar = [_row("a"), _row("b")]
        sp = [_row("c"), _row("d")]
        assert _dedup_reply_rows(sar, sp) == sar

    def test_all_sar_rows_are_sp_rows_returns_empty(self):
        sar = [_row("x"), _row("y")]
        sp = [_row("x"), _row("y")]
        assert _dedup_reply_rows(sar, sp) == []

    def test_empty_sar_returns_empty(self):
        assert _dedup_reply_rows([], [_row("a")]) == []
