"""Crawl blueprint — trigger and monitor background crawl waves."""

from __future__ import annotations

import threading

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.crawl_scheduler import collect_domains_for_wave, run_wave
from gdpr_universe.db import FetchLog, get_session

bp = Blueprint("crawl", __name__)

_crawl_state = {"running": False, "current": 0, "total": 0, "domain": "", "wave": None}

# Max wave depth to show in the UI
_MAX_WAVE = 6


# ── GET /crawl — render crawl management page ───────────────────


@bp.route("/crawl")
def crawl_page():
    """Render the crawl management page with wave summaries."""
    engine: Engine = current_app.config["DB_ENGINE"]

    waves = []
    for w in range(_MAX_WAVE + 1):
        pending = collect_domains_for_wave(engine, w)

        # Count existing fetch results for this wave's domain pool
        with get_session(engine) as session:
            if w == 0:
                # Seeds
                domain_sql = text(
                    "SELECT c.domain FROM companies c WHERE c.is_seed = 1"
                )
            else:
                # Non-seed children at depth < w
                domain_sql = text(
                    "SELECT DISTINCT e.child_domain "
                    "FROM edges e "
                    "JOIN companies c ON e.child_domain = c.domain "
                    "WHERE e.depth < :wave AND c.is_seed = 0"
                )

            if w == 0:
                all_domains = [r[0] for r in session.execute(domain_sql).fetchall()]
            else:
                all_domains = [r[0] for r in session.execute(domain_sql, {"wave": w}).fetchall()]

            # Count statuses from latest fetch_log per domain
            ok_count = 0
            nf_count = 0
            err_count = 0
            for domain in all_domains:
                latest = session.execute(
                    text(
                        "SELECT fetch_status FROM fetch_log "
                        "WHERE domain = :d ORDER BY id DESC LIMIT 1"
                    ),
                    {"d": domain},
                ).fetchone()
                if latest is None:
                    continue
                if latest[0] == "ok":
                    ok_count += 1
                elif latest[0] == "not_found":
                    nf_count += 1
                else:
                    err_count += 1

        waves.append({
            "wave": w,
            "pending": len(pending),
            "ok": ok_count,
            "not_found": nf_count,
            "errors": err_count,
        })

    # Recent fetch log entries
    with get_session(engine) as session:
        recent_rows = session.execute(
            text(
                "SELECT domain, fetch_status, sp_count, source_url, fetched_at "
                "FROM fetch_log ORDER BY id DESC LIMIT 30"
            )
        ).fetchall()
    recent_fetches = [
        {
            "domain": r[0],
            "fetch_status": r[1],
            "sp_count": r[2],
            "source_url": r[3],
            "fetched_at": str(r[4])[:16] if r[4] else "",
        }
        for r in recent_rows
    ]

    return render_template(
        "crawl.html",
        active_tab="crawl",
        waves=waves,
        recent_fetches=recent_fetches,
        crawl_running=_crawl_state["running"],
    )


# ── POST /crawl — kick off a crawl wave ─────────────────────────


@bp.route("/crawl", methods=["POST"])
def start_crawl():
    engine: Engine = current_app.config["DB_ENGINE"]

    body = request.get_json(silent=True) or {}
    wave = int(body.get("wave", 0))
    max_llm = int(body.get("max_llm", 500))

    if _crawl_state["running"]:
        return jsonify({"error": "Crawl already running"}), 409

    domains = collect_domains_for_wave(engine, wave)
    if not domains:
        return jsonify({"message": "No domains to fetch for this wave"})

    def _progress(current: int, total: int, domain: str):
        _crawl_state["current"] = current
        _crawl_state["total"] = total
        _crawl_state["domain"] = domain

    def _run():
        _crawl_state.update(running=True, current=0, total=len(domains), domain="", wave=wave)
        try:
            run_wave(engine, wave, max_llm=max_llm, progress_callback=_progress)
        finally:
            _crawl_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()

    return jsonify({"message": "Crawl started", "wave": wave, "domains": len(domains)})


# ── GET /crawl/status — current crawl state + totals ────────────


@bp.route("/crawl/status")
def crawl_status():
    engine: Engine = current_app.config["DB_ENGINE"]

    with get_session(engine) as session:
        total_fetched = session.query(FetchLog).filter(FetchLog.fetch_status == "ok").count()
        total_errors = session.query(FetchLog).filter(FetchLog.fetch_status == "error").count()

    return jsonify(
        {
            "running": _crawl_state["running"],
            "current": _crawl_state["current"],
            "total": _crawl_state["total"],
            "domain": _crawl_state["domain"],
            "wave": _crawl_state["wave"],
            "total_fetched": total_fetched,
            "total_errors": total_errors,
        }
    )
