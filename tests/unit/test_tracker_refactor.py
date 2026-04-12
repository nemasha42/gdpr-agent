"""Tests for data_dir parameter on letter_engine/tracker.py functions."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from letter_engine.tracker import get_log, record_sent, record_subprocessor_request


def _make_letter():
    letter = MagicMock()
    letter.company_name = "Spotify"
    letter.method = "email"
    letter.to_email = "privacy@spotify.com"
    letter.subject = "Subject Access Request"
    letter.body = "Dear Spotify..."
    letter.gmail_message_id = "msg123"
    letter.gmail_thread_id = "thread123"
    return letter


def test_record_sent_to_data_dir(tmp_path):
    letter = _make_letter()
    record_sent(letter, data_dir=tmp_path)
    log_file = tmp_path / "sent_letters.json"
    assert log_file.exists()
    records = json.loads(log_file.read_text())
    assert len(records) == 1
    assert records[0]["company_name"] == "Spotify"


def test_get_log_from_data_dir(tmp_path):
    letter = _make_letter()
    record_sent(letter, data_dir=tmp_path)
    log = get_log(data_dir=tmp_path)
    assert len(log) == 1
    assert log[0]["company_name"] == "Spotify"


def test_record_subprocessor_request_to_data_dir(tmp_path):
    letter = _make_letter()
    record_subprocessor_request(letter, "spotify.com", data_dir=tmp_path)
    sp_file = tmp_path / "subprocessor_requests.json"
    assert sp_file.exists()
    records = json.loads(sp_file.read_text())
    assert len(records) == 1
    assert records[0]["domain"] == "spotify.com"
    assert records[0]["company_name"] == "Spotify"


def test_explicit_path_still_works(tmp_path):
    """Backward compat: explicit path= kwarg takes precedence."""
    letter = _make_letter()
    custom = tmp_path / "custom.json"
    record_sent(letter, path=custom)
    assert custom.exists()
    records = json.loads(custom.read_text())
    assert len(records) == 1


def test_path_takes_precedence_over_data_dir(tmp_path):
    """When both path and data_dir are given, path wins."""
    letter = _make_letter()
    custom = tmp_path / "custom.json"
    record_sent(letter, path=custom, data_dir=tmp_path)
    assert custom.exists()
    # data_dir file should NOT exist since path was explicit
    assert not (tmp_path / "sent_letters.json").exists()


def test_get_log_empty_data_dir(tmp_path):
    """get_log returns [] for a fresh data_dir with no file."""
    log = get_log(data_dir=tmp_path)
    assert log == []


def test_multiple_records_append(tmp_path):
    """Multiple record_sent calls append to the same file."""
    letter1 = _make_letter()
    letter2 = _make_letter()
    letter2.company_name = "Netflix"
    record_sent(letter1, data_dir=tmp_path)
    record_sent(letter2, data_dir=tmp_path)
    log = get_log(data_dir=tmp_path)
    assert len(log) == 2
    assert log[0]["company_name"] == "Spotify"
    assert log[1]["company_name"] == "Netflix"
