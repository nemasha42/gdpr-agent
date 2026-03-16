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

from auth.gmail_oauth import get_gmail_service
from contact_resolver import cost_tracker
from letter_engine.tracker import get_log
from reply_monitor.attachment_handler import handle_attachment
from reply_monitor.classifier import classify
from reply_monitor.fetcher import fetch_replies_for_sar
from reply_monitor.models import CompanyState, ReplyRecord
from reply_monitor.state_manager import (
    compute_status,
    days_remaining,
    deadline_from_sent,
    domain_from_sent_record,
    load_state,
    save_state,
    update_state,
)

_STATE_PATH = Path(__file__).parent / "user_data" / "reply_state.json"

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
    "DATA_PROVIDED_PORTAL":  "DATA_PORT",
    "REQUEST_DENIED":        "DENIED",
    "NO_DATA_HELD":          "NO_DATA",
    "NOT_GDPR_APPLICABLE":   "NOT_GDPR",
    "FULFILLED_DELETION":    "DELETED",
    "HUMAN_REVIEW":          "REVIEW",
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
    service, email = get_gmail_service(email_hint=args.account)
    print(f"Monitoring: {email}\n")

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    sent_log = get_log()
    if not sent_log:
        print("No sent SARs found in user_data/sent_letters.json. Nothing to monitor.")
        return

    # Load existing state
    states = load_state(email, path=_STATE_PATH)

    # Ensure every sent record has a CompanyState entry
    for record in sent_log:
        domain = domain_from_sent_record(record)
        if domain not in states:
            states[domain] = CompanyState(
                domain=domain,
                company_name=record.get("company_name", domain),
                sar_sent_at=record.get("sent_at", ""),
                to_email=record.get("to_email", ""),
                subject=record.get("subject", ""),
                gmail_thread_id=record.get("gmail_thread_id", ""),
                deadline=deadline_from_sent(record.get("sent_at", "")),
            )

    # Deduplicate sent records by domain (process each domain once)
    seen_domains: set[str] = set()
    new_counts: dict[str, int] = {}

    for record in sent_log:
        domain = domain_from_sent_record(record)
        if domain in seen_domains:
            continue
        seen_domains.add(domain)

        state = states[domain]
        existing_ids = {r.gmail_message_id for r in state.replies}

        if args.verbose:
            print(f"Fetching replies for {state.company_name} ({domain})...", end=" ", flush=True)

        new_messages = fetch_replies_for_sar(service, record, existing_ids, user_email=email, verbose=args.verbose)

        if args.verbose:
            print(f"{len(new_messages)} new message(s)")

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            result = classify(msg, api_key=api_key)
            catalog_dict = None
            if msg.get("has_attachment"):
                for part in msg.get("parts", []):
                    cat = handle_attachment(service, msg["id"], part, domain)
                    if cat:
                        catalog_dict = cat.to_dict()
                        break

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
            )
            new_replies.append(reply)

            if args.verbose:
                print(f"  [{state.company_name}] {result.tags} — {msg['snippet'][:60]}")

        new_counts[domain] = len(new_replies)
        if new_replies:
            states[domain] = update_state(state, new_replies)

    save_state(email, states, path=_STATE_PATH)

    # Auto-download data links (Playwright handles Cloudflare-protected sites)
    _auto_download_data_links(email, states, api_key, verbose=args.verbose)

    # Print summary table
    _print_summary(email, states, new_counts)
    cost_tracker.print_cost_summary()


def _auto_download_data_links(
    account: str, states: dict, api_key: str | None, verbose: bool = False
) -> None:
    """Automatically download any DATA_PROVIDED_LINK replies that have a URL but no catalog."""
    from reply_monitor.link_downloader import download_data_link
    from reply_monitor.state_manager import save_state as _save

    needs_save = False
    for domain, state in states.items():
        for reply in state.replies:
            if (
                "DATA_PROVIDED_LINK" in reply.tags
                and reply.extracted.get("data_link")
                and not reply.attachment_catalog
            ):
                url = reply.extracted["data_link"]
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
        _save(account, states, path=_STATE_PATH)


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


def _status_priority(status: str) -> int:
    return {
        "OVERDUE": 8, "ACTION_REQUIRED": 7, "BOUNCED": 6, "DENIED": 5,
        "COMPLETED": 4, "EXTENDED": 3, "ACKNOWLEDGED": 2, "PENDING": 1,
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
    return parser.parse_args()


if __name__ == "__main__":
    main()
