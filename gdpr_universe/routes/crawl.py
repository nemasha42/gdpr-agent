"""Crawl blueprint — trigger and monitor background crawl waves."""

from __future__ import annotations

import threading

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.engine import Engine

from gdpr_universe.crawl_scheduler import collect_domains_for_wave, run_wave
from gdpr_universe.db import FetchLog, get_session

bp = Blueprint("crawl", __name__)

_crawl_state = {"running": False, "current": 0, "total": 0, "domain": "", "wave": None}


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
