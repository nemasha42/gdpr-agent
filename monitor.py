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
from reply_monitor.models import CompanyState
from reply_monitor.state_manager import (
    _ACTION_TAGS,
    compute_status,
    days_remaining,
    deadline_from_sent,
    load_state,
    save_state,
)

from dashboard.services.monitor_runner import (
    auto_download_data_links,
    run_sar_monitor,
    run_sp_monitor,
    _reprocess_existing,
    _backfill_reply_drafts,
)

_STATE_PATH = Path(__file__).parent / "user_data" / "reply_state.json"

# Maximum total address attempts before marking ADDRESS_NOT_FOUND
MAX_ADDRESS_ATTEMPTS = 3

_W_COMPANY = 18
_W_STATUS = 16
_W_DAY = 8
_W_TAGS = 16
_INNER = (_W_COMPANY + 2) + 1 + (_W_STATUS + 2) + 1 + (_W_DAY + 2) + 1 + (_W_TAGS + 2)

# Short tag abbreviations for the summary table
_TAG_ABBR: dict[str, str] = {
    "AUTO_ACKNOWLEDGE": "ACK",
    "OUT_OF_OFFICE": "OOO",
    "BOUNCE_PERMANENT": "BOUNCE",
    "BOUNCE_TEMPORARY": "BOUNCE_TMP",
    "CONFIRMATION_REQUIRED": "CONFIRM",
    "IDENTITY_REQUIRED": "ID_REQ",
    "MORE_INFO_REQUIRED": "MORE_INFO",
    "WRONG_CHANNEL": "WRONG_CH",
    "REQUEST_ACCEPTED": "ACCEPTED",
    "EXTENDED": "EXTENDED",
    "IN_PROGRESS": "IN_PROG",
    "DATA_PROVIDED_LINK": "DATA_LINK",
    "DATA_PROVIDED_ATTACHMENT": "DATA_ATT",
    "DATA_PROVIDED_INLINE": "DATA_INL",
    "DATA_PROVIDED_PORTAL": "DATA_PORT",
    "REQUEST_DENIED": "DENIED",
    "NO_DATA_HELD": "NO_DATA",
    "NOT_GDPR_APPLICABLE": "NOT_GDPR",
    "FULFILLED_DELETION": "DELETED",
    "HUMAN_REVIEW": "REVIEW",
    "YOUR_REPLY": "YOU",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = _parse_args()

    if not (Path(__file__).parent / "credentials.json").exists():
        print("credentials.json not found. Run python setup.py first.")
        sys.exit(1)

    from dashboard.user_model import user_data_dir

    # For --reprocess and --draft-backfill we need the Gmail service directly
    # because these modes need a pre-authenticated service before calling helpers.
    if args.reprocess or args.draft_backfill:
        print("Connecting to Gmail...")
        service, email = get_gmail_service(email_hint=args.account)
        data_dir = user_data_dir(email)
        data_dir.mkdir(parents=True, exist_ok=True)
        print(f"Monitoring: {email}\n")

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        state_path = data_dir / "reply_state.json"
        sp_state_path = data_dir / "subprocessor_reply_state.json"

        states = load_state(email, path=state_path)
        sp_states = load_state(email, path=sp_state_path)

        if args.reprocess:
            all_states = {**states, **{f"sp:{k}": v for k, v in sp_states.items()}}
            n_changed = _reprocess_existing(
                all_states, api_key, verbose=args.verbose, dry_run=args.dry_run
            )
            print(f"[reprocess] {n_changed} reply(ies) reclassified")
            if n_changed and not args.dry_run:
                sar_states = {
                    k: v for k, v in all_states.items() if not k.startswith("sp:")
                }
                sp_states2 = {
                    k[3:]: v for k, v in all_states.items() if k.startswith("sp:")
                }
                save_state(email, sar_states, path=state_path)
                save_state(email, sp_states2, path=sp_state_path)
            if args.dry_run:
                print("[reprocess] dry-run: no changes saved")
            return

        if args.draft_backfill:
            n_sar = _backfill_reply_drafts(states, api_key, service, verbose=args.verbose)
            n_sp = _backfill_reply_drafts(sp_states, api_key, service, verbose=args.verbose)
            print(f"[draft-backfill] {n_sar} SAR + {n_sp} SP reply draft(s) generated")
            if n_sar:
                save_state(email, states, path=state_path)
            if n_sp:
                save_state(email, sp_states, path=sp_state_path)
            cost_tracker.print_cost_summary()
            return

    # Normal monitor run — delegate to monitor_runner.
    # Resolve data_dir early: we need the account email to build per-user paths
    # but run_sar_monitor handles the Gmail connection itself.
    print("Connecting to Gmail...")
    _tokens_dir = Path(__file__).parent / "user_data" / "tokens"
    service_peek, email_peek = get_gmail_service(
        email_hint=args.account, tokens_dir=_tokens_dir
    )
    data_dir = user_data_dir(email_peek)
    data_dir.mkdir(parents=True, exist_ok=True)
    print(f"Monitoring: {email_peek}\n")

    state_path = data_dir / "reply_state.json"
    sp_requests_path = data_dir / "subprocessor_requests.json"
    sp_state_path = data_dir / "subprocessor_reply_state.json"

    service, email, states, new_counts = run_sar_monitor(
        args.account or "",
        state_path=state_path,
        tokens_dir=_tokens_dir,
        data_dir=data_dir,
        sp_requests_path=sp_requests_path,
        verbose=args.verbose,
    )

    # Poll Gmail for subprocessor disclosure request replies
    run_sp_monitor(
        args.account or "",
        state_path=state_path,
        tokens_dir=_tokens_dir,
        data_dir=data_dir,
        sp_requests_path=sp_requests_path,
        sp_state_path=sp_state_path,
        service=service,
        email=email,
        verbose=args.verbose,
    )

    # Re-resolve and auto-send for bounced companies
    if _handle_bounce_retries(email, states, verbose=args.verbose, data_dir=data_dir):
        save_state(email, states, data_dir=data_dir)

    # Print summary table
    _print_summary(email, states, new_counts)
    cost_tracker.print_cost_summary()


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
            print(
                f"  [bounce-retry] {state.company_name}: max attempts ({MAX_ADDRESS_ATTEMPTS}) reached — marking ADDRESS_NOT_FOUND"
            )
            state.address_exhausted = True
            changed = True
            continue

        print(
            f"  [bounce-retry] {state.company_name}: attempt {total_attempts}/{MAX_ADDRESS_ATTEMPTS} failed, re-resolving..."
        )
        new_record = resolver.resolve(
            domain, state.company_name, exclude_emails=tried_emails
        )

        if not new_record:
            print(
                f"  [bounce-retry] {state.company_name}: no alternative address found — ADDRESS_NOT_FOUND"
            )
            state.address_exhausted = True
            changed = True
            continue

        new_email = (
            new_record.contact.privacy_email or new_record.contact.dpo_email or ""
        ).lower()
        if not new_email or new_email in tried_emails:
            print(
                f"  [bounce-retry] {state.company_name}: resolver returned same/no address — ADDRESS_NOT_FOUND"
            )
            state.address_exhausted = True
            changed = True
            continue

        # Compose and send to new address
        letter = compose(new_record)
        tokens_dir = data_dir / "tokens" if data_dir else None
        success, msg_id, thread_id = send_letter(
            letter,
            scan_email=account_email,
            data_dir=data_dir,
            tokens_dir=tokens_dir,
        )

        if not success:
            print(
                f"  [bounce-retry] {state.company_name}: send to {new_email} failed — will retry next run"
            )
            continue

        print(
            f"  [bounce-retry] {state.company_name}: sent to {new_email} (attempt {total_attempts + 1})"
        )

        # Archive current attempt into past_attempts
        state.past_attempts.append(
            {
                "to_email": state.to_email,
                "gmail_thread_id": state.gmail_thread_id,
                "sar_sent_at": state.sar_sent_at,
                "deadline": state.deadline,
                "replies": [r.to_dict() for r in state.replies],
            }
        )

        # Reset active attempt to the new address
        now = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        state.replies = []
        state.to_email = letter.to_email
        state.subject = letter.subject
        state.gmail_thread_id = thread_id
        state.sar_sent_at = now
        state.deadline = deadline_from_sent(now)
        state.last_checked = ""
        changed = True

    return changed


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------


def _print_summary(
    account: str, states: dict[str, CompanyState], new_counts: dict[str, int]
) -> None:
    def _row(company: str, status: str, day: str, tags: str) -> str:
        return (
            f"│  {company[:_W_COMPANY]:<{_W_COMPANY}}"
            f"│  {status[:_W_STATUS]:<{_W_STATUS}}"
            f"│  {day[:_W_DAY]:<{_W_DAY}}"
            f"│  {tags[:_W_TAGS]:<{_W_TAGS}}│"
        )

    sep_top = f"╔{'═' * _INNER}╗"
    title = f"║  {'REPLY MONITOR — ' + account:<{_INNER - 2}}║"
    sep_h = (
        f"╠{'═' * (_W_COMPANY + 2)}"
        f"╦{'═' * (_W_STATUS + 2)}"
        f"╦{'═' * (_W_DAY + 2)}"
        f"╦{'═' * (_W_TAGS + 2)}╣"
    )
    sep_m = (
        f"├{'─' * (_W_COMPANY + 2)}"
        f"┼{'─' * (_W_STATUS + 2)}"
        f"┼{'─' * (_W_DAY + 2)}"
        f"┼{'─' * (_W_TAGS + 2)}┤"
    )
    sep_bot = f"└{'─' * _INNER}┘"

    lines = [
        sep_top,
        title,
        sep_h,
        _row("Company", "Status", "Day", "Tags"),
        sep_h.replace("╦", "╬").replace("╣", "╣"),
    ]

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

        lines.append(
            _row(state.company_name[:_W_COMPANY], status_display, day_str, tags_str)
        )
        if i < len(sorted_states) - 1:
            lines.append(sep_m)

    lines.append(sep_bot)
    print("\n" + "\n".join(lines) + "\n")


def _status_priority(status: str) -> int:
    return {
        "OVERDUE": 8,
        "ACTION_REQUIRED": 7,
        "ADDRESS_NOT_FOUND": 6,
        "BOUNCED": 5,
        "DENIED": 4,
        "COMPLETED": 3,
        "EXTENDED": 3,
        "ACKNOWLEDGED": 2,
        "PENDING": 1,
    }.get(status, 0)


# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor Gmail for GDPR SAR replies")
    parser.add_argument(
        "--account",
        metavar="EMAIL",
        default=None,
        help="Gmail account to monitor (e.g. user@gmail.com)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-message classification details",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Re-classify existing HUMAN_REVIEW/AUTO_ACKNOWLEDGE replies with improved patterns",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --reprocess: show changes without saving",
    )
    parser.add_argument(
        "--draft-backfill",
        action="store_true",
        help="Generate suggested_reply drafts for existing action-tagged replies that have none",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
