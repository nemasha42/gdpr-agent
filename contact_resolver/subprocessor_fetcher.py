"""Subprocessor discovery via web scraping + Haiku LLM."""

import json
import re
from datetime import date, datetime, timezone

import requests

import anthropic

from config.settings import settings
from contact_resolver import cost_tracker
from contact_resolver.models import Subprocessor, SubprocessorRecord
from contact_resolver.privacy_page_scraper import _strip_html

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 8192

# Minimum plain-text length (post HTML-strip) to consider a scraped page useful.
# JS-rendered shells have < 200 chars of real text after stripping; real pages have thousands.
_MIN_PLAIN_TEXT = 500

_SUBPROCESSOR_PATHS = [
    "/sub-processors",
    "/subprocessors",
    "/vendors",
    "/third-parties",
    "/data-processors",
    "/legal/sub-processors",
    "/privacy/sub-processors",
]

_SYSTEM_PROMPT = """\
You are a GDPR subprocessor analyst. Find all third-party data processors (subprocessors) \
used by the given company and return ONLY a valid JSON object — no prose, no markdown fences.

Return compact JSON — omit source_url per entry and source (they are computed/known separately):
{"subprocessors": [
  {
    "domain": "stripe.com",
    "company_name": "Stripe",
    "hq_country": "United States",
    "hq_country_code": "US",
    "purposes": ["payment processing"],
    "data_categories": ["payment data", "billing address"],
    "transfer_basis": "SCCs"
  }
],
"source_url": "https://example.com/sub-processors"}

Rules:
- Only include genuine third-party data processors, not group companies or subsidiaries
- Do not include the company itself as a subprocessor
- Cap at 50 subprocessors
- If no subprocessors found, return {"subprocessors": [], "source_url": ""}
"""


def fetch_subprocessors(
    company_name: str,
    domain: str,
    *,
    api_key: str | None = None,
) -> SubprocessorRecord:
    """Fetch subprocessors for a company. ~$0.025-0.05/call.

    Returns a SubprocessorRecord with fetch_status="pending" if LLM limit reached.
    """
    if cost_tracker.is_llm_limit_reached():
        return SubprocessorRecord(
            fetched_at=datetime.now(timezone.utc).isoformat(),
            fetch_status="pending",
            error_message="LLM call limit reached",
        )

    anthropic_key = api_key or settings.anthropic_api_key
    if not anthropic_key:
        return SubprocessorRecord(
            fetched_at=datetime.now(timezone.utc).isoformat(),
            fetch_status="error",
            error_message="No API key",
        )

    # Step 1: Try to scrape subprocessor page for context.
    # Try both bare domain and www-prefixed domain.
    # Use _extract_page_content() which targets <table> elements first so we get
    # the actual subprocessor rows and not the surrounding JS/CSS noise.
    page_text = ""
    source_url = ""
    for path in _SUBPROCESSOR_PATHS:
        for prefix in (f"https://{domain}", f"https://www.{domain}"):
            url = f"{prefix}{path}"
            try:
                resp = requests.get(
                    url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}
                )
                if resp.status_code == 200:
                    content = _extract_page_content(resp.text)
                    if len(content) >= _MIN_PLAIN_TEXT:
                        page_text = content[:50_000]
                        source_url = url
                        break
            except Exception as exc:
                print(f"[subprocessor_fetcher] scrape {url}: {exc}")
                continue
        if page_text:
            break

    # Step 1b: Playwright fallback — for JS-rendered SPAs where requests returns an empty shell.
    if not page_text:
        for path in _SUBPROCESSOR_PATHS:
            for prefix in (f"https://{domain}", f"https://www.{domain}"):
                url = f"{prefix}{path}"
                html = _fetch_page_playwright(url)
                if html:
                    content = _extract_page_content(html)
                    if len(content) >= _MIN_PLAIN_TEXT:
                        page_text = content[:50_000]
                        source_url = url
                        break
            if page_text:
                break

    # Step 2: Build user message with targeted search instructions.
    user_message = (
        f"Find all GDPR subprocessors (third-party data processors) for {company_name} ({domain}). "
        f"Search for: '{company_name} sub-processors list', "
        f"'site:{domain} sub-processors', "
        f"'{company_name} GDPR third-party data processors'. "
        f"The subprocessor list page is often at /sub-processors, /vendors, or /data-processors on their site. "
        f"Apply the trust scoring rules from your system prompt and return JSON."
    )
    if page_text:
        user_message = (
            f"Subprocessor page content from {source_url}:\n{page_text}\n\n"
            + user_message
        )

    # Step 3: LLM call.
    # Skip web_search when we already have scraped content — saves the entire output-token
    # budget for JSON generation instead of wasting it on a redundant search.
    client = anthropic.Anthropic(api_key=anthropic_key)
    create_kwargs: dict = dict(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    if not page_text:
        create_kwargs["tools"] = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
        ]
    try:
        response = client.messages.create(**create_kwargs)
    except anthropic.APIError as exc:
        return SubprocessorRecord(
            fetched_at=datetime.now(timezone.utc).isoformat(),
            fetch_status="error",
            error_message=str(exc)[:200],
        )

    text = _extract_text(response)
    data = _extract_json(text) if text else None

    record = _build_record(data, domain, source_url)

    cost_tracker.record_llm_call(
        company_name=company_name,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=_MODEL,
        found=record.fetch_status == "ok",
        source="subprocessor_fetcher",
        purpose="subprocessor_discovery",
    )
    return record


def is_stale(record: SubprocessorRecord, ttl_days: int = 30) -> bool:
    """Return True if the record is older than ttl_days."""
    if not record.fetched_at:
        return True
    try:
        fetched = datetime.fromisoformat(record.fetched_at)
        age = (
            datetime.now(timezone.utc)
            - fetched.replace(
                tzinfo=timezone.utc if fetched.tzinfo is None else fetched.tzinfo
            )
        ).days
        return age > ttl_days
    except Exception as exc:
        print(f"[subprocessor_fetcher] is_stale date parse failed: {exc}")
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_text(response) -> str:
    parts: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    idx = text.find("{")
    if idx == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, idx)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _extract_page_content(html: str) -> str:
    """Extract the most useful plain text from an HTML page for subprocessor discovery.

    Strategy (in priority order):
    1. Extract all <table> elements — subprocessor pages nearly always use tables;
       this skips the surrounding JS/CSS noise regardless of where the table sits.
    2. Find a keyword-anchored window around 'sub-processor'/'processor'/'vendor'
       in the full stripped text (for list-based pages).
    3. Fall back to the full stripped text (caller caps at 50 KB).

    Returns compact plain text with collapsed whitespace.
    """
    # Strategy 1: tables
    tables = re.findall(r"<table[^>]*>.*?</table>", html, re.DOTALL | re.IGNORECASE)
    if tables:
        parts: list[str] = []
        for t in tables:
            text = re.sub(r"\s+", " ", _strip_html(t)).strip()
            if len(text) > 100:
                parts.append(text)
        combined = "\n\n".join(parts)
        if len(combined) >= _MIN_PLAIN_TEXT:
            return combined

    # Strategy 2: keyword window
    plain = _strip_html(html)
    keywords = [
        "sub-processor",
        "subprocessor",
        "data processor",
        "third-party processor",
        "vendor",
    ]
    for kw in keywords:
        idx = plain.lower().find(kw)
        if idx != -1:
            start = max(0, idx - 200)
            return re.sub(r"\s+", " ", plain[start : start + 50_000]).strip()

    # Strategy 3: full text
    return re.sub(r"\s+", " ", plain).strip()


def _fetch_page_playwright(url: str) -> str:
    """Render *url* with headless Chromium and return raw HTML.

    Returns an empty string if Playwright is not installed or the page fails to load.
    Used as a fallback for JS-rendered SPA pages that return empty shells via requests.
    The caller is responsible for passing the HTML through _extract_page_content().
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=15_000, wait_until="domcontentloaded")
            page.wait_for_timeout(2_000)  # allow React/Vue to render
            html = page.content()
            browser.close()
        return html
    except Exception as exc:
        print(f"[subprocessor_fetcher] playwright fetch {url} failed: {exc}")
        return ""


def _build_record(
    data: dict | None, domain: str, source_url: str
) -> SubprocessorRecord:
    now = datetime.now(timezone.utc).isoformat()
    if not data:
        return SubprocessorRecord(
            fetched_at=now,
            fetch_status="not_found",
            error_message="No JSON in LLM response",
        )

    raw_list = data.get("subprocessors", [])
    if not isinstance(raw_list, list):
        raw_list = []

    detected_source_url = data.get("source_url", "") or source_url
    subprocessors: list[Subprocessor] = []
    seen_domains: set[str] = {domain}  # exclude self-referential

    for item in raw_list[:50]:
        if not isinstance(item, dict):
            continue
        sp_domain = str(item.get("domain", "")).strip().lower()
        if not sp_domain or sp_domain in seen_domains:
            continue
        seen_domains.add(sp_domain)

        transfer_basis = item.get("transfer_basis", "unknown")
        if transfer_basis not in (
            "adequacy_decision",
            "SCCs",
            "BCRs",
            "consent",
            "none",
            "unknown",
        ):
            transfer_basis = "unknown"

        sp_source = item.get("source", "llm_search")
        if sp_source not in (
            "scrape_subprocessor_page",
            "scrape_privacy_policy",
            "llm_search",
        ):
            sp_source = "llm_search"

        subprocessors.append(
            Subprocessor(
                domain=sp_domain,
                company_name=str(item.get("company_name", sp_domain)),
                hq_country=str(item.get("hq_country", "")),
                hq_country_code=str(item.get("hq_country_code", "")).upper()[:2],
                purposes=[
                    str(p) for p in item.get("purposes", []) if isinstance(p, str)
                ],
                data_categories=[
                    str(c)
                    for c in item.get("data_categories", [])
                    if isinstance(c, str)
                ],
                transfer_basis=transfer_basis,
                source_url=str(item.get("source_url", detected_source_url)),
                source=sp_source,
                last_fetched=date.today().isoformat(),
            )
        )

    return SubprocessorRecord(
        fetched_at=now,
        source_url=detected_source_url,
        subprocessors=subprocessors,
        fetch_status="ok" if subprocessors else "not_found",
    )
