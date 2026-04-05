"""Contagion / Blast Radius blueprint — shows upstream impact if a domain is compromised."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, render_template, request
from sqlalchemy.engine import Engine

from gdpr_universe.db import Company, get_session
from gdpr_universe.graph_queries import blast_radius

bp = Blueprint("contagion", __name__)


# ── Inline country-code inference (mirrors graph.py) ──

_CCTLD_MAP = {
    "uk": "GB", "de": "DE", "fr": "FR", "nl": "NL", "be": "BE", "it": "IT",
    "es": "ES", "pt": "PT", "at": "AT", "ch": "CH", "se": "SE", "no": "NO",
    "dk": "DK", "fi": "FI", "ie": "IE", "pl": "PL", "cz": "CZ", "ro": "RO",
    "hu": "HU", "bg": "BG", "hr": "HR", "sk": "SK", "si": "SI", "lt": "LT",
    "lv": "LV", "ee": "EE", "gr": "GR", "cy": "CY", "mt": "MT", "lu": "LU",
    "us": "US", "ca": "CA", "au": "AU", "nz": "NZ", "jp": "JP", "kr": "KR",
    "in": "IN", "br": "BR", "mx": "MX", "cn": "CN", "ru": "RU", "za": "ZA",
    "sg": "SG", "hk": "HK", "tw": "TW", "il": "IL", "ar": "AR", "co": "CO",
    "cl": "CL", "ae": "AE", "th": "TH", "ph": "PH", "my": "MY", "id": "ID",
    "vn": "VN", "ua": "UA", "tr": "TR", "ng": "NG", "ke": "KE", "eg": "EG",
    "is": "IS",
}


def _infer_country_code(domain: str) -> str:
    """Infer country code from domain TLD."""
    parts = domain.rsplit(".", 1)
    if len(parts) < 2:
        return ""
    tld = parts[-1].lower()
    return _CCTLD_MAP.get(tld, "")


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


@bp.route("/contagion/<domain>")
def contagion(domain: str):
    """Blast radius page — shows all entities affected if domain is compromised."""
    engine = _get_engine()

    # 1. Load target company
    with get_session(engine) as session:
        company = session.query(Company).filter(Company.domain == domain).first()
        if company is None:
            abort(404)
        session.expunge(company)

    # 2. Parse max_hops query param (default 5, clamp 1-10)
    try:
        max_hops = int(request.args.get("max_hops", "5"))
    except (ValueError, TypeError):
        max_hops = 5
    max_hops = max(1, min(10, max_hops))

    # 3. Run blast radius query
    affected_raw = blast_radius(engine, domain, max_hops=max_hops)

    # 4. Enrich each affected entity with Company details
    affected_domains = [r["domain"] for r in affected_raw]
    hops_map = {r["domain"]: r["hops"] for r in affected_raw}

    company_map: dict[str, Company] = {}
    if affected_domains:
        with get_session(engine) as session:
            companies = (
                session.query(Company)
                .filter(Company.domain.in_(affected_domains))
                .all()
            )
            for c in companies:
                session.expunge(c)
                company_map[c.domain] = c

    entities = []
    for d in affected_domains:
        c = company_map.get(d)
        country = ""
        if c is not None:
            country = c.hq_country_code or ""
        if not country:
            country = _infer_country_code(d)
        entities.append({
            "domain": d,
            "company_name": c.company_name if c else d,
            "hq_country_code": country,
            "is_seed": bool(c.is_seed) if c else False,
            "hops": hops_map[d],
        })

    # 5. Compute stats
    total_affected = len(entities)
    seed_count = sum(1 for e in entities if e["is_seed"])
    countries = {e["hq_country_code"] for e in entities if e["hq_country_code"]}
    country_count = len(countries)

    # 6. Top countries by count
    country_counts: dict[str, int] = {}
    for e in entities:
        cc = e["hq_country_code"]
        if cc:
            country_counts[cc] = country_counts.get(cc, 0) + 1
    top_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)

    # 7. Render
    return render_template(
        "contagion.html",
        active_tab="",
        company=company,
        domain=domain,
        max_hops=max_hops,
        entities=entities,
        total_affected=total_affected,
        seed_count=seed_count,
        country_count=country_count,
        top_countries=top_countries,
    )
