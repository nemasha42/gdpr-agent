"""Reply monitor CLI — scan Gmail for GDPR SAR replies and update state.

Usage:
    python monitor.py [--account EMAIL] [--verbose]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env so ANTHROPIC_API_KEY is available for LLM classification fallback
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from auth.gmail_oauth import get_gmail_service
from contact_resolver import cost_tracker
from letter_engine.tracker import get_log, _SUBPROCESSOR_REQUESTS_PATH
from reply_monitor.attachment_handler import handle_attachment
from reply_monitor.classifier import classify, generate_reply_draft, _ACTION_DRAFT_TAGS
from reply_monitor.fetcher import fetch_replies_for_sar
from reply_monitor.models import CompanyState, ReplyRecord
from reply_monitor.url_verifier import verify_if_needed, CLASSIFICATION
from reply_monitor.state_manager import (
    _ACTION_TAGS,
    _SUBPROCESSOR_STATE_PATH,
    compute_status,
    days_remaining,
    deadline_from_sent,
    domain_from_sent_record,
    load_state,
    promote_latest_attempt,
    save_state,
    update_state,
)

_STATE_PATH = Path(__file__).parent / "user_data" / "reply_state.json"

# Maximum total address attempts before marking ADDRESS_NOT_FOUND
MAX_ADDRESS_ATTEMPTS = 3

_W_COMPANY = 18
_W_STATUS  = 16
_W_DAY     = 8
_W_TAGS    = 16
_INNER     = (_W_COMPANY + 2) + 1 + (_W_STATUS + 2) + 1 + (_W_DAY + 2) + 1 + (_W_TAGS + 2)

# Short tag abbreviations for the summary table
_TAG_ABBR: dict[str, str] = {
    "AUTO_ACKNOWLEDGE":      "ACK",
    "OUT_OF_OFFICE":         "OOO",
    "BOUNCE_PERMANENT":      "BOUNCE",
    "BOUNCE_TEMPORARY":      "BOUNCE_TMP",
    "CONFIRMATION_REQUIRED": "CONFIRM",
    "IDENTITY_REQUIRED":     "ID_REQ",
    "MORE_INFO_REQUIRED":    "MORE_INFO",
    "WRONG_CHANNEL":         "WRONG_CH",
    "REQUEST_ACCEPTED":      "ACCEPTED",
    "EXTENDED":              "EXTENDED",
    "IN_PROGRESS":           "IN_PROG",
    "DATA_PROVIDED_LINK":    "DATA_LINK",
    "DATA_PROVIDED_ATTACHMENT": "DATA_ATT",
    "DATA_PROVIDED_INLINE":  "DATA_INL",
    "DATA_PROVIDED_PORTAL":  "DATA_PORT",
    "REQUEST_DENIED":        "DENIED",
    "NO_DATA_HELD":          "NO_DATA",
    "NOT_GDPR_APPLICABLE":   "NOT_GDPR",
    "FULFILLED_DELETION":    "DELETED",
    "HUMAN_REVIEW":          "REVIEW",
    "YOUR_REPLY":            "YOU",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()

    if not (Path(__file__).parent / "credentials.json").exists():
        print("credentials.json not found. Run python setup.py first.")
        sys.exit(1)

    print("Connecting to Gmail...")
    from dashboard.user_model import user_data_dir
    service, email = get_gmail_service(email_hint=args.account)
    data_dir = user_data_dir(email)
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Monitoring: {email}\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    sent_log = get_log(data_dir=data_dir)
    if not sent_log:
        print("No sent SARs found in user_data/sent_letters.json. Nothing to monitor.")
        return

    # Load existing state
    states = load_state(email, data_dir=data_dir)

    # Group all sent records by domain (multiple attempts possible per domain)
    records_by_domain: dict[str, list[dict]] = {}
    for record in sent_log:
        domain = domain_from_sent_record(record)
        records_by_domain.setdefault(domain, []).append(record)

    # Ensure every domain has a CompanyState that reflects the most recent attempt.
    # promote_latest_attempt handles multi-attempt domains by archiving older attempts
    # into past_attempts and making the newest letter the active state.
    for domain, records in records_by_domain.items():
        states[domain] = promote_latest_attempt(
            domain=domain,
            sent_records=records,
            existing_state=states.get(domain),
            deadline_fn=deadline_from_sent,
        )

    new_counts: dict[str, int] = {}

    for domain, records in records_by_domain.items():
        # Use the most recent sent record for fetching (active attempt)
        latest_record = max(records, key=lambda r: r.get("sent_at", ""))

        state = states[domain]
        # Exclude message IDs already seen in the current attempt AND all past attempts
        # to prevent re-classifying messages that moved between attempts (e.g. newsletters
        # that were NON_GDPR in attempt 1 must not be re-fetched in attempt 2).
        existing_ids = {r.gmail_message_id for r in state.replies}
        for pa in state.past_attempts:
            for r in pa.get("replies", []):
                existing_ids.add(r["gmail_message_id"])

        if args.verbose:
            print(f"Fetching replies for {state.company_name} ({domain})...", end=" ", flush=True)

        new_messages = fetch_replies_for_sar(service, latest_record, existing_ids, user_email=email, verbose=args.verbose)

        if args.verbose:
            print(f"{len(new_messages)} new message(s)")

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            if msg.get("from_self"):
                # Manual reply sent by the user directly in Gmail — record without classifying
                reply = ReplyRecord(
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
                new_replies.append(reply)
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
                body = msg.get("body", "")
                if body:
                    from reply_monitor.schema_builder import build_schema_from_body
                    try:
                        schema_result = build_schema_from_body(body, api_key, company_name=state.company_name)
                        if schema_result:
                            from reply_monitor.models import AttachmentCatalog
                            cat = AttachmentCatalog(
                                path="",
                                size_bytes=len(body.encode("utf-8")),
                                file_type="email_body",
                                files=[],
                                categories=[c["name"] for c in schema_result.get("categories", [])],
                                schema=schema_result.get("categories", []),
                                services=schema_result.get("services", []),
                                export_meta=schema_result.get("export_meta", {}),
                            )
                            catalog_dict = cat.to_dict()
                            if args.verbose:
                                print(f"  [{state.company_name}] inline data schema: "
                                      f"{len(cat.schema)} categories, {sum(len(c.get('fields', [])) for c in cat.schema)} fields")
                    except Exception as exc:
                        print(f"[monitor] inline schema failed for {domain}: {exc}")

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

            # --- Portal verification for WRONG_CHANNEL replies ---
            _VERIFY_TAGS = {"WRONG_CHANNEL", "CONFIRMATION_REQUIRED", "DATA_PROVIDED_PORTAL"}
            if set(reply.tags) & _VERIFY_TAGS:
                portal_url = reply.extracted.get("portal_url", "")
                if portal_url:
                    try:
                        verification = verify_if_needed(portal_url, existing=reply.portal_verification)
                        reply.portal_verification = verification

                        if args.verbose:
                            print(f"  [{state.company_name}] portal verified: {verification['classification']}")

                        # Auto-submit if it's a real GDPR portal
                        if verification["classification"] == CLASSIFICATION.GDPR_PORTAL:
                            _try_portal_submit(
                                domain=domain,
                                portal_url=portal_url,
                                state=state,
                                reply=reply,
                                scan_email=email,
                                verbose=args.verbose,
                                data_dir=data_dir,
                            )
                    except Exception as exc:
                        if args.verbose:
                            print(f"  [{state.company_name}] portal verification failed: {exc}")

            if args.verbose:
                print(f"  [{state.company_name}] {result.tags} — {msg['snippet'][:60]}")

        new_counts[domain] = len(new_replies)
        if new_replies:
            states[domain] = update_state(state, new_replies)

            # Auto-dismiss stale drafts when a YOUR_REPLY arrives — the user
            # already responded via Gmail, so pending drafts are obsolete.
            has_your_reply = any("YOUR_REPLY" in r.tags for r in new_replies)
            if has_your_reply:
                for r in states[domain].replies:
                    if (
                        r.reply_review_status == "pending"
                        and bool(set(r.tags) & _ACTION_TAGS)
                    ):
                        r.reply_review_status = "dismissed"
                        if args.verbose:
                            print(f"  [{state.company_name}] auto-dismissed stale draft on {r.gmail_message_id[:8]}")

    save_state(email, states, data_dir=data_dir)

    # --reprocess: re-classify existing HUMAN_REVIEW / AUTO_ACKNOWLEDGE replies
    if args.reprocess:
        sp_states = load_state(email, path=data_dir / "subprocessor_reply_state.json")
        all_states = {**states, **{f"sp:{k}": v for k, v in sp_states.items()}}
        n_changed = _reprocess_existing(all_states, api_key, verbose=args.verbose, dry_run=args.dry_run)
        print(f"[reprocess] {n_changed} reply(ies) reclassified")
        if n_changed and not args.dry_run:
            # Split back and save both
            sar_states = {k: v for k, v in all_states.items() if not k.startswith("sp:")}
            sp_states2 = {k[3:]: v for k, v in all_states.items() if k.startswith("sp:")}
            save_state(email, sar_states, data_dir=data_dir)
            save_state(email, sp_states2, path=data_dir / "subprocessor_reply_state.json")
        if args.dry_run:
            print("[reprocess] dry-run: no changes saved")
        return

    # --draft-backfill: generate suggested_reply for existing action-tagged replies
    if args.draft_backfill:
        sp_states = load_state(email, path=data_dir / "subprocessor_reply_state.json")
        n_sar = _backfill_reply_drafts(states, api_key, service, verbose=args.verbose)
        n_sp = _backfill_reply_drafts(sp_states, api_key, service, verbose=args.verbose)
        print(f"[draft-backfill] {n_sar} SAR + {n_sp} SP reply draft(s) generated")
        if n_sar:
            save_state(email, states, data_dir=data_dir)
        if n_sp:
            save_state(email, sp_states, path=data_dir / "subprocessor_reply_state.json")
        cost_tracker.print_cost_summary()
        return

    # Poll Gmail for subprocessor disclosure request replies
    sp_count = _monitor_subprocessor_requests(service, email, email, data_dir=data_dir)
    if sp_count and args.verbose:
        print(f"[subprocessor] {sp_count} new reply(ies) for subprocessor disclosure requests\n")

    # Re-resolve and auto-send for bounced companies
    if _handle_bounce_retries(email, states, verbose=args.verbose, data_dir=data_dir):
        save_state(email, states, data_dir=data_dir)

    # Auto-download data links (Playwright handles Cloudflare-protected sites)
    _auto_download_data_links(email, states, api_key, verbose=args.verbose, data_dir=data_dir)

    # Print summary table
    _print_summary(email, states, new_counts)
    cost_tracker.print_cost_summary()


def _monitor_subprocessor_requests(service, email: str, account: str, *, data_dir: Path) -> int:
    """Poll Gmail for replies to subprocessor disclosure requests. Returns new reply count."""
    log = get_log(path=data_dir / "subprocessor_requests.json")
    if not log:
        return 0

    sp_states = load_state(account, path=data_dir / "subprocessor_reply_state.json")

    # Group by domain (most recent sent wins)
    by_domain: dict[str, list[dict]] = {}
    for rec in log:
        domain = rec.get("domain", "")
        if domain:
            by_domain.setdefault(domain, []).append(rec)

    total_new = 0
    api_key = __import__("os").environ.get("ANTHROPIC_API_KEY")

    for domain, records in by_domain.items():
        sp_states[domain] = promote_latest_attempt(
            domain=domain,
            sent_records=records,
            existing_state=sp_states.get(domain),
            deadline_fn=deadline_from_sent,
        )
        state = sp_states[domain]
        existing_ids = {r.gmail_message_id for r in state.replies}
        for pa in state.past_attempts:
            for r in pa.get("replies", []):
                existing_ids.add(r["gmail_message_id"])

        latest_record = max(records, key=lambda r: r.get("sent_at", ""))
        new_messages = fetch_replies_for_sar(service, latest_record, existing_ids, user_email=email)

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            if msg.get("from_self"):
                new_replies.append(ReplyRecord(
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
                ))
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

            new_replies.append(ReplyRecord(
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
            ))

        total_new += len(new_replies)
        if new_replies:
            sp_states[domain] = update_state(state, new_replies)

            # Auto-dismiss stale drafts when user replied via Gmail
            has_your_reply = any("YOUR_REPLY" in r.tags for r in new_replies)
            if has_your_reply:
                for r in sp_states[domain].replies:
                    if (
                        r.reply_review_status == "pending"
                        and bool(set(r.tags) & _ACTION_TAGS)
                    ):
                        r.reply_review_status = "dismissed"

    save_state(account, sp_states, path=data_dir / "subprocessor_reply_state.json")
    return total_new


def _handle_bounce_retries(
    account_email: str,
    states: dict[str, CompanyState],
    verbose: bool = False,
    *,
    data_dir: Path | None = None,
) -> bool:
    """For each BOUNCED company, re-resolve and auto-send to a new address.

    Archives the failed attempt into past_attempts, resets active state to the
    new address, and updates sent_letters.json via tracker.record_sent().

    Returns True if any state was changed.
    """
    from contact_resolver.resolver import ContactResolver
    from letter_engine.composer import compose
    from letter_engine.sender import send_letter

    resolver = ContactResolver()
    changed = False

    for domain, state in states.items():
        if state.address_exhausted:
            continue
        if compute_status(state) != "BOUNCED":
            continue

        total_attempts = 1 + len(state.past_attempts)
        # Collect every email address tried so far (current + all past)
        tried_emails: set[str] = {state.to_email.lower()} if state.to_email else set()
        for pa in state.past_attempts:
            if pa.get("to_email"):
                tried_emails.add(pa["to_email"].lower())

        if total_attempts >= MAX_ADDRESS_ATTEMPTS:
            print(f"  [bounce-retry] {state.company_name}: max attempts ({MAX_ADDRESS_ATTEMPTS}) reached — marking ADDRESS_NOT_FOUND")
            state.address_exhausted = True
            changed = True
            continue

        print(f"  [bounce-retry] {state.company_name}: attempt {total_attempts}/{MAX_ADDRESS_ATTEMPTS} failed, re-resolving...")
        new_record = resolver.resolve(domain, state.company_name, exclude_emails=tried_emails)

        if not new_record:
            print(f"  [bounce-retry] {state.company_name}: no alternative address found — ADDRESS_NOT_FOUND")
            state.address_exhausted = True
            changed = True
            continue

        new_email = (new_record.contact.privacy_email or new_record.contact.dpo_email or "").lower()
        if not new_email or new_email in tried_emails:
            print(f"  [bounce-retry] {state.company_name}: resolver returned same/no address — ADDRESS_NOT_FOUND")
            state.address_exhausted = True
            changed = True
            continue

        # Compose and send to new address
        letter = compose(new_record)
        tokens_dir = data_dir / "tokens" if data_dir else None
        success, msg_id, thread_id = send_letter(
            letter, scan_email=account_email, data_dir=data_dir, tokens_dir=tokens_dir,
        )

        if not success:
            print(f"  [bounce-retry] {state.company_name}: send to {new_email} failed — will retry next run")
            continue

        print(f"  [bounce-retry] {state.company_name}: sent to {new_email} (attempt {total_attempts + 1})")

        # Archive current attempt into past_attempts
        state.past_attempts.append({
            "to_email": state.to_email,
            "gmail_thread_id": state.gmail_thread_id,
            "sar_sent_at": state.sar_sent_at,
            "deadline": state.deadline,
            "replies": [r.to_dict() for r in state.replies],
        })

        # Reset active attempt to the new address
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        state.replies = []
        state.to_email = letter.to_email
        state.subject = letter.subject
        state.gmail_thread_id = thread_id
        state.sar_sent_at = now
        state.deadline = deadline_from_sent(now)
        state.last_checked = ""
        changed = True

    return changed


def _auto_download_data_links(
    account: str, states: dict, api_key: str | None, verbose: bool = False,
    *, data_dir: Path | None = None,
) -> None:
    """Automatically download any DATA_PROVIDED_LINK replies that have a URL but no catalog."""
    from reply_monitor.link_downloader import download_data_link
    from reply_monitor.state_manager import save_state as _save

    needs_save = False
    for domain, state in states.items():
        for reply in state.replies:
            if "DATA_PROVIDED_LINK" not in reply.tags:
                continue
            if reply.attachment_catalog:
                continue  # already downloaded

            # Support single data_link (legacy) and data_links list (multi-file, e.g. Substack)
            urls: list[str] = reply.extracted.get("data_links") or []
            if not urls and reply.extracted.get("data_link"):
                urls = [reply.extracted["data_link"]]
            if not urls:
                continue

            for url in urls:
                if verbose:
                    print(f"  [auto-download] {domain}: fetching {url[:70]}…")
                try:
                    result = download_data_link(url, domain, api_key=api_key or "")
                    if result.ok:
                        reply.attachment_catalog = result.catalog.to_dict()
                        needs_save = True
                        cats = len(result.catalog.schema)
                        files = len(result.catalog.files)
                        print(f"  [auto-download] ✓ {domain}: {files} files, {cats} schema categories")
                    else:
                        print(f"  [auto-download] ✗ {domain}: {result.error or ('expired' if result.expired else 'unknown')}")
                except Exception as exc:
                    print(f"  [auto-download] ✗ {domain}: {exc}")

    if needs_save:
        _save(account, states, data_dir=data_dir)


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
    """Attempt to auto-submit SAR via portal when WRONG_CHANNEL reply points to a GDPR portal."""
    try:
        from letter_engine.composer import compose
        from letter_engine.models import SARLetter
        from contact_resolver.models import CompanyRecord
        from portal_submitter.submitter import submit_portal

        # Load company record if available
        import json
        companies_path = Path(__file__).parent / "data" / "companies.json"
        record = None
        if companies_path.exists():
            companies = json.loads(companies_path.read_text())
            if domain in companies:
                record = CompanyRecord.from_dict(companies[domain])

        if record:
            # Update the record to use portal method
            record.contact.preferred_method = "portal"
            record.contact.gdpr_portal_url = portal_url
            letter = compose(record)
        else:
            # Fallback: build minimal letter from state
            from config.settings import settings
            letter = SARLetter(
                to_email=state.to_email,
                subject=f"Subject Access Request — {settings.USER_FULL_NAME}",
                body="",
                company_name=state.company_name,
                portal_url=portal_url,
            )

        result = submit_portal(letter, scan_email)

        if result.success:
            print(f"  [auto-portal] ✓ {state.company_name}: submitted via {portal_url[:50]}")
            if result.confirmation_ref:
                print(f"  [auto-portal]   confirmation: {result.confirmation_ref}")
            # Dismiss the WRONG_CHANNEL draft — auto-handled
            reply.reply_review_status = "dismissed"
            reply.suggested_reply = ""
        elif result.needs_manual:
            if verbose:
                print(f"  [auto-portal] {state.company_name}: needs manual submission ({result.error or 'login required'})")
        else:
            if verbose:
                print(f"  [auto-portal] ✗ {state.company_name}: {result.error or 'unknown error'}")
    except Exception as exc:
        if verbose:
            print(f"  [auto-portal] ✗ {state.company_name}: {exc}")


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------


def _print_summary(account: str, states: dict[str, CompanyState], new_counts: dict[str, int]) -> None:
    def _row(company: str, status: str, day: str, tags: str) -> str:
        return (
            f"│  {company[:_W_COMPANY]:<{_W_COMPANY}}"
            f"│  {status[:_W_STATUS]:<{_W_STATUS}}"
            f"│  {day[:_W_DAY]:<{_W_DAY}}"
            f"│  {tags[:_W_TAGS]:<{_W_TAGS}}│"
        )

    sep_top  = f"╔{'═' * _INNER}╗"
    title    = f"║  {'REPLY MONITOR — ' + account:<{_INNER - 2}}║"
    sep_h    = (
        f"╠{'═' * (_W_COMPANY + 2)}"
        f"╦{'═' * (_W_STATUS + 2)}"
        f"╦{'═' * (_W_DAY + 2)}"
        f"╦{'═' * (_W_TAGS + 2)}╣"
    )
    sep_m    = (
        f"├{'─' * (_W_COMPANY + 2)}"
        f"┼{'─' * (_W_STATUS + 2)}"
        f"┼{'─' * (_W_DAY + 2)}"
        f"┼{'─' * (_W_TAGS + 2)}┤"
    )
    sep_bot  = f"└{'─' * _INNER}┘"

    lines = [sep_top, title, sep_h, _row("Company", "Status", "Day", "Tags"), sep_h.replace("╦", "╬").replace("╣", "╣")]

    sorted_states = sorted(
        states.items(),
        key=lambda kv: -_status_priority(compute_status(kv[1])),
    )

    for i, (domain, state) in enumerate(sorted_states):
        status = compute_status(state)
        remaining = days_remaining(state.sar_sent_at)
        elapsed = 30 - remaining
        day_str = f"{max(0, elapsed)}/30"

        all_tags: list[str] = []
        for r in state.replies:
            for t in r.tags:
                abbr = _TAG_ABBR.get(t, t[:8])
                if abbr not in all_tags:
                    all_tags.append(abbr)
        tags_str = ",".join(all_tags[:3])

        # Mark new replies
        n = new_counts.get(domain, 0)
        status_display = status.replace("_", " ")
        if n:
            status_display = f"*{status_display}"

        lines.append(_row(state.company_name[:_W_COMPANY], status_display, day_str, tags_str))
        if i < len(sorted_states) - 1:
            lines.append(sep_m)

    lines.append(sep_bot)
    print("\n" + "\n".join(lines) + "\n")


def _reprocess_existing(
    states: dict[str, CompanyState],
    api_key: str | None,
    *,
    verbose: bool = False,
    dry_run: bool = False,
) -> int:
    """Re-classify replies whose tags are a subset of {HUMAN_REVIEW, AUTO_ACKNOWLEDGE}.

    These are the two tags that improved regex patterns are most likely to supersede.
    Returns the number of replies whose tags changed.
    """
    _REPROCESS_TAGS = {"HUMAN_REVIEW", "AUTO_ACKNOWLEDGE"}
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
            if new_result.tags != old_tags:
                if verbose or dry_run:
                    print(
                        f"  [reprocess] {state.company_name} ({domain}): "
                        f"{old_tags} → {new_result.tags}"
                        f"{'  [dry-run]' if dry_run else ''}"
                    )
                if not dry_run:
                    reply.tags = new_result.tags
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

    Fetches the full email body from Gmail for each reply so the LLM has complete
    context (snippets are truncated and cause confused drafts).

    Returns the number of replies updated.
    """
    from reply_monitor.fetcher import _extract_body

    updated = 0
    for domain, state in states.items():
        for reply in state.replies:
            if reply.suggested_reply:
                continue  # already has a draft
            if not any(t in _ACTION_DRAFT_TAGS for t in reply.tags):
                continue
            # Fetch full body from Gmail — fall back to snippet if fetch fails
            body = reply.snippet
            if service and reply.gmail_message_id:
                try:
                    msg = service.users().messages().get(
                        userId="me", id=reply.gmail_message_id, format="full"
                    ).execute()
                    full_body = _extract_body(msg.get("payload", {}))
                    if full_body:
                        body = full_body
                except Exception as exc:
                    if verbose:
                        print(f"  [backfill] warning: could not fetch body for {reply.gmail_message_id}: {exc}")

            draft = generate_reply_draft(body, reply.tags, state.company_name, api_key=api_key)
            if draft:
                reply.suggested_reply = draft
                reply.reply_review_status = "pending"
                updated += 1
                if verbose:
                    print(f"  [backfill] {state.company_name} ({domain}): draft generated for {reply.tags}")
    return updated


def _status_priority(status: str) -> int:
    return {
        "OVERDUE": 8, "ACTION_REQUIRED": 7, "ADDRESS_NOT_FOUND": 6,
        "BOUNCED": 5, "DENIED": 4, "COMPLETED": 3,
        "EXTENDED": 3, "ACKNOWLEDGED": 2, "PENDING": 1,
    }.get(status, 0)


# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Gmail for GDPR SAR replies")
    parser.add_argument("--account", metavar="EMAIL", default=None,
                        help="Gmail account to monitor (e.g. user@gmail.com)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-message classification details")
    parser.add_argument("--reprocess", action="store_true",
                        help="Re-classify existing HUMAN_REVIEW/AUTO_ACKNOWLEDGE replies with improved patterns")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --reprocess: show changes without saving")
    parser.add_argument("--draft-backfill", action="store_true",
                        help="Generate suggested_reply drafts for existing action-tagged replies that have none")
    return parser.parse_args()


if __name__ == "__main__":
    main()
