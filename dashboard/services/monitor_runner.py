"""Unified monitor functions — single source of truth for SAR and SP monitoring.

Used by both the CLI (monitor.py) and the dashboard (monitor_bp.py).
All paths are passed as explicit arguments — no Flask ``g`` dependency —
so every function is safe for CLI, web routes, and background threads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from auth.gmail_oauth import get_gmail_service
from contact_resolver import cost_tracker
from letter_engine.tracker import get_log
from reply_monitor.attachment_handler import handle_attachment
from reply_monitor.classifier import (
    _ACTION_DRAFT_TAGS,
    classify,
    generate_reply_draft,
    reextract_data_links,
)
from reply_monitor.fetcher import _extract_body, fetch_replies_for_sar
from reply_monitor.models import AttachmentCatalog, CompanyState, ReplyRecord
from reply_monitor.state_manager import (
    _ACTION_TAGS,
    compute_status,
    deadline_from_sent,
    domain_from_sent_record,
    load_state,
    promote_latest_attempt,
    save_state,
    update_state,
)
from reply_monitor.url_verifier import CLASSIFICATION, verify_if_needed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_sar_monitor(
    account: str,
    *,
    state_path: Path,
    tokens_dir: Path,
    data_dir: Path,
    sp_requests_path: Path,
    verbose: bool = False,
) -> tuple[object, str, dict, dict[str, int]]:
    """Run SAR monitor for *account*.

    Returns ``(service, email, states, new_counts)`` where *states* is the
    saved state dict and *new_counts* maps domain → number of new replies
    found this run.  The CLI uses these for the summary table and bounce
    retries; the dashboard discards them.
    """
    service, email = get_gmail_service(
        email_hint=account, tokens_dir=tokens_dir
    )

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    sent_log = get_log(data_dir=data_dir)
    if not sent_log:
        if verbose:
            print("No sent SARs found. Nothing to monitor.")
        return service, email, {}, {}

    states = load_state(email, path=state_path)

    # Group by domain and promote most recent attempt to active state
    records_by_domain: dict[str, list[dict]] = {}
    for record in sent_log:
        domain = domain_from_sent_record(record)
        records_by_domain.setdefault(domain, []).append(record)

    for domain, records in records_by_domain.items():
        states[domain] = promote_latest_attempt(
            domain=domain,
            sent_records=records,
            existing_state=states.get(domain),
            deadline_fn=deadline_from_sent,
        )

    new_counts: dict[str, int] = {}

    for domain, records in records_by_domain.items():
        latest_record = max(records, key=lambda r: r.get("sent_at", ""))
        state = states[domain]

        # Dedup against current AND past-attempt replies (bug fix: app.py was
        # missing past-attempts, causing re-classification of archived messages)
        existing_ids = {r.gmail_message_id for r in state.replies}
        for pa in state.past_attempts:
            for r in pa.get("replies", []):
                existing_ids.add(r["gmail_message_id"])

        if verbose:
            print(
                f"Fetching replies for {state.company_name} ({domain})...",
                end=" ",
                flush=True,
            )

        new_messages = fetch_replies_for_sar(
            service, latest_record, existing_ids, user_email=email, verbose=verbose
        )

        if verbose:
            print(f"{len(new_messages)} new message(s)")

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            if msg.get("from_self"):
                new_replies.append(
                    ReplyRecord(
                        gmail_message_id=msg["id"],
                        received_at=msg["received_at"],
                        from_addr=msg["from"],
                        subject=msg["subject"],
                        snippet=msg.get("body", msg["snippet"]) or msg["snippet"],
                        tags=["YOUR_REPLY"],
                        extracted={},
                        llm_used=False,
                        has_attachment=False,
                        attachment_catalog=None,
                    )
                )
                continue

            result = classify(msg, api_key=api_key)
            catalog_dict = None
            if msg.get("has_attachment"):
                for part in msg.get("parts", []):
                    cat = handle_attachment(service, msg["id"], part, domain)
                    if cat:
                        if not cat.schema and api_key:
                            from reply_monitor.link_downloader import _enrich_schema

                            _enrich_schema(cat, api_key, domain=domain)
                        catalog_dict = cat.to_dict()
                        break

            # Inline data: build schema from email body text
            if "DATA_PROVIDED_INLINE" in result.tags and not catalog_dict and api_key:
                catalog_dict = _build_inline_schema(
                    msg.get("body", ""), api_key, state.company_name, domain, verbose
                )

            draft = ""
            review_status = ""
            if any(t in _ACTION_DRAFT_TAGS for t in result.tags):
                draft = generate_reply_draft(
                    msg.get("body", msg["snippet"]),
                    result.tags,
                    state.company_name,
                    api_key=api_key,
                )
                review_status = "pending" if draft else ""

            reply = ReplyRecord(
                gmail_message_id=msg["id"],
                received_at=msg["received_at"],
                from_addr=msg["from"],
                subject=msg["subject"],
                snippet=msg["snippet"],
                tags=result.tags,
                extracted=result.extracted,
                llm_used=result.llm_used,
                has_attachment=msg["has_attachment"],
                attachment_catalog=catalog_dict,
                suggested_reply=draft,
                reply_review_status=review_status,
            )
            new_replies.append(reply)

            # Portal verification for WRONG_CHANNEL / CONFIRMATION / DATA_PROVIDED_PORTAL
            _verify_and_submit_portal(
                reply=reply,
                state=state,
                domain=domain,
                scan_email=email,
                verbose=verbose,
                data_dir=data_dir,
            )

            if verbose:
                print(f"  [{state.company_name}] {result.tags} -- {msg['snippet'][:60]}")

        new_counts[domain] = len(new_replies)
        if new_replies:
            states[domain] = update_state(state, new_replies)

            # Auto-dismiss stale drafts when a YOUR_REPLY arrives
            has_your_reply = any("YOUR_REPLY" in r.tags for r in new_replies)
            if has_your_reply:
                for r in states[domain].replies:
                    if r.reply_review_status == "pending" and bool(
                        set(r.tags) & _ACTION_TAGS
                    ):
                        r.reply_review_status = "dismissed"
                        if verbose:
                            print(
                                f"  [{state.company_name}] auto-dismissed stale draft on {r.gmail_message_id[:8]}"
                            )

    save_state(email, states, path=state_path)

    # Auto-download data links (Playwright handles Cloudflare-protected sites)
    auto_download_data_links(email, states, api_key, verbose=verbose, state_path=state_path)
    # Auto-analyze inline data replies
    auto_analyze_inline_data(
        email, states, api_key, verbose=verbose,
        state_path=state_path, tokens_dir=tokens_dir,
    )

    return service, email, states, new_counts


def run_sp_monitor(
    account: str,
    *,
    state_path: Path,
    tokens_dir: Path,
    data_dir: Path,
    sp_requests_path: Path,
    sp_state_path: Path,
    service=None,
    email: str = "",
    verbose: bool = False,
) -> None:
    """Poll Gmail for replies to subprocessor disclosure requests.

    Pass *service* and *email* (from ``run_sar_monitor``) to reuse an
    already-authenticated Gmail connection and avoid a second OAuth prompt.
    """
    log = get_log(path=sp_requests_path)
    if not log:
        return

    if service is not None and email:
        svc, eml = service, email
    else:
        svc, eml = get_gmail_service(email_hint=account, tokens_dir=tokens_dir)

    sp_states = load_state(eml, path=sp_state_path)
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    by_domain: dict[str, list[dict]] = {}
    for rec in log:
        domain = rec.get("domain", "")
        if domain:
            by_domain.setdefault(domain, []).append(rec)

    total_new = 0
    for domain, records in by_domain.items():
        sp_states[domain] = promote_latest_attempt(
            domain=domain,
            sent_records=records,
            existing_state=sp_states.get(domain),
            deadline_fn=deadline_from_sent,
        )
        state = sp_states[domain]

        # Dedup against current AND past-attempt replies
        existing_ids = {r.gmail_message_id for r in state.replies}
        for pa in state.past_attempts:
            for r in pa.get("replies", []):
                existing_ids.add(r["gmail_message_id"])

        latest_record = max(records, key=lambda r: r.get("sent_at", ""))
        new_messages = fetch_replies_for_sar(
            svc, latest_record, existing_ids, user_email=eml
        )

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            if msg.get("from_self"):
                new_replies.append(
                    ReplyRecord(
                        gmail_message_id=msg["id"],
                        received_at=msg["received_at"],
                        from_addr=msg["from"],
                        subject=msg["subject"],
                        snippet=msg.get("body", msg["snippet"]) or msg["snippet"],
                        tags=["YOUR_REPLY"],
                        extracted={},
                        llm_used=False,
                        has_attachment=False,
                        attachment_catalog=None,
                    )
                )
                continue
            result = classify(msg, api_key=api_key)

            draft = ""
            review_status = ""
            if any(t in _ACTION_DRAFT_TAGS for t in result.tags):
                draft = generate_reply_draft(
                    msg.get("body", msg["snippet"]),
                    result.tags,
                    state.company_name,
                    api_key=api_key,
                )
                review_status = "pending" if draft else ""

            new_replies.append(
                ReplyRecord(
                    gmail_message_id=msg["id"],
                    received_at=msg["received_at"],
                    from_addr=msg["from"],
                    subject=msg["subject"],
                    snippet=msg["snippet"],
                    tags=result.tags,
                    extracted=result.extracted,
                    llm_used=result.llm_used,
                    has_attachment=msg["has_attachment"],
                    attachment_catalog=None,
                    suggested_reply=draft,
                    reply_review_status=review_status,
                )
            )

        total_new += len(new_replies)
        if new_replies:
            sp_states[domain] = update_state(state, new_replies)

            # Auto-dismiss stale drafts when user replied via Gmail
            has_your_reply = any("YOUR_REPLY" in r.tags for r in new_replies)
            if has_your_reply:
                for r in sp_states[domain].replies:
                    if r.reply_review_status == "pending" and bool(
                        set(r.tags) & _ACTION_TAGS
                    ):
                        r.reply_review_status = "dismissed"

    save_state(eml, sp_states, path=sp_state_path)

    if total_new and verbose:
        print(
            f"[subprocessor] {total_new} new reply(ies) for subprocessor disclosure requests"
        )


def auto_download_data_links(
    account: str,
    states: dict,
    api_key: str | None,
    verbose: bool = False,
    *,
    state_path: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Download DATA_PROVIDED_LINK replies that have URL(s) but no catalog.

    Supports both single ``data_link`` (legacy) and ``data_links`` list
    (multi-file deliveries like Substack).  Pass either *state_path* or
    *data_dir* so the function knows where to save after downloads.
    """
    from reply_monitor.link_downloader import download_data_link

    needs_save = False
    for domain, state in states.items():
        for reply in state.replies:
            if "DATA_PROVIDED_LINK" not in reply.tags:
                continue
            if reply.attachment_catalog:
                continue

            urls: list[str] = reply.extracted.get("data_links") or []
            if not urls and reply.extracted.get("data_link"):
                urls = [reply.extracted["data_link"]]
            if not urls:
                continue

            for url in urls:
                if verbose:
                    print(f"  [auto-download] {domain}: fetching {url[:70]}...")
                try:
                    result = download_data_link(url, domain, api_key=api_key or "")
                    if result.ok:
                        reply.attachment_catalog = result.catalog.to_dict()
                        needs_save = True
                        cats = len(result.catalog.schema)
                        files = len(result.catalog.files)
                        print(
                            f"  [auto-download] {domain}: {files} files, {cats} schema categories"
                        )
                    else:
                        print(
                            f"  [auto-download] {domain}: {result.error or ('expired' if result.expired else 'unknown')}"
                        )
                except Exception as exc:
                    print(f"  [auto-download] {domain}: {exc}")

    if needs_save:
        if state_path:
            save_state(account, states, path=state_path)
        elif data_dir:
            save_state(account, states, data_dir=data_dir)


def auto_analyze_inline_data(
    account: str,
    states: dict,
    api_key: str | None,
    verbose: bool = False,
    *,
    state_path: Path | None = None,
    tokens_dir: Path | None = None,
) -> None:
    """Analyze DATA_PROVIDED_INLINE replies that lack an attachment_catalog.

    Fetches the full email body from Gmail, then runs LLM schema analysis
    via ``build_schema_from_body``.
    """
    if not api_key:
        return

    from reply_monitor.schema_builder import build_schema_from_body

    try:
        service, _email = get_gmail_service(
            email_hint=account, tokens_dir=tokens_dir
        )
    except Exception:
        return

    needs_save = False
    for domain, state in states.items():
        for reply in state.replies:
            if "DATA_PROVIDED_INLINE" not in reply.tags:
                continue
            if reply.attachment_catalog:
                continue

            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=reply.gmail_message_id, format="full")
                    .execute()
                )
                body = _extract_body(msg.get("payload", {}))
            except Exception as exc:
                print(f"[inline-schema] {domain}: failed to fetch body -- {exc}")
                continue

            if not body:
                continue

            if verbose:
                print(f"[inline-schema] {domain}: analyzing inline data...")
            try:
                result = build_schema_from_body(
                    body, api_key, company_name=state.company_name
                )
                if result:
                    cat = AttachmentCatalog(
                        path="",
                        size_bytes=len(body.encode("utf-8")),
                        file_type="email_body",
                        files=[],
                        categories=[
                            c["name"] for c in result.get("categories", [])
                        ],
                        schema=result.get("categories", []),
                        services=result.get("services", []),
                        export_meta=result.get("export_meta", {}),
                    )
                    reply.attachment_catalog = cat.to_dict()
                    needs_save = True
                    if verbose:
                        print(
                            f"[inline-schema] {domain}: {len(cat.schema)} categories"
                        )
            except Exception as exc:
                print(f"[inline-schema] {domain}: schema analysis failed -- {exc}")

    if needs_save and state_path:
        save_state(account, states, path=state_path)


def reextract_missing_links(
    account: str,
    *,
    state_path: Path,
    tokens_dir: Path,
) -> int:
    """Re-fetch Gmail bodies for DATA_PROVIDED_LINK replies with empty data_link.

    After re-extraction, triggers auto-download and auto-analyze for any
    newly populated URLs.  Returns the count of replies with a populated
    ``data_link`` after processing.
    """
    states = load_state(account, path=state_path)

    pending = [
        (domain, reply)
        for domain, state in states.items()
        for reply in state.replies
        if "DATA_PROVIDED_LINK" in reply.tags and not reply.extracted.get("data_link")
    ]

    if not pending:
        return 0

    service, _email = get_gmail_service(
        email_hint=account, tokens_dir=tokens_dir
    )

    needs_update = False
    for domain, reply in pending:
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=reply.gmail_message_id, format="full")
                .execute()
            )
            body = _extract_body(msg.get("payload", {}))
            new_extracted = reextract_data_links(reply.to_dict(), body)
            if new_extracted.get("data_link"):
                reply.extracted = new_extracted
                needs_update = True
        except Exception as exc:
            print(f"[reextract] {domain}/{reply.gmail_message_id}: {exc}")

    if needs_update:
        save_state(account, states, path=state_path)
        # Reload and trigger auto-download / auto-analyze for new URLs
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        states = load_state(account, path=state_path)
        auto_download_data_links(account, states, api_key, state_path=state_path)
        auto_analyze_inline_data(
            account, states, api_key, state_path=state_path, tokens_dir=tokens_dir,
        )

    return sum(
        1
        for state in states.values()
        for r in state.replies
        if "DATA_PROVIDED_LINK" in r.tags and r.extracted.get("data_link")
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_inline_schema(
    body: str,
    api_key: str,
    company_name: str,
    domain: str,
    verbose: bool,
) -> dict | None:
    """Build an AttachmentCatalog dict from inline email body data."""
    if not body:
        return None
    from reply_monitor.schema_builder import build_schema_from_body

    try:
        schema_result = build_schema_from_body(
            body, api_key, company_name=company_name
        )
        if schema_result:
            cat = AttachmentCatalog(
                path="",
                size_bytes=len(body.encode("utf-8")),
                file_type="email_body",
                files=[],
                categories=[
                    c["name"] for c in schema_result.get("categories", [])
                ],
                schema=schema_result.get("categories", []),
                services=schema_result.get("services", []),
                export_meta=schema_result.get("export_meta", {}),
            )
            if verbose:
                print(
                    f"  [{company_name}] inline data schema: "
                    f"{len(cat.schema)} categories, "
                    f"{sum(len(c.get('fields', [])) for c in cat.schema)} fields"
                )
            return cat.to_dict()
    except Exception as exc:
        print(f"[monitor] inline schema failed for {domain}: {exc}")
    return None


_VERIFY_TAGS = {"WRONG_CHANNEL", "CONFIRMATION_REQUIRED", "DATA_PROVIDED_PORTAL"}


def _verify_and_submit_portal(
    *,
    reply: ReplyRecord,
    state: CompanyState,
    domain: str,
    scan_email: str,
    verbose: bool,
    data_dir: Path,
) -> None:
    """Verify portal URL on qualifying replies and auto-submit if it's a GDPR portal."""
    if not set(reply.tags) & _VERIFY_TAGS:
        return
    portal_url = reply.extracted.get("portal_url", "")
    if not portal_url:
        return

    try:
        verification = verify_if_needed(
            portal_url, existing=reply.portal_verification
        )
        reply.portal_verification = verification

        if verbose:
            print(
                f"  [{state.company_name}] portal verified: {verification['classification']}"
            )

        if verification["classification"] == CLASSIFICATION.GDPR_PORTAL:
            _try_portal_submit(
                domain=domain,
                portal_url=portal_url,
                state=state,
                reply=reply,
                scan_email=scan_email,
                verbose=verbose,
                data_dir=data_dir,
            )
    except Exception as exc:
        if verbose:
            print(f"  [{state.company_name}] portal verification failed: {exc}")


def _try_portal_submit(
    *,
    domain: str,
    portal_url: str,
    state: CompanyState,
    reply: ReplyRecord,
    scan_email: str,
    verbose: bool = False,
    data_dir: Path | None = None,
) -> None:
    """Attempt to auto-submit SAR via portal for WRONG_CHANNEL replies."""
    try:
        from letter_engine.composer import compose
        from letter_engine.models import SARLetter
        from contact_resolver.models import CompanyRecord
        from portal_submitter.submitter import submit_portal

        companies_path = Path(__file__).resolve().parent.parent.parent / "data" / "companies.json"
        record = None
        if companies_path.exists():
            companies = json.loads(companies_path.read_text())
            if domain in companies:
                record = CompanyRecord.from_dict(companies[domain])

        if record:
            record.contact.preferred_method = "portal"
            record.contact.gdpr_portal_url = portal_url
            letter = compose(record)
        else:
            from config.settings import settings

            letter = SARLetter(
                to_email=state.to_email,
                subject=f"Subject Access Request -- {settings.USER_FULL_NAME}",
                body="",
                company_name=state.company_name,
                portal_url=portal_url,
            )

        result = submit_portal(letter, scan_email)

        if result.success:
            print(
                f"  [auto-portal] {state.company_name}: submitted via {portal_url[:50]}"
            )
            if result.confirmation_ref:
                print(f"  [auto-portal]   confirmation: {result.confirmation_ref}")
            reply.reply_review_status = "dismissed"
            reply.suggested_reply = ""
        elif result.needs_manual:
            if verbose:
                print(
                    f"  [auto-portal] {state.company_name}: needs manual submission ({result.error or 'login required'})"
                )
        else:
            if verbose:
                print(
                    f"  [auto-portal] {state.company_name}: {result.error or 'unknown error'}"
                )
    except Exception as exc:
        if verbose:
            print(f"  [auto-portal] {state.company_name}: {exc}")


def _reprocess_existing(
    states: dict[str, CompanyState],
    api_key: str | None,
    *,
    verbose: bool = False,
    dry_run: bool = False,
) -> int:
    """Re-classify replies tagged HUMAN_REVIEW / AUTO_ACKNOWLEDGE / WRONG_CHANNEL.

    Returns the number of replies whose tags or extracted URLs changed.
    """
    _REPROCESS_TAGS = {"HUMAN_REVIEW", "AUTO_ACKNOWLEDGE", "WRONG_CHANNEL"}
    changed = 0

    for domain, state in states.items():
        for reply in state.replies:
            if not set(reply.tags) <= _REPROCESS_TAGS:
                continue
            old_tags = list(reply.tags)
            msg = {
                "from": reply.from_addr,
                "subject": reply.subject,
                "snippet": reply.snippet,
                "body": "",
                "has_attachment": reply.has_attachment,
            }
            new_result = classify(msg, api_key=api_key)
            tags_changed = new_result.tags != old_tags
            urls_changed = any(
                reply.extracted.get(k) != new_result.extracted.get(k)
                for k in ("data_link", "data_links", "portal_url")
            )
            if tags_changed or urls_changed:
                if verbose or dry_run:
                    detail = (
                        f"{old_tags} -> {new_result.tags}"
                        if tags_changed
                        else f"{old_tags} (URLs cleaned)"
                    )
                    print(
                        f"  [reprocess] {state.company_name} ({domain}): "
                        f"{detail}"
                        f"{'  [dry-run]' if dry_run else ''}"
                    )
                if not dry_run:
                    reply.tags = new_result.tags
                    for url_key in ("data_link", "data_links", "portal_url"):
                        if url_key in new_result.extracted:
                            reply.extracted[url_key] = new_result.extracted[url_key]
                changed += 1

    return changed


def _backfill_reply_drafts(
    states: dict[str, CompanyState],
    api_key: str | None,
    service,
    *,
    verbose: bool = False,
) -> int:
    """Generate suggested_reply for existing action-tagged replies that have none.

    Fetches the full email body from Gmail so the LLM has complete context.
    Returns the number of replies updated.
    """
    updated = 0
    for domain, state in states.items():
        for reply in state.replies:
            if reply.suggested_reply:
                continue
            if not any(t in _ACTION_DRAFT_TAGS for t in reply.tags):
                continue
            body = reply.snippet
            if service and reply.gmail_message_id:
                try:
                    msg = (
                        service.users()
                        .messages()
                        .get(userId="me", id=reply.gmail_message_id, format="full")
                        .execute()
                    )
                    full_body = _extract_body(msg.get("payload", {}))
                    if full_body:
                        body = full_body
                except Exception as exc:
                    if verbose:
                        print(
                            f"  [backfill] warning: could not fetch body for {reply.gmail_message_id}: {exc}"
                        )

            draft = generate_reply_draft(
                body, reply.tags, state.company_name, api_key=api_key
            )
            if draft:
                reply.suggested_reply = draft
                reply.reply_review_status = "pending"
                updated += 1
                if verbose:
                    print(
                        f"  [backfill] {state.company_name} ({domain}): draft generated for {reply.tags}"
                    )
    return updated
