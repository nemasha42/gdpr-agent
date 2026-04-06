"""Company detail blueprint — single-company view with SP table and graph."""

from __future__ import annotations

import json

from flask import Blueprint, abort, current_app, render_template
from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import Company, Edge, FetchLog, IndexConstituent, get_session
from gdpr_universe.graph_builder import build_neighborhood_graph

bp = Blueprint("company", __name__)


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


@bp.route("/company/<domain>")
def detail(domain: str):
    """Company detail page with SP table and neighborhood graph."""
    engine = _get_engine()

    # 1. Load company
    with get_session(engine) as session:
        company = session.query(Company).filter(Company.domain == domain).first()
        if company is None:
            abort(404)
        session.expunge(company)

        # 2. Load index constituents
        indices = session.query(IndexConstituent).filter(
            IndexConstituent.domain == domain
        ).all()
        for idx in indices:
            session.expunge(idx)

        # 3. Query child SPs via edges
        edge_rows = (
            session.query(Edge, Company)
            .join(Company, Edge.child_domain == Company.domain)
            .filter(Edge.parent_domain == domain)
            .all()
        )
        sps = []
        for edge, sp_company in edge_rows:
            purposes = []
            if edge.purposes:
                try:
                    purposes = json.loads(edge.purposes)
                except (json.JSONDecodeError, TypeError):
                    purposes = [edge.purposes] if edge.purposes else []
            sps.append({
                "domain": sp_company.domain,
                "company_name": sp_company.company_name or sp_company.domain,
                "hq_country_code": sp_company.hq_country_code or "",
                "service_category": sp_company.service_category or "",
                "purposes": purposes,
                "transfer_basis": edge.transfer_basis or "",
            })

        # 4. Latest FetchLog entry
        latest_fetch = (
            session.query(FetchLog)
            .filter(FetchLog.domain == domain)
            .order_by(FetchLog.id.desc())
            .first()
        )
        if latest_fetch is not None:
            session.expunge(latest_fetch)

    # 5. Neighborhood graph
    graph_data = build_neighborhood_graph(engine, domain, hops=2)

    # 6. Render
    return render_template(
        "company.html",
        active_tab="",
        company=company,
        indices=indices,
        sps=sps,
        latest_fetch=latest_fetch,
        graph_json=graph_data,
    )
