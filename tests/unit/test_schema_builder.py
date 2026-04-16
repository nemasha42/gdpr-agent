"""Unit tests for reply_monitor/schema_builder.py."""

import io
import json
import zipfile
from unittest.mock import MagicMock, patch


from reply_monitor.schema_builder import build_schema
from reply_monitor.models import AttachmentCatalog


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
    resp.usage.input_tokens = 500
    resp.usage.output_tokens = 200
    return resp


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

    schema_response = json.dumps(
        {
            "categories": [
                {"name": "Profile", "description": "User profile data", "fields": []}
            ],
            "services": [{"name": "Platform", "description": "Main service"}],
            "export_meta": {
                "format": "JSON",
                "delivery": "Email",
                "timeline": "30 days",
            },
        }
    )

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_llm_response(schema_response)

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = build_schema(f, "fake_key")

    assert "categories" in result
    assert result["categories"][0]["name"] == "Profile"


def test_build_schema_no_recognizable_categories(tmp_path):
    """LLM returns valid JSON but empty categories — should return empty dict or valid dict."""
    f = tmp_path / "data.json"
    f.write_bytes(b"{}")

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
    mock_client.messages.create.return_value = _mock_llm_response(
        "Not valid JSON at all"
    )

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
# AttachmentCatalog new schema fields
# ---------------------------------------------------------------------------


class TestAttachmentCatalogNewFields:
    def test_new_export_meta_fields_serialize(self):
        cat = AttachmentCatalog(
            path="/tmp/data.zip",
            size_bytes=1024,
            file_type="zip",
        )
        cat.export_meta = {
            "format": "ZIP",
            "formats_found": ["json", "csv"],
            "delivery": "Download link",
            "timeline": "30 days",
            "structure": "Organized by service in folders",
            "total_files": 12,
            "total_records_estimate": 5000,
        }
        d = cat.to_dict()
        assert d["export_meta"]["formats_found"] == ["json", "csv"]
        assert d["export_meta"]["total_files"] == 12
        assert d["export_meta"]["total_records_estimate"] == 5000
        assert d["export_meta"]["structure"] == "Organized by service in folders"

    def test_new_category_fields_in_schema(self):
        cat = AttachmentCatalog(
            path="/tmp/data.zip",
            size_bytes=1024,
            file_type="zip",
            schema=[
                {
                    "name": "Streaming History",
                    "description": "Music listening history",
                    "structure_type": "array",
                    "record_count": 12847,
                    "provenance": "observed",
                    "fields": [
                        {
                            "name": "trackName",
                            "type": "string",
                            "example": "Everything In Its Right Place",
                            "description": "Name of the track played",
                            "sensitive": False,
                            "provenance": "observed",
                        }
                    ],
                }
            ],
        )
        d = cat.to_dict()
        category = d["schema"][0]
        assert category["structure_type"] == "array"
        assert category["record_count"] == 12847
        assert category["provenance"] == "observed"
        assert category["fields"][0]["sensitive"] is False
        assert category["fields"][0]["provenance"] == "observed"

    def test_backward_compat_old_schema_still_works(self):
        cat = AttachmentCatalog(
            path="/tmp/data.zip",
            size_bytes=1024,
            file_type="zip",
            schema=[
                {
                    "name": "Profile",
                    "description": "User profile",
                    "fields": [
                        {
                            "name": "email",
                            "type": "string",
                            "example": "a@b.com",
                            "description": "Email",
                        }
                    ],
                }
            ],
        )
        d = cat.to_dict()
        assert d["schema"][0]["name"] == "Profile"


# ---------------------------------------------------------------------------
# Enriched schema (new fields from preprocessor integration)
# ---------------------------------------------------------------------------


class TestEnrichedSchema:
    def test_build_schema_includes_new_fields(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_bytes(
            json.dumps(
                [
                    {"name": "Alice", "email": "a@b.com", "created": "2024-01-01"},
                ]
            ).encode()
        )

        schema_response = json.dumps(
            {
                "categories": [
                    {
                        "name": "User Accounts",
                        "description": "Registered user profile data",
                        "structure_type": "array",
                        "record_count": 1,
                        "provenance": "provided",
                        "fields": [
                            {
                                "name": "name",
                                "type": "string",
                                "example": "Alice",
                                "description": "User's full name",
                                "sensitive": False,
                                "provenance": "provided",
                            }
                        ],
                    }
                ],
                "services": [{"name": "Platform", "description": "Main service"}],
                "export_meta": {
                    "format": "JSON",
                    "formats_found": ["json"],
                    "delivery": "Email attachment",
                    "timeline": "30 days",
                    "structure": "Single JSON file with user records",
                    "total_files": 1,
                    "total_records_estimate": 1,
                },
            }
        )

        mock_client = MagicMock()
        mock_resp = _mock_llm_response(schema_response)
        mock_client.messages.create.return_value = mock_resp

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = build_schema(f, "fake_key", company_name="testco")

        assert result["categories"][0]["structure_type"] == "array"
        assert result["categories"][0]["provenance"] == "provided"
        assert result["categories"][0]["fields"][0]["sensitive"] is False
        assert result["export_meta"]["formats_found"] == ["json"]

    def test_prompt_includes_preprocessor_context(self, tmp_path):
        zdata = _make_zip(
            {
                "profile/account.json": json.dumps({"name": "Alice"}).encode(),
                "history/searches.csv": b"query,date\nfoo,2024-01-01",
            }
        )
        zpath = tmp_path / "export.zip"
        zpath.write_bytes(zdata)

        captured_prompt: list[str] = []

        def fake_create(**kwargs):
            captured_prompt.append(kwargs["messages"][0]["content"])
            raise RuntimeError("stop")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create

        with patch("anthropic.Anthropic", return_value=mock_client):
            build_schema(zpath, "fake_key")

        assert len(captured_prompt) == 1
        prompt = captured_prompt[0]
        assert "FOLDER STRUCTURE" in prompt
        assert "profile/" in prompt
        assert "history/" in prompt
        assert "structure_type" in prompt
        assert "provenance" in prompt
        assert "sensitive" in prompt


# ---------------------------------------------------------------------------
# End-to-end enrichment integration test
# ---------------------------------------------------------------------------


class TestEndToEndEnrichment:
    def test_realistic_zip_produces_enriched_schema(self, tmp_path):
        """Full pipeline: ZIP with mixed formats → preprocessor → enriched LLM → schema."""
        zdata = _make_zip(
            {
                "profile/account.json": json.dumps(
                    {
                        "name": "Alice Smith",
                        "email": "alice@example.com",
                        "created_at": "2020-03-15T10:30:00Z",
                        "plan": "premium",
                    }
                ).encode(),
                "activity/streaming_history.json": json.dumps(
                    [
                        {
                            "trackName": "Song A",
                            "artistName": "Band X",
                            "endTime": "2024-01-15T23:45:00",
                            "msPlayed": 251000,
                        },
                        {
                            "trackName": "Song B",
                            "artistName": "Band Y",
                            "endTime": "2024-01-16T01:20:00",
                            "msPlayed": 180000,
                        },
                    ]
                ).encode(),
                "activity/search_queries.csv": b"query,timestamp,platform\nfoo,2024-01-01,web\nbar,2024-01-02,mobile",
            }
        )
        zpath = tmp_path / "export.zip"
        zpath.write_bytes(zdata)

        enriched_response = json.dumps(
            {
                "categories": [
                    {
                        "name": "Account Profile",
                        "description": "Core account and identity information",
                        "structure_type": "object",
                        "record_count": 1,
                        "provenance": "provided",
                        "fields": [
                            {
                                "name": "name",
                                "type": "string",
                                "example": "Alice Smith",
                                "description": "Full name",
                                "sensitive": False,
                                "provenance": "provided",
                            },
                            {
                                "name": "email",
                                "type": "string/email",
                                "example": "alice@example.com",
                                "description": "Primary email",
                                "sensitive": False,
                                "provenance": "provided",
                            },
                            {
                                "name": "created_at",
                                "type": "string/date-time",
                                "example": "2020-03-15T10:30:00Z",
                                "description": "Account creation date",
                                "sensitive": False,
                                "provenance": "observed",
                            },
                        ],
                    },
                    {
                        "name": "Streaming History",
                        "description": "Music listening activity",
                        "structure_type": "array",
                        "record_count": 2,
                        "provenance": "observed",
                        "fields": [
                            {
                                "name": "trackName",
                                "type": "string",
                                "example": "Song A",
                                "description": "Track title",
                                "sensitive": False,
                                "provenance": "observed",
                            },
                            {
                                "name": "msPlayed",
                                "type": "integer",
                                "example": 251000,
                                "description": "Milliseconds played",
                                "sensitive": False,
                                "provenance": "observed",
                            },
                        ],
                    },
                ],
                "services": [
                    {
                        "name": "Music Streaming",
                        "description": "Stream and discover music",
                    }
                ],
                "export_meta": {
                    "format": "ZIP",
                    "formats_found": ["json", "csv"],
                    "delivery": "Download link",
                    "timeline": "30 days",
                    "structure": "Organized by type: profile/ and activity/ folders",
                    "total_files": 3,
                    "total_records_estimate": 4,
                },
            }
        )

        mock_client = MagicMock()
        mock_resp = _mock_llm_response(enriched_response)
        mock_client.messages.create.return_value = mock_resp

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = build_schema(zpath, "fake_key", company_name="testmusic")

        # Verify enriched fields
        assert len(result["categories"]) == 2
        profile_cat = result["categories"][0]
        assert profile_cat["structure_type"] == "object"
        assert profile_cat["provenance"] == "provided"

        history_cat = result["categories"][1]
        assert history_cat["structure_type"] == "array"
        assert history_cat["record_count"] == 2
        assert history_cat["provenance"] == "observed"

        # Check field-level enrichment
        email_field = profile_cat["fields"][1]
        assert email_field["type"] == "string/email"
        assert email_field["sensitive"] is False

        # Check export_meta
        assert result["export_meta"]["formats_found"] == ["json", "csv"]
        assert result["export_meta"]["total_files"] == 3

        # Verify the prompt included structural context
        call_args = mock_client.messages.create.call_args
        prompt_text = call_args.kwargs["messages"][0]["content"]
        assert "FOLDER STRUCTURE" in prompt_text
        assert "STRUCTURAL CONTEXT" in prompt_text
