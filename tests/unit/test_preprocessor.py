"""Unit tests for reply_monitor/preprocessor.py."""

import io
import json
import zipfile


from reply_monitor.preprocessor import (
    PreprocessResult,
    build_context_summary,
    preprocess,
)


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestPreprocessZipFolderTree:
    def test_extracts_folder_structure(self, tmp_path):
        zdata = _make_zip(
            {
                "profile/account.json": b'{"name": "Alice"}',
                "profile/settings.json": b'{"theme": "dark"}',
                "activity/search_history.csv": b"query,date\nfoo,2024-01-01",
                "activity/watch_history.json": b'[{"title": "x"}]',
            }
        )
        zpath = tmp_path / "data.zip"
        zpath.write_bytes(zdata)

        result = preprocess(zpath)

        assert isinstance(result, PreprocessResult)
        assert result.total_files == 4
        assert set(result.formats_found) == {"json", "csv"}
        assert "profile" in result.folder_tree
        assert "activity" in result.folder_tree
        assert len(result.folder_tree["profile"]) == 2
        assert len(result.folder_tree["activity"]) == 2

    def test_flat_zip_uses_root_folder(self, tmp_path):
        zdata = _make_zip(
            {
                "data.json": b'{"x": 1}',
                "readme.txt": b"hello",
            }
        )
        zpath = tmp_path / "flat.zip"
        zpath.write_bytes(zdata)

        result = preprocess(zpath)
        assert "." in result.folder_tree
        assert result.total_files == 2

    def test_corrupt_zip_returns_empty_result(self, tmp_path):
        zpath = tmp_path / "bad.zip"
        zpath.write_bytes(b"not a zip")

        result = preprocess(zpath)
        assert result.total_files == 0
        assert result.file_samples == []


class TestJsonAnalysis:
    def test_json_object_extracts_keys(self, tmp_path):
        data = json.dumps(
            {
                "name": "Alice",
                "email": "alice@example.com",
                "addresses": [{"city": "London"}, {"city": "Paris"}],
            }
        )
        fpath = tmp_path / "profile.json"
        fpath.write_text(data)
        result = preprocess(fpath)
        assert result.file_metas[0].json_structure == "object"
        assert "name" in result.file_metas[0].json_keys
        assert "email" in result.file_metas[0].json_keys
        assert "addresses" in result.file_metas[0].json_keys

    def test_json_array_counts_records(self, tmp_path):
        data = json.dumps(
            [
                {"track": "Song A", "artist": "Band", "msPlayed": 12000},
                {"track": "Song B", "artist": "Other", "msPlayed": 45000},
                {"track": "Song C", "artist": "Band", "msPlayed": 8000},
            ]
        )
        fpath = tmp_path / "streaming_history.json"
        fpath.write_text(data)
        result = preprocess(fpath)
        meta = result.file_metas[0]
        assert meta.json_structure == "array"
        assert meta.record_count == 3
        assert "track" in meta.json_keys
        assert result.total_records_estimate == 3

    def test_twitter_js_wrapper_unwrapped(self, tmp_path):
        inner = json.dumps([{"tweet": {"full_text": "Hello world"}}])
        js_content = f"window.YTD.tweets.part0 = {inner}"
        fpath = tmp_path / "tweets.js"
        fpath.write_text(js_content)
        result = preprocess(fpath)
        assert result.total_files == 1
        assert result.file_metas[0].json_structure == "array"
        assert result.file_metas[0].record_count == 1
        assert result.file_samples[0]["content"].startswith("[")

    def test_zip_with_json_extracts_structure(self, tmp_path):
        zdata = _make_zip(
            {
                "profile.json": json.dumps({"name": "Alice", "age": 30}).encode(),
                "history.json": json.dumps([{"q": "foo"}, {"q": "bar"}]).encode(),
            }
        )
        zpath = tmp_path / "export.zip"
        zpath.write_bytes(zdata)
        result = preprocess(zpath)
        metas_by_name = {m.filename: m for m in result.file_metas}
        assert metas_by_name["profile.json"].json_structure == "object"
        assert metas_by_name["history.json"].json_structure == "array"
        assert metas_by_name["history.json"].record_count == 2
        assert result.total_records_estimate == 2


class TestCsvAnalysis:
    def test_csv_extracts_headers(self, tmp_path):
        csv_content = "name,email,created_at,plan_type\nAlice,a@b.com,2024-01-01,pro\nBob,b@c.com,2024-02-01,free"
        fpath = tmp_path / "users.csv"
        fpath.write_text(csv_content)
        result = preprocess(fpath)
        meta = result.file_metas[0]
        assert meta.headers == ["name", "email", "created_at", "plan_type"]
        assert meta.record_count == 2

    def test_csv_in_zip(self, tmp_path):
        csv_data = b"query,timestamp\nfoo,2024-01-01\nbar,2024-01-02\nbaz,2024-01-03"
        zdata = _make_zip({"search/queries.csv": csv_data})
        zpath = tmp_path / "export.zip"
        zpath.write_bytes(zdata)
        result = preprocess(zpath)
        meta = result.file_metas[0]
        assert meta.headers == ["query", "timestamp"]
        assert meta.record_count == 3

    def test_tsv_handled_like_csv(self, tmp_path):
        tsv_content = "name\temail\nAlice\ta@b.com"
        fpath = tmp_path / "data.tsv"
        fpath.write_text(tsv_content)
        result = preprocess(fpath)
        assert result.total_files == 1
        assert len(result.file_metas) == 1


class TestBuildContextSummary:
    def test_includes_folder_tree(self, tmp_path):
        zdata = _make_zip(
            {
                "profile/account.json": json.dumps({"name": "Alice"}).encode(),
                "history/searches.csv": b"query,date\nfoo,2024-01-01",
            }
        )
        zpath = tmp_path / "export.zip"
        zpath.write_bytes(zdata)
        result = preprocess(zpath)
        summary = build_context_summary(result)
        assert "FOLDER STRUCTURE" in summary
        assert "profile/" in summary
        assert "history/" in summary

    def test_includes_json_structure_info(self, tmp_path):
        data = json.dumps([{"track": "A"}, {"track": "B"}, {"track": "C"}])
        fpath = tmp_path / "history.json"
        fpath.write_text(data)
        result = preprocess(fpath)
        summary = build_context_summary(result)
        assert "array" in summary.lower()
        assert "3 records" in summary or "3 record" in summary

    def test_includes_csv_headers(self, tmp_path):
        fpath = tmp_path / "data.csv"
        fpath.write_text("name,email,phone\nAlice,a@b,123")
        result = preprocess(fpath)
        summary = build_context_summary(result)
        assert "name" in summary
        assert "email" in summary

    def test_includes_stats(self, tmp_path):
        zdata = _make_zip(
            {
                "a.json": b'{"x": 1}',
                "b.csv": b"col1\nval1",
                "c.txt": b"hello",
            }
        )
        zpath = tmp_path / "data.zip"
        zpath.write_bytes(zdata)
        result = preprocess(zpath)
        summary = build_context_summary(result)
        assert "3 files" in summary or "3 total" in summary
