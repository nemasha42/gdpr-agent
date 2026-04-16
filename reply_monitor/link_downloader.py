"""Download personal data packages from time-limited links in SAR replies.

Strategy:
  1. Playwright (headless Chromium) — handles Cloudflare JS challenges and
     authenticated sessions that block plain HTTP clients.
  2. requests fallback — used only if Playwright is not installed.

After download the file is cataloged and LLM schema analysis is run
if ANTHROPIC_API_KEY is available.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from reply_monitor.attachment_handler import _catalog_csv, _catalog_json, _catalog_zip
from reply_monitor.models import AttachmentCatalog, FileEntry

_RECEIVED_DIR = Path(__file__).parent.parent / "user_data" / "received"
_MAX_AUTO_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB hard cap


class DownloadResult:
    """Result of a download_data_link() call."""

    def __init__(
        self,
        catalog: AttachmentCatalog | None = None,
        *,
        too_large: bool = False,
        expired: bool = False,
        error: str = "",
    ):
        self.catalog = catalog
        self.too_large = too_large
        self.expired = expired
        self.error = error

    @property
    def ok(self) -> bool:
        return self.catalog is not None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "too_large": self.too_large,
            "expired": self.expired,
            "error": self.error,
            "catalog": self.catalog.to_dict() if self.catalog else None,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def download_data_link(url: str, domain: str, api_key: str = "") -> DownloadResult:
    """Download a data export link, catalog the result, and run LLM schema analysis.

    Tries Playwright first (bypasses Cloudflare), falls back to requests.
    """
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    save_dir = _RECEIVED_DIR / domain
    save_dir.mkdir(parents=True, exist_ok=True)

    # Try Playwright first
    result = _download_playwright(url, save_dir)
    if result is None:
        # Playwright not installed — fall back
        result = _download_requests(url, save_dir)

    if not result.ok:
        return result

    # Run LLM schema analysis on the downloaded file
    if api_key and result.catalog:
        _enrich_schema(result.catalog, api_key, domain=domain)

    return result


# ---------------------------------------------------------------------------
# Playwright downloader
# ---------------------------------------------------------------------------


def _download_playwright(url: str, save_dir: Path) -> DownloadResult | None:
    """Download via headless Chromium. Returns None if Playwright not installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    _STEALTH_SCRIPT = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
    """

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            context.add_init_script(_STEALTH_SCRIPT)
            page = context.new_page()

            download_info: dict = {}

            def _on_download(dl):
                download_info["dl"] = dl

            page.on("download", _on_download)

            # Navigate — Cloudflare challenge runs here automatically
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass  # timeout ok if download already triggered

            # Wait up to 60s for download to start
            deadline = 60_000
            page.wait_for_timeout(2000)

            if "dl" not in download_info:
                # Some sites redirect directly to a file download without a DOM event
                # Try waiting a bit more
                for _ in range(28):
                    page.wait_for_timeout(1000)
                    if "dl" in download_info:
                        break

            if "dl" not in download_info:
                browser.close()
                return DownloadResult(error="playwright_no_download")

            dl = download_info["dl"]

            # Save to a temp file first so we know the filename
            with tempfile.NamedTemporaryFile(delete=False, suffix=".tmp") as tmp:
                tmp_path = Path(tmp.name)

            dl.save_as(str(tmp_path))
            suggested = dl.suggested_filename or "data.bin"
            dest = save_dir / _safe_filename(suggested)
            tmp_path.rename(dest)
            browser.close()

        return _catalog_file(dest)

    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            hint = (
                "Playwright browser binaries not found. "
                "Run: python -m playwright install chromium"
            )
            print(f"[link_downloader] {hint}", flush=True)
            return DownloadResult(error=hint)
        return DownloadResult(error=f"playwright: {exc}")


# ---------------------------------------------------------------------------
# requests fallback
# ---------------------------------------------------------------------------


def _download_requests(url: str, save_dir: Path) -> DownloadResult:
    try:
        import requests
    except ImportError:
        return DownloadResult(error="neither playwright nor requests is installed")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/zip,application/octet-stream,*/*",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=120, stream=True)
    except Exception as exc:
        return DownloadResult(error=f"requests: {exc}")

    if resp.status_code >= 400:
        if resp.status_code == 403 and "text/html" in resp.headers.get(
            "Content-Type", ""
        ):
            return DownloadResult(error="browser_required")
        return DownloadResult(
            expired=True if resp.status_code in (401, 403, 404, 410) else False,
            error=""
            if resp.status_code in (401, 403, 404, 410)
            else f"HTTP {resp.status_code}",
        )

    chunks, total = [], 0
    for chunk in resp.iter_content(chunk_size=256 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total > _MAX_AUTO_DOWNLOAD_BYTES:
            resp.close()
            return DownloadResult(too_large=True)
    data = b"".join(chunks)

    filename = _filename_from_response(resp, url)
    dest = save_dir / filename
    dest.write_bytes(data)

    return _catalog_file(dest)


# ---------------------------------------------------------------------------
# Cataloging + schema
# ---------------------------------------------------------------------------


def _catalog_file(file_path: Path) -> DownloadResult:
    """Read a saved file, build AttachmentCatalog (no LLM yet)."""
    try:
        data = file_path.read_bytes()
    except Exception as exc:
        return DownloadResult(error=f"read failed: {exc}")

    if len(data) > _MAX_AUTO_DOWNLOAD_BYTES:
        return DownloadResult(too_large=True)

    ext = file_path.suffix.lstrip(".").lower()

    if ext == "zip":
        files, categories = _catalog_zip(data, file_path.name)
    elif ext == "json":
        files, categories = _catalog_json(data, file_path.name, len(data))
    elif ext == "csv":
        files, categories = _catalog_csv(data, file_path.name, len(data))
    else:
        files = [
            FileEntry(
                filename=file_path.name, size_bytes=len(data), file_type=ext or "bin"
            )
        ]
        categories = []

    catalog = AttachmentCatalog(
        path=str(file_path),
        size_bytes=len(data),
        file_type=ext or "bin",
        files=files,
        categories=sorted(set(categories)),
    )
    return DownloadResult(catalog=catalog)


def _enrich_schema(catalog: AttachmentCatalog, api_key: str, domain: str = "") -> None:
    """Run LLM schema analysis on catalog.path and store result in catalog.schema."""
    from reply_monitor.schema_builder import build_schema

    try:
        result = build_schema(Path(catalog.path), api_key, company_name=domain)
        if result:
            catalog.schema = result.get("categories", [])
            catalog.categories = [c["name"] for c in catalog.schema]
            catalog.services = result.get("services", [])
            catalog.export_meta = result.get("export_meta", {})
    except Exception as exc:
        print(f"[link_downloader] Schema analysis failed: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filename_from_response(resp: object, url: str) -> str:
    cd = getattr(resp, "headers", {}).get("Content-Disposition", "")
    m = re.search(r'filename[^;=\n]*=(["\']?)([^;\n"\']+)\1', cd)
    if m:
        return _safe_filename(m.group(2).strip())
    segment = url.split("?")[0].split("/")[-1]
    if segment and "." in segment:
        return _safe_filename(segment)
    ct = getattr(resp, "headers", {}).get("Content-Type", "")
    ext = (
        "zip"
        if "zip" in ct
        else "json"
        if "json" in ct
        else "csv"
        if "csv" in ct
        else "bin"
    )
    return f"data.{ext}"


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name)[:100]
