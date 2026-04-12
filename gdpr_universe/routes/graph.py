"""Graph API blueprint — JSON endpoint for D3 neighborhood graph."""

from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.engine import Engine

from gdpr_universe.graph_queries import ADEQUATE_COUNTRIES, neighborhood

bp = Blueprint("graph", __name__)


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


# ── Inline risk helpers (mirrors dashboard/services/jurisdiction.py) ──

# Countries with known safeguards (SCCs, BCRs common) but no adequacy decision
_SAFEGUARDED_COUNTRIES = {"IN", "BR", "PH", "SG", "MY", "TH", "MX", "CO", "CL", "AE"}

# ccTLD → country code mapping for fallback inference
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


def _assess_risk(country_code: str) -> str:
    """Return risk level: adequate, safeguarded, risky, or unknown."""
    if not country_code:
        return "unknown"
    code = country_code.upper()
    if code in ADEQUATE_COUNTRIES:
        return "adequate"
    if code in _SAFEGUARDED_COUNTRIES:
        return "safeguarded"
    return "risky"


@bp.route("/api/graph")
def graph_api():
    """Return D3-ready JSON for a neighborhood graph."""
    engine = _get_engine()

    domain = request.args.get("domain", "").strip()
    if not domain:
        return jsonify({"error": "domain parameter is required"}), 400

    hops = request.args.get("hops", "2")
    try:
        hops = int(hops)
    except (ValueError, TypeError):
        hops = 2
    hops = max(1, min(4, hops))

    raw_nodes, raw_edges = neighborhood(engine, domain, hops=hops)

    # Enrich nodes with risk assessment
    nodes = []
    for n in raw_nodes:
        country = n.get("hq_country_code") or ""
        if not country:
            country = _infer_country_code(n["domain"])
        risk = _assess_risk(country)
        nodes.append({
            "id": n["domain"],
            "label": n.get("company_name") or n["domain"],
            "country": country,
            "is_seed": n.get("is_seed", False),
            "service_category": n.get("service_category", ""),
            "risk": risk,
            "is_center": n["domain"] == domain,
        })

    # Transform edges
    edges = []
    for e in raw_edges:
        purposes = []
        if e.get("purposes"):
            try:
                purposes = json.loads(e["purposes"])
            except (json.JSONDecodeError, TypeError):
                purposes = [e["purposes"]] if e["purposes"] else []
        edges.append({
            "source": e["parent_domain"],
            "target": e["child_domain"],
            "depth": e.get("depth"),
            "purposes": purposes,
            "transfer_basis": e.get("transfer_basis", ""),
        })

    return jsonify({"nodes": nodes, "edges": edges, "center": domain})
