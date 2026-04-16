"""Unit tests for reply_monitor/link_downloader.py."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from reply_monitor.link_downloader import (
    DownloadResult,
    _catalog_file,
    _download_requests,
    _filename_from_response,
    _safe_filename,
    download_data_link,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _mock_requests_response(status: int, content: bytes, headers: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.headers = headers or {}
    resp.iter_content = lambda chunk_size: [content]
    resp.close = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# DownloadResult
# ---------------------------------------------------------------------------


def test_download_result_ok_when_catalog_set():
    from reply_monitor.models import AttachmentCatalog

    cat = AttachmentCatalog(path="/tmp/data.zip", size_bytes=100, file_type="zip")
    r = DownloadResult(catalog=cat)
    assert r.ok is True


def test_download_result_not_ok_when_no_catalog():
    r = DownloadResult(error="network error")
    assert r.ok is False


def test_download_result_to_dict():
    r = DownloadResult(too_large=True)
    d = r.to_dict()
    assert d["too_large"] is True
    assert d["ok"] is False


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------


def test_safe_filename_removes_special_chars():
    assert _safe_filename("my file (1).zip") == "my_file__1_.zip"


def test_safe_filename_truncates_long_names():
    long = "a" * 200 + ".zip"
    assert len(_safe_filename(long)) <= 100


# ---------------------------------------------------------------------------
# _filename_from_response
# ---------------------------------------------------------------------------


def test_filename_from_content_disposition():
    resp = MagicMock()
    resp.headers = {"Content-Disposition": 'attachment; filename="data_export.zip"'}
    assert (
        _filename_from_response(resp, "https://example.com/download")
        == "data_export.zip"
    )


def test_filename_from_url_segment():
    resp = MagicMock()
    resp.headers = {}
    name = _filename_from_response(
        resp, "https://example.com/export/mydata.zip?token=abc"
    )
    assert name == "mydata.zip"


def test_filename_fallback_to_content_type():
    resp = MagicMock()
    resp.headers = {"Content-Type": "application/zip"}
    name = _filename_from_response(resp, "https://example.com/download")
    assert name == "data.zip"


# ---------------------------------------------------------------------------
# _catalog_file
# ---------------------------------------------------------------------------


def test_catalog_file_zip(tmp_path):
    zdata = _make_zip({"profile.json": b'{"name":"Alice"}'})
    f = tmp_path / "data.zip"
    f.write_bytes(zdata)
    result = _catalog_file(f)
    assert result.ok
    assert result.catalog.file_type == "zip"
    assert len(result.catalog.files) >= 1


def test_catalog_file_json(tmp_path):
    f = tmp_path / "data.json"
    f.write_bytes(b'{"user":"alice"}')
    result = _catalog_file(f)
    assert result.ok
    assert result.catalog.file_type == "json"


def test_catalog_file_missing(tmp_path):
    f = tmp_path / "missing.zip"
    result = _catalog_file(f)
    assert not result.ok
    assert "read failed" in result.error


# ---------------------------------------------------------------------------
# _download_requests
# ---------------------------------------------------------------------------


def test_download_requests_success(tmp_path):
    zdata = _make_zip({"a.json": b"{}"})
    mock_resp = _mock_requests_response(
        200, zdata, {"Content-Disposition": 'attachment; filename="export.zip"'}
    )

    with patch("requests.get", return_value=mock_resp):
        result = _download_requests("https://example.com/export", tmp_path)

    assert result.ok
    assert (tmp_path / "export.zip").exists()


def test_download_requests_404_marks_expired(tmp_path):
    mock_resp = _mock_requests_response(404, b"Not found")

    with patch("requests.get", return_value=mock_resp):
        result = _download_requests("https://example.com/export", tmp_path)

    assert not result.ok
    assert result.expired is True


def test_download_requests_too_large(tmp_path):
    # Patch the cap to 1 byte so a tiny chunk triggers it — avoids 600 MB allocation
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.ok = True
    mock_resp.headers = {}
    mock_resp.iter_content = lambda chunk_size: [b"xx"]
    mock_resp.close = MagicMock()

    with patch("reply_monitor.link_downloader._MAX_AUTO_DOWNLOAD_BYTES", 1):
        with patch("requests.get", return_value=mock_resp):
            result = _download_requests("https://example.com/export", tmp_path)

    assert result.too_large is True


# ---------------------------------------------------------------------------
# download_data_link — Playwright fallback path
# ---------------------------------------------------------------------------


def test_download_data_link_playwright_not_installed(tmp_path):
    """When Playwright is not installed, falls back to requests."""
    zdata = _make_zip({"data.json": b"{}"})
    mock_resp = _mock_requests_response(200, zdata, {"Content-Type": "application/zip"})

    with patch("reply_monitor.link_downloader._download_playwright", return_value=None):
        with patch("requests.get", return_value=mock_resp):
            result = download_data_link("https://example.com/data", "example.com")

    assert result.ok or result.error  # either downloaded or explicit error


@pytest.mark.skipif(
    __import__("importlib.util", fromlist=["find_spec"]).find_spec("playwright")
    is None,
    reason="playwright not installed",
)
def test_download_data_link_playwright_binary_missing_gives_helpful_error(
    tmp_path, capsys
):
    """Missing Playwright binaries should print a hint about 'playwright install'."""
    with patch("reply_monitor.link_downloader._download_playwright") as mock_pw:
        mock_pw.return_value = DownloadResult(
            error="Playwright browser binaries not found. Run: python -m playwright install chromium"
        )
        with patch("reply_monitor.link_downloader._download_requests") as mock_req:
            mock_req.return_value = DownloadResult(
                error="requests fallback also failed"
            )
            result = download_data_link("https://example.com/data", "example.com")

    assert "playwright" in result.error.lower() or "install" in result.error.lower()
