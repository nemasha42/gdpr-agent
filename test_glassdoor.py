#!/usr/bin/env python3
"""
Glassdoor end-to-end diagnostic script.

Shows every step of processing Glassdoor's replies:
  1. Stored state — what's in reply_state.json right now
  2. Re-classification — run new classifier on each stored reply, show NON_GDPR vs GDPR
  3. Body extraction — re-fetch the DATA_PROVIDED_LINK email from Gmail, show raw body + URL extraction
  4. Download — attempt to download Glassdoor's data ZIP, show size check and progress
  5. Catalog analysis — what files, categories, and schema issues were found

Usage:
    python test_glassdoor.py [--account EMAIL] [--skip-gmail] [--skip-download]
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

# ── project root on path ──────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

ACCOUNT = "traderm1620@gmail.com"
DOMAIN = "glassdoor.com"
STATE_PATH = ROOT / "user_data" / "reply_state.json"

# ─── ANSI colours ─────────────────────────────────────────────────────────────
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
RESET = "\033[0m"


def hdr(title: str) -> None:
    width = 72
    print(f"\n{BOLD}{CYAN}{'━' * width}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'━' * width}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET}  {msg}")


def err(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")


def info(msg: str, indent: int = 2) -> None:
    prefix = " " * (indent + 4)
    for line in textwrap.wrap(
        msg, width=68, initial_indent="  · ", subsequent_indent=prefix
    ):
        print(line)


def field(label: str, value: str) -> None:
    print(f"    {DIM}{label:<22}{RESET}{value}")


# ─── Step 1: Stored state ─────────────────────────────────────────────────────


def step1_stored_state(account: str) -> dict | None:
    hdr("STEP 1 — Stored state in reply_state.json")

    if not STATE_PATH.exists():
        err(f"reply_state.json not found at {STATE_PATH}")
        return None

    raw = json.loads(STATE_PATH.read_text())
    safe_key = account.replace("@", "_at_").replace(".", "_")
    account_data = raw.get(safe_key, {})
    glassdoor = account_data.get(DOMAIN)

    if not glassdoor:
        err(f"No state found for {DOMAIN} under account {account}")
        return None

    ok(f"Found Glassdoor state — {len(glassdoor.get('replies', []))} stored replies")
    field("SAR sent at", glassdoor.get("sar_sent_at", ""))
    field("Deadline", glassdoor.get("deadline", ""))
    field("Sent to", glassdoor.get("to_email", ""))
    field("Thread ID", glassdoor.get("gmail_thread_id") or "(none)")

    print()
    for i, r in enumerate(glassdoor.get("replies", []), 1):
        print(f"  {BOLD}Reply {i}{RESET}")
        field("Message ID", r.get("gmail_message_id", ""))
        field("From", r.get("from", ""))
        field("Subject", r.get("subject", ""))
        field("Received", r.get("received_at", "")[:19])
        field("Stored tags", str(r.get("tags", [])))
        field("data_link", r.get("extracted", {}).get("data_link") or "(empty)")
        field("LLM used", str(r.get("llm_used", False)))
        print()

    return glassdoor


# ─── Step 2: Re-classify stored replies ──────────────────────────────────────


def step2_reclassify(glassdoor: dict) -> None:
    hdr("STEP 2 — Re-classify each reply with current classifier code")

    from reply_monitor.classifier import classify, _is_non_gdpr

    for i, r in enumerate(glassdoor.get("replies", []), 1):
        from_addr = r.get("from", "")
        subject = r.get("subject", "")
        snippet = r.get("snippet", "")

        print(f"  {BOLD}Reply {i}  ·  {subject[:60]}{RESET}")
        field("From", from_addr)
        field("Subject", subject)
        field("Snippet", snippet[:120])

        # Show NON_GDPR pre-pass decision
        is_noise = _is_non_gdpr(from_addr, subject, snippet)
        if is_noise:
            warn(
                "NON_GDPR pre-pass FIRES → short-circuits classifier (newsletter/marketing)"
            )
        else:
            ok("NON_GDPR pre-pass: passes through to GDPR classifier")

        # Full classify (snippet only — as stored, no body)
        msg_dict = {
            "from": from_addr,
            "subject": subject,
            "snippet": snippet,
            "has_attachment": r.get("has_attachment", False),
        }
        result = classify(msg_dict)  # no api_key → no LLM

        field("New tags", str(result.tags))
        field("LLM used", str(result.llm_used))
        field(
            "data_link", result.extracted.get("data_link") or "(not found in snippet)"
        )
        if result.extracted.get("data_link"):
            ok("URL found in snippet!")
        elif "DATA_PROVIDED_LINK" in result.tags:
            warn(
                "DATA_PROVIDED_LINK tag correct — but URL not in snippet (likely in body)"
            )
        print()


# ─── Step 3: Re-fetch email body from Gmail ───────────────────────────────────


def step3_body_extraction(
    glassdoor: dict, skip_gmail: bool, account: str = ACCOUNT
) -> str | None:
    hdr("STEP 3 — Re-fetch email body from Gmail & extract download URL")

    # Find the DATA_PROVIDED_LINK reply
    target = None
    for r in glassdoor.get("replies", []):
        if "DATA_PROVIDED_LINK" in r.get("tags", []):
            target = r
            break

    if not target:
        err("No DATA_PROVIDED_LINK reply found in stored state")
        return None

    ok(f"Target message: {target['gmail_message_id']}")
    field("Subject", target.get("subject", ""))
    field("From", target.get("from", ""))

    if skip_gmail:
        warn("--skip-gmail: skipping live Gmail fetch")
        # Try to re-extract from stored snippet anyway
        from reply_monitor.classifier import _extract

        result = _extract(
            target.get("from", ""), target.get("subject", ""), target.get("snippet", "")
        )
        field("URL from snippet alone", result.get("data_link") or "(none)")
        return None

    print()
    print(f"  {DIM}Connecting to Gmail (readonly token)…{RESET}")

    try:
        from auth.gmail_oauth import get_gmail_service

        service, email = get_gmail_service(email_hint=account)
        ok(f"Authenticated as {email}")
    except Exception as exc:
        err(f"Gmail auth failed: {exc}")
        return None

    # Fetch the full message
    print(f"  {DIM}Fetching message {target['gmail_message_id']} (format=full)…{RESET}")
    try:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=target["gmail_message_id"],
                format="full",
            )
            .execute()
        )
        ok("Message fetched successfully")
    except Exception as exc:
        err(f"Gmail messages.get() failed: {exc}")
        return None

    # Extract body
    from reply_monitor.fetcher import _extract_body

    payload = msg.get("payload", {})
    body = _extract_body(payload)

    print()
    ok(f"Body extracted — {len(body)} characters")
    print(f"\n  {BOLD}Body preview (first 800 chars):{RESET}")
    preview = body[:800].replace("\n", "\n    ")
    print(f"    {DIM}{preview}{RESET}")

    if len(body) > 800:
        print(f"    {DIM}… [{len(body) - 800} more chars]{RESET}")

    # Run URL extraction on body
    print()
    from reply_monitor.classifier import _extract, classify

    # Show what snippet-only extraction misses
    snippet_result = _extract(
        target.get("from", ""), target.get("subject", ""), target.get("snippet", ""), ""
    )
    body_result = _extract(
        target.get("from", ""),
        target.get("subject", ""),
        target.get("snippet", ""),
        body,
    )

    print(f"  {BOLD}URL extraction comparison:{RESET}")
    field(
        "Snippet-only data_link",
        snippet_result.get("data_link") or "(empty — this was the bug)",
    )
    field("With body data_link", body_result.get("data_link") or "(still empty)")

    if body_result.get("data_link"):
        ok(f"URL found: {body_result['data_link'][:80]}")
    else:
        warn(
            "URL still not found in body text — checking raw body for glassdoor.com URLs…"
        )
        import re

        urls = re.findall(r"https?://\S+glassdoor\S+", body)
        if urls:
            for u in urls[:5]:
                print(f"    {YELLOW}candidate: {u}{RESET}")
        else:
            err("No glassdoor URLs found in body text at all")
            print()
            print(f"  {BOLD}Full body (for manual inspection):{RESET}")
            print(f"    {DIM}{body}{RESET}")

    # Also run full classify with body
    print()
    full_result = classify(
        {
            "from": target.get("from", ""),
            "subject": target.get("subject", ""),
            "snippet": target.get("snippet", ""),
            "body": body,
            "has_attachment": target.get("has_attachment", False),
        }
    )
    field("Full classify tags", str(full_result.tags))
    field(
        "Full classify data_link", full_result.extracted.get("data_link") or "(empty)"
    )
    field("LLM used", str(full_result.llm_used))

    return body_result.get("data_link") or full_result.extracted.get("data_link") or ""


# ─── Step 4: Download ─────────────────────────────────────────────────────────


def step4_download(data_link: str | None, skip_download: bool) -> dict | None:
    hdr("STEP 4 — Download Glassdoor data package")

    if not data_link:
        err("No data_link available — cannot attempt download")
        info(
            "To get the link: run 'python monitor.py --account traderm1620@gmail.com' first, "
            "or hit /reextract on the dashboard"
        )
        return None

    ok(f"data_link: {data_link[:80]}")

    if skip_download:
        warn("--skip-download: skipping HTTP download")
        return None

    print()
    print(f"  {DIM}Step 4a — HEAD request (check size before downloading)…{RESET}")

    try:
        import requests
    except ImportError:
        err("requests library not installed")
        return None

    try:
        head = requests.head(data_link, allow_redirects=True, timeout=15)
        ok(f"HEAD response: HTTP {head.status_code}")
        content_length = int(head.headers.get("Content-Length", 0))
        content_type = head.headers.get("Content-Type", "unknown")
        field("Content-Type", content_type)
        field(
            "Content-Length",
            f"{content_length:,} bytes ({content_length/1024:.1f} KB)"
            if content_length
            else "(not provided)",
        )
    except Exception as exc:
        err(f"HEAD request failed: {exc}")
        return None

    if head.status_code in (401, 403, 404, 410):
        warn(f"HTTP {head.status_code} — download link has likely EXPIRED")
        info(
            "Glassdoor's download links are time-limited (typically 7 days). "
            "You may need to re-request your data from Glassdoor's privacy portal."
        )
        return None

    if head.status_code >= 400:
        err(f"HTTP {head.status_code} — cannot download")
        return None

    MAX = 100 * 1024 * 1024
    if content_length > MAX:
        warn(
            f"File is {content_length / 1048576:.1f} MB — exceeds 100 MB auto-download limit"
        )
        info(f"Download manually and place in user_data/received/{DOMAIN}/")
        return None

    print()
    print(f"  {DIM}Step 4b — GET download…{RESET}")

    from reply_monitor.link_downloader import download_data_link

    result = download_data_link(data_link, DOMAIN)

    if result.expired:
        warn("Link expired (4xx response on GET)")
        return None
    if result.too_large:
        warn("File exceeded size limit during download")
        return None
    if result.error:
        err(f"Download error: {result.error}")
        return None
    if not result.ok:
        err("Download failed (unknown reason)")
        return None

    ok("Downloaded successfully!")
    field("Saved to", result.catalog.path)
    field(
        "Size",
        f"{result.catalog.size_bytes:,} bytes ({result.catalog.size_bytes/1024:.1f} KB)",
    )
    field("File type", result.catalog.file_type.upper())
    field("Files found", str(len(result.catalog.files)))
    field("Categories", str(result.catalog.categories))

    return result.catalog.to_dict()


# ─── Step 5: Catalog analysis ─────────────────────────────────────────────────


def step5_catalog_analysis(catalog: dict | None, glassdoor: dict) -> None:
    hdr("STEP 5 — Data catalog analysis (what worked, what needs improvement)")

    # Try to load catalog from stored state if not passed in
    if catalog is None:
        for r in glassdoor.get("replies", []):
            if r.get("attachment_catalog"):
                catalog = r["attachment_catalog"]
                ok("Loaded catalog from stored reply_state.json")
                break

    if catalog is None:
        warn("No catalog available yet (data not downloaded)")
        print()
        print(f"  {BOLD}What the data card would show once downloaded:{RESET}")
        info("After download, the ZIP will be cataloged into:")
        info(
            "  • Data categories inferred from filenames (Search History, Profile Data, etc.)"
        )
        info("  • File tree with sizes")
        info("  • Export Info (format, total size, date received)")
        info("  • Collapsible <details>/<summary> tree in dataowners.org style")
        print()
        print(f"  {BOLD}Known limitations of current catalog logic:{RESET}")
        warn(
            "Category matching is filename-based regex — may miss Glassdoor-specific names"
        )
        warn(
            "No field-level schema (type/example/description) — dataowners.org shows this level of detail"
        )
        warn(
            "Category→file association uses substring matching, may have false positives"
        )
        return

    ok(
        f"Catalog loaded — {len(catalog.get('files', []))} files, "
        f"{len(catalog.get('categories', []))} categories"
    )
    field("Format", catalog.get("file_type", "").upper())
    field("Total size", f"{catalog.get('size_bytes', 0):,} bytes")
    field("Path", catalog.get("path", ""))

    print()
    print(f"  {BOLD}Files:{RESET}")
    for f in catalog.get("files", []):
        icon = {"json": "📄", "csv": "📊", "zip": "📦"}.get(f.get("file_type"), "📁")
        print(f"    {icon}  {f['filename']:<50}  {f.get('size_bytes',0)/1024:.1f} KB")

    print()
    print(f"  {BOLD}Detected categories:{RESET}")
    cats = catalog.get("categories", [])
    if cats:
        for c in cats:
            print(f"    · {c}")
    else:
        warn("No categories detected — filenames didn't match any known patterns")

    print()
    print(f"  {BOLD}What works well:{RESET}")
    ok("ZIP extraction and file enumeration")
    ok("Size and format stats shown in Export Info section")
    ok("File tree with type icons in Data Description")
    ok("Collapsible category tree (Expand All / Collapse All)")

    print()
    print(f"  {BOLD}Known gaps vs dataowners.org format:{RESET}")

    # Check if categories cover the files
    all_files = [f["filename"] for f in catalog.get("files", [])]
    uncategorised = []
    for fname in all_files:
        matched = False
        for cat in cats:
            cat_lower = cat.lower().replace(" ", "")
            if cat_lower in fname.lower() or cat.lower() in fname.lower():
                matched = True
                break
        if not matched:
            uncategorised.append(fname)

    if uncategorised:
        warn(f"{len(uncategorised)} file(s) not matched to any category:")
        for fname in uncategorised:
            print(f"      · {fname}")
        info(
            "Fix: add Glassdoor-specific patterns to _CATEGORY_HINTS in attachment_handler.py"
        )
    else:
        ok("All files matched to at least one category")

    warn("No field-level schema (column names, types, example values)")
    info(
        "dataowners.org shows type + example + description per field. "
        "To reach that level: parse JSON keys / CSV column headers and display them "
        "in the category tree as sub-rows."
    )

    warn("No data richness score or record count shown")
    info(
        "Could add: row count for CSVs, key count for JSONs, to give a sense of data volume."
    )

    if any(f.get("file_type") == "json" for f in catalog.get("files", [])):
        ok("JSON files detected — can be introspected for schema keys")
        info(
            "Next step: open each JSON in the received/ folder, print top-level keys, "
            "add them as field entries under the matching category."
        )

    if any(f.get("file_type") == "csv" for f in catalog.get("files", [])):
        ok("CSV files detected — column headers available for schema display")
        info(
            "Next step: read first row of each CSV, display column names "
            "under matching category with sample values from row 2."
        )


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Glassdoor end-to-end diagnostic")
    parser.add_argument("--account", default=ACCOUNT, help="Gmail account email")
    parser.add_argument(
        "--skip-gmail", action="store_true", help="Skip live Gmail fetch"
    )
    parser.add_argument(
        "--skip-download", action="store_true", help="Skip HTTP download"
    )
    args = parser.parse_args()

    account = args.account
    print(f"\n{BOLD}Glassdoor GDPR SAR — end-to-end diagnostic{RESET}")
    print(f"{DIM}Account: {account}  |  Domain: {DOMAIN}{RESET}")

    # Step 1
    glassdoor = step1_stored_state(account)
    if glassdoor is None:
        sys.exit(1)

    # Step 2
    step2_reclassify(glassdoor)

    # Step 3
    data_link = step3_body_extraction(glassdoor, args.skip_gmail, account)

    # Step 4
    catalog = step4_download(data_link, args.skip_download)

    # Step 5
    step5_catalog_analysis(catalog, glassdoor)

    print(f"\n{BOLD}{GREEN}Diagnostic complete.{RESET}\n")


if __name__ == "__main__":
    main()
