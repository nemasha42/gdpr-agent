"""Tests for data_dir parameter on scan_state load/save."""

from pathlib import Path


def test_load_scan_state_from_data_dir(tmp_path):
    from dashboard.scan_state import load_scan_state, save_scan_state

    save_scan_state("alice@gmail.com", {"status": "paused"}, data_dir=tmp_path)
    state = load_scan_state("alice@gmail.com", data_dir=tmp_path)
    assert state["status"] == "paused"


def test_scan_state_file_location(tmp_path):
    from dashboard.scan_state import save_scan_state

    save_scan_state("alice@gmail.com", {"status": "done"}, data_dir=tmp_path)
    assert (tmp_path / "scan_state.json").exists()
