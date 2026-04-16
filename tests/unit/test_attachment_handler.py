"""Unit tests for reply_monitor/attachment_handler.py."""

import base64
import csv
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch


from reply_monitor.attachment_handler import (
    _catalog_csv,
    _catalog_json,
    _catalog_zip,
    _guess_categories_from_filename,
    handle_attachment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_zip_bytes(filenames: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in filenames:
            zf.writestr(name, f"content of {name}")
    return buf.getvalue()


def _make_service(attachment_data: bytes):
    """Mock Gmail service that returns base64-encoded attachment data."""
    service = MagicMock()
    encoded = base64.urlsafe_b64encode(attachment_data).decode()
    service.users().messages().attachments().get().execute.return_value = {
        "data": encoded
    }
    return service


# ---------------------------------------------------------------------------
# _guess_categories_from_filename tests
# ---------------------------------------------------------------------------


class TestGuessCategories:
    def test_location_filename(self):
        cats = _guess_categories_from_filename("location_history.json")
        assert "Location" in cats

    def test_search_history(self):
        cats = _guess_categories_from_filename("search_activity.csv")
        assert "Search History" in cats

    def test_purchase_history(self):
        cats = _guess_categories_from_filename("purchase_orders.json")
        assert "Purchase History" in cats

    def test_no_match_returns_empty(self):
        cats = _guess_categories_from_filename("abcxyz123.bin")
        assert cats == []

    def test_advertising_data(self):
        cats = _guess_categories_from_filename("ad_targeting_data.json")
        assert "Advertising" in cats

    def test_profile_data(self):
        cats = _guess_categories_from_filename("user_profile.json")
        assert "Profile Data" in cats


# ---------------------------------------------------------------------------
# _catalog_zip tests
# ---------------------------------------------------------------------------


class TestCatalogZip:
    def test_lists_files(self):
        data = _make_zip_bytes(["profile.json", "location_history.csv", "ads.json"])
        files, cats = _catalog_zip(data, "archive.zip")
        filenames = [f.filename for f in files]
        assert "profile.json" in filenames
        assert "location_history.csv" in filenames
        assert len(files) == 3

    def test_detects_categories(self):
        data = _make_zip_bytes(["location_data.json", "search_history.csv"])
        _, cats = _catalog_zip(data, "archive.zip")
        assert "Location" in cats
        assert "Search History" in cats

    def test_records_file_sizes(self):
        data = _make_zip_bytes(["file.json"])
        files, _ = _catalog_zip(data, "archive.zip")
        assert files[0].size_bytes > 0

    def test_bad_zip_returns_fallback(self):
        files, _ = _catalog_zip(b"NOT A ZIP FILE", "archive.zip")
        assert len(files) == 1
        assert files[0].filename == "archive.zip"

    def test_skips_directories(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.mkdir("subdir/")
            zf.writestr("subdir/file.json", "{}")
        data = buf.getvalue()
        files, _ = _catalog_zip(data, "archive.zip")
        # Only the file, not the directory
        assert all(not f.filename.endswith("/") for f in files)


# ---------------------------------------------------------------------------
# _catalog_json tests
# ---------------------------------------------------------------------------


class TestCatalogJson:
    def test_extracts_top_level_keys(self):
        payload = json.dumps(
            {
                "location_history": [],
                "profile": {},
                "search_activity": [],
            }
        ).encode()
        files, cats = _catalog_json(payload, "data.json", len(payload))
        assert "Location" in cats
        assert "Profile Data" in cats
        assert "Search History" in cats

    def test_handles_list_of_dicts(self):
        payload = json.dumps([{"purchase_id": 1, "amount": 9.99}]).encode()
        _, cats = _catalog_json(payload, "orders.json", len(payload))
        assert "Purchase History" in cats

    def test_bad_json_returns_no_categories(self):
        files, cats = _catalog_json(b"NOT JSON", "data.json", 8)
        assert isinstance(files, list)
        assert isinstance(cats, list)


# ---------------------------------------------------------------------------
# _catalog_csv tests
# ---------------------------------------------------------------------------


class TestCatalogCsv:
    def test_extracts_column_headers(self):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["user_id", "location_lat", "location_lon", "timestamp"])
        writer.writerows([[1, 51.5, -0.1, "2026-01-01"]])
        data = buf.getvalue().encode()
        files, cats = _catalog_csv(data, "location.csv", len(data))
        assert "Location" in cats

    def test_category_from_filename(self):
        buf = io.StringIO()
        csv.writer(buf).writerow(["id", "value"])
        data = buf.getvalue().encode()
        _, cats = _catalog_csv(data, "search_history.csv", len(data))
        assert "Search History" in cats

    def test_returns_file_entry(self):
        buf = io.StringIO()
        csv.writer(buf).writerow(["id"])
        data = buf.getvalue().encode()
        files, _ = _catalog_csv(data, "data.csv", len(data))
        assert len(files) == 1
        assert files[0].file_type == "csv"


# ---------------------------------------------------------------------------
# handle_attachment integration tests (mocked Gmail service)
# ---------------------------------------------------------------------------


class TestHandleAttachment:
    def test_downloads_and_saves_zip(self, tmp_path):
        zip_data = _make_zip_bytes(["profile.json", "location.csv"])
        service = _make_service(zip_data)
        part = {
            "filename": "mydata.zip",
            "attachmentId": "att001",
            "size": len(zip_data),
        }

        with patch("reply_monitor.attachment_handler._RECEIVED_DIR", tmp_path):
            catalog = handle_attachment(service, "msg001", part, "example.com")

        assert catalog is not None
        assert catalog.file_type == "zip"
        assert len(catalog.files) == 2
        assert Path(catalog.path).exists()

    def test_returns_none_on_download_failure(self, tmp_path):
        service = MagicMock()
        service.users().messages().attachments().get().execute.side_effect = Exception(
            "API error"
        )
        part = {"filename": "data.zip", "attachmentId": "att001", "size": 100}

        with patch("reply_monitor.attachment_handler._RECEIVED_DIR", tmp_path):
            catalog = handle_attachment(service, "msg001", part, "example.com")

        assert catalog is None

    def test_json_attachment_catalog(self, tmp_path):
        json_data = json.dumps({"location_history": [], "profile": {}}).encode()
        service = _make_service(json_data)
        part = {
            "filename": "data.json",
            "attachmentId": "att002",
            "size": len(json_data),
        }

        with patch("reply_monitor.attachment_handler._RECEIVED_DIR", tmp_path):
            catalog = handle_attachment(service, "msg002", part, "example.com")

        assert catalog is not None
        assert "Location" in catalog.categories

    def test_save_path_uses_domain(self, tmp_path):
        zip_data = _make_zip_bytes(["file.json"])
        service = _make_service(zip_data)
        part = {"filename": "data.zip", "attachmentId": "att003", "size": len(zip_data)}

        with patch("reply_monitor.attachment_handler._RECEIVED_DIR", tmp_path):
            catalog = handle_attachment(service, "msg003", part, "glassdoor.com")

        assert "glassdoor.com" in catalog.path
