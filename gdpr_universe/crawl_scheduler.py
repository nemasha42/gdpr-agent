"""Wave-based crawl scheduler for the GDPR universe graph.

Collects domains due for (re-)fetching and orchestrates calls to the
subprocessor fetcher, storing results via the adapter layer.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.engine import Engine

from contact_resolver.models import SubprocessorRecord
from gdpr_universe.db import Company, Edge, FetchLog, get_session


# ── Domain collection ────────────────────────────────────────────


def collect_domains_for_wave(
    engine: Engine,
    wave: int,
    *,
    ttl_days: int = 30,
) -> list[str]:
    """Return sorted list of domains to fetch for *wave*.

    Wave 0: all seed companies not yet successfully fetched within TTL.
    Wave 1+: non-seed child_domains from edges at depth < wave, not yet fetched.
    """
    with get_session(engine) as session:
        if wave == 0:
            candidates = (
                session.query(Company.domain)
                .filter(Company.is_seed.is_(True))
                .all()
            )
        else:
            candidates = (
                session.query(Edge.child_domain)
                .filter(Edge.depth < wave)
                .join(Company, Company.domain == Edge.child_domain)
                .filter(Company.is_seed.is_(False))
                .distinct()
                .all()
            )

        candidate_domains = sorted({row[0] for row in candidates})

        # Filter by skip logic (strip tzinfo — SQLite stores naive datetimes)
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ttl_days)
        result = []
        for domain in candidate_domains:
            latest_log = (
                session.query(FetchLog)
                .filter(FetchLog.domain == domain)
                .order_by(FetchLog.fetched_at.desc())
                .first()
            )
            if latest_log is None:
                result.append(domain)
            elif latest_log.fetch_status == "error":
                result.append(domain)
            elif latest_log.fetch_status == "not_found":
                continue
            elif latest_log.fetch_status == "ok":
                if latest_log.fetched_at < cutoff:
                    result.append(domain)
                # else fresh — skip
            else:
                result.append(domain)

        return result


# ── Fetch helper (isolated for mocking) ──────────────────────────


def _do_fetch(domain: str, *, api_key: str | None = None) -> SubprocessorRecord:
    """Fetch subprocessors for a single domain.

    Isolated so tests can mock this without touching the real fetcher.
    """
    from contact_resolver.subprocessor_fetcher import fetch_subprocessors

    return fetch_subprocessors(
        company_name=domain.split(".")[0].title(),
        domain=domain,
        api_key=api_key,
    )


# ── Wave execution ───────────────────────────────────────────────


def run_wave(
    engine: Engine,
    wave: int,
    *,
    max_llm: int = 500,
    delay: int = 2,
    api_key: str | None = None,
    ttl_days: int = 30,
    progress_callback=None,
) -> dict:
    """Execute a crawl wave and return stats dict."""
    from gdpr_universe.adapters import store_fetch_result

    domains = collect_domains_for_wave(engine, wave, ttl_days=ttl_days)
    total = len(domains)
    capped = domains[:max_llm]

    stats = {"fetched": 0, "errors": 0, "skipped": total - len(capped), "total": total}

    for i, domain in enumerate(capped):
        if progress_callback:
            progress_callback(i + 1, total, domain)

        try:
            record = _do_fetch(domain, api_key=api_key)
            store_fetch_result(engine, domain, record, depth=wave)
            if record.fetch_status == "error":
                stats["errors"] += 1
            else:
                stats["fetched"] += 1
        except Exception:
            stats["errors"] += 1

        if delay and i < len(capped) - 1:
            time.sleep(delay)

    return stats


# ── CLI ──────────────────────────────────────────────────────────


def _cli():
    parser = argparse.ArgumentParser(description="GDPR Universe crawl scheduler")
    parser.add_argument("--wave", type=int, default=0, help="Wave number (0=seeds)")
    parser.add_argument("--max-llm", type=int, default=500, help="Max domains to fetch")
    parser.add_argument("--delay", type=int, default=2, help="Seconds between fetches")
    parser.add_argument("--domain", type=str, help="Fetch a single domain only")
    parser.add_argument("--status", action="store_true", help="Show pending domains and exit")
    parser.add_argument("--db", type=str, default="gdpr_universe/data/universe.db", help="SQLite DB path")
    args = parser.parse_args()

    from gdpr_universe.db import get_engine, init_db

    engine = get_engine(args.db)
    init_db(engine)

    if args.status:
        domains = collect_domains_for_wave(engine, args.wave, ttl_days=30)
        print(f"Wave {args.wave}: {len(domains)} domains pending")
        for d in domains:
            print(f"  {d}")
        return

    if args.domain:
        from gdpr_universe.adapters import store_fetch_result

        record = _do_fetch(args.domain, api_key=None)
        store_fetch_result(engine, args.domain, record, depth=args.wave)
        print(f"Fetched {args.domain}: {record.fetch_status} ({len(record.subprocessors)} SPs)")
        return

    def _progress(current, total, domain):
        print(f"[{current}/{total}] {domain}")

    stats = run_wave(
        engine,
        args.wave,
        max_llm=args.max_llm,
        delay=args.delay,
        progress_callback=_progress,
    )
    print(f"Done: {stats}")


if __name__ == "__main__":
    _cli()
