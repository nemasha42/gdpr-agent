"""Unit tests for reply_monitor/schema_builder.py."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from reply_monitor.schema_builder import _call_llm, _read_sample, _sample_zip, build_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mock_llm_response(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


# ---------------------------------------------------------------------------
# _sample_zip
# ---------------------------------------------------------------------------


def test_sample_zip_returns_readable_files(tmp_path):
    zdata = _make_zip({"profile.json": b'{"name": "Alice"}', "search_history.csv": b"query,date\nfoo,2024-01-01"})
    zpath = tmp_path / "data.zip"
    zpath.write_bytes(zdata)
    samples = _sample_zip(zpath)
    assert len(samples) == 2
    names = {s["filename"] for s in samples}
    assert "profile.json" in names
    assert "search_history.csv" in names


def test_sample_zip_skips_binary_files(tmp_path):
    zdata = _make_zip({"profile.json": b'{"x":1}', "image.png": b"\x89PNG..."})
    zpath = tmp_path / "data.zip"
    zpath.write_bytes(zdata)
    samples = _sample_zip(zpath)
    assert len(samples) == 1
    assert samples[0]["filename"] == "profile.json"


def test_sample_zip_empty_zip(tmp_path):
    zdata = _make_zip({})
    zpath = tmp_path / "empty.zip"
    zpath.write_bytes(zdata)
    samples = _sample_zip(zpath)
    assert samples == []


def test_sample_zip_corrupt_zip(tmp_path):
    zpath = tmp_path / "corrupt.zip"
    zpath.write_bytes(b"not a zip file at all")
    samples = _sample_zip(zpath)
    assert samples == []


# ---------------------------------------------------------------------------
# build_schema
# ---------------------------------------------------------------------------


def test_build_schema_no_api_key(tmp_path):
    f = tmp_path / "data.json"
    f.write_bytes(b'{"user": "alice"}')
    result = build_schema(f, "")
    assert result == {}


def test_build_schema_unsupported_extension(tmp_path):
    f = tmp_path / "data.pdf"
    f.write_bytes(b"PDF content")
    result = build_schema(f, "dummy_key")
    assert result == {}


def test_build_schema_empty_json_file(tmp_path):
    f = tmp_path / "empty.json"
    f.write_bytes(b"")
    result = build_schema(f, "dummy_key")
    assert result == {}


def test_build_schema_returns_dict_on_success(tmp_path):
    f = tmp_path / "data.json"
    f.write_bytes(b'{"name": "Alice", "email": "alice@example.com"}')

    schema_response = json.dumps({
        "categories": [{"name": "Profile", "description": "User profile data", "fields": []}],
        "services": [{"name": "Platform", "description": "Main service"}],
        "export_meta": {"format": "JSON", "delivery": "Email", "timeline": "30 days"},
    })

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_llm_response(schema_response)

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = build_schema(f, "fake_key")

    assert "categories" in result
    assert result["categories"][0]["name"] == "Profile"


def test_build_schema_no_recognizable_categories(tmp_path):
    """LLM returns valid JSON but empty categories — should return empty dict or valid dict."""
    f = tmp_path / "data.json"
    f.write_bytes(b'{}')

    schema_response = json.dumps({"categories": [], "services": [], "export_meta": {}})
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_llm_response(schema_response)

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = build_schema(f, "fake_key")

    assert isinstance(result, dict)
    assert result.get("categories") == []


def test_build_schema_llm_returns_malformed_json(tmp_path):
    f = tmp_path / "data.json"
    f.write_bytes(b'{"name": "Alice"}')

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_llm_response("Not valid JSON at all")

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = build_schema(f, "fake_key")

    assert result == {}


def test_build_schema_corrupt_zip(tmp_path):
    f = tmp_path / "corrupt.zip"
    f.write_bytes(b"not a real zip")
    # Should not raise — returns empty dict (no samples)
    result = build_schema(f, "fake_key")
    assert result == {}


# ---------------------------------------------------------------------------
# Dynamic truncation
# ---------------------------------------------------------------------------


def test_call_llm_truncates_large_batches():
    """With many samples, per-file limit should be reduced to stay under 60 KB."""
    # 30 samples, 60KB total → max_per_file = 60000 // 30 = 2000
    samples = [{"filename": f"file{i}.json", "content": "x" * 3000} for i in range(30)]
    called_with: list[str] = []

    def fake_create(**kwargs):
        called_with.append(kwargs["messages"][0]["content"])
        raise RuntimeError("stop")

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = fake_create

    with patch("anthropic.Anthropic", return_value=mock_client):
        _call_llm(samples, "fake_key")

    # Should have been called (even though it raised) — check content length
    if called_with:
        # Each file content should be truncated to ≤ 2000 chars
        content = called_with[0]
        for i in range(min(3, len(samples))):
            filename = f"file{i}.json"
            idx = content.find(filename)
            if idx != -1:
                # The "x" repetition after the filename header should be ≤ 2000
                excerpt_start = content.find("x", idx)
                excerpt_end = content.find("\n=", excerpt_start) if "\n=" in content[excerpt_start:] else len(content)
                excerpt = content[excerpt_start:excerpt_end]
                assert len(excerpt) <= 2000
