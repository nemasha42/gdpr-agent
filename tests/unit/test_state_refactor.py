"""Tests for data_dir parameter on load_state / save_state."""

import json
from pathlib import Path


def test_load_state_from_data_dir(tmp_path):
    from reply_monitor.state_manager import load_state

    state_file = tmp_path / "reply_state.json"
    state_file.write_text("{}")
    states = load_state("alice@gmail.com", data_dir=tmp_path)
    assert states == {}


def test_save_state_to_data_dir(tmp_path):
    from reply_monitor.state_manager import save_state, load_state
    from reply_monitor.models import CompanyState

    state = CompanyState(
        domain="example.com",
        company_name="Example",
        sar_sent_at="2026-01-01T00:00:00Z",
        to_email="privacy@example.com",
        subject="SAR",
        gmail_thread_id="thread1",
        deadline="2026-01-31",
    )
    save_state("alice@gmail.com", {"example.com": state}, data_dir=tmp_path)
    state_file = tmp_path / "reply_state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    safe_key = "alice_at_gmail_com"
    assert safe_key in data
    assert "example.com" in data[safe_key]
