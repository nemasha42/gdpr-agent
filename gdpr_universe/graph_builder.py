"""Build D3-compatible graph JSON from SQLite data."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import get_session
from gdpr_universe.graph_queries import ADEQUATE_COUNTRIES, neighborhood

# Countries with some safeguards but not EU-adequate
_SAFEGUARDED_COUNTRIES = {"IN", "BR", "PH", "SG", "MY", "TH", "MX", "CO", "CL", "AE"}

_EDGE_COLOR = "#484f58"


def _infer_country_from_domain(domain: str) -> str | None:
    """Infer country code from ccTLD (e.g. stripe.co.uk -> GB)."""
    _CCTLD_MAP = {
        "uk": "GB", "de": "DE", "fr": "FR", "nl": "NL", "es": "ES",
        "it": "IT", "pl": "PL", "se": "SE", "no": "NO", "dk": "DK",
        "fi": "FI", "be": "BE", "at": "AT", "ch": "CH", "pt": "PT",
        "ie": "IE", "gr": "GR", "cz": "CZ", "hu": "HU", "ro": "RO",
        "sk": "SK", "bg": "BG", "hr": "HR", "si": "SI", "lt": "LT",
        "lv": "LV", "ee": "EE", "mt": "MT", "lu": "LU", "cy": "CY",
        "is": "IS", "li": "LI", "no": "NO",
        "jp": "JP", "au": "AU", "ca": "CA", "br": "BR", "in": "IN",
        "sg": "SG", "nz": "NZ", "kr": "KR", "cn": "CN", "ru": "RU",
        "ph": "PH", "my": "MY", "th": "TH", "mx": "MX", "co": "CO",
        "cl": "CL", "ae": "AE", "za": "ZA", "ar": "AR",
    }
    parts = domain.rstrip(".").lower().split(".")
    if len(parts) >= 2:
        tld = parts[-1]
        # co.uk, com.au style — check second-to-last
        if tld in ("uk", "au", "nz") and len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net"):
            return _CCTLD_MAP.get(tld)
        # Skip generic TLDs
        if tld not in ("com", "net", "org", "io", "app", "co", "ai", "dev", "cloud"):
            return _CCTLD_MAP.get(tld)
    return None


def _assess_risk(country_code: str | None, domain: str | None = None) -> str:
    """Return 'adequate' | 'safeguarded' | 'risky' | 'unknown'."""
    code = country_code
    if not code and domain:
        code = _infer_country_from_domain(domain)
    if not code:
        return "unknown"
    if code in ADEQUATE_COUNTRIES:
        return "adequate"
    if code in _SAFEGUARDED_COUNTRIES:
        return "safeguarded"
    return "risky"


def _effective_country(country_code: str | None, domain: str | None = None) -> str | None:
    """Return stored country code or infer from ccTLD."""
    if country_code:
        return country_code
    if domain:
        return _infer_country_from_domain(domain)
    return None


def _parse_purposes(raw: str | None) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def build_full_graph(engine: Engine) -> dict:
    """Build complete graph of all seed companies and their subprocessors.

    Returns {"nodes": [...], "edges": [...], "stats": {...}}.
    """
    with get_session(engine) as session:
        # All seed companies
        seed_rows = session.execute(
            text(
                "SELECT domain, company_name, hq_country_code, service_category "
                "FROM companies WHERE is_seed = 1"
            )
        ).fetchall()

        # All edges with child company info
        edge_rows = session.execute(
            text(
                "SELECT e.parent_domain, e.child_domain, e.depth, e.purposes, "
                "       e.transfer_basis, c.company_name, c.hq_country_code, "
                "       c.is_seed, c.service_category "
                "FROM edges e "
                "JOIN companies c ON e.child_domain = c.domain"
            )
        ).fetchall()

    # Build sharing_count map: child_domain -> count of distinct parents
    sharing_count_map: dict[str, int] = {}
    for row in edge_rows:
        child = row[1]
        sharing_count_map[child] = sharing_count_map.get(child, 0) + 1

    # Build seed nodes
    seed_domains = set()
    nodes_by_domain: dict[str, dict] = {}

    for row in seed_rows:
        domain, name, country_code, service_category = row
        seed_domains.add(domain)
        country = _effective_country(country_code, domain)
        nodes_by_domain[domain] = {
            "id": domain,
            "domain": domain,
            "type": "seed",
            "label": name or domain,
            "country": country,
            "risk": _assess_risk(country_code, domain),
            "is_center": False,
            "is_seed": True,
            "service_category": service_category or "",
            "sharing_count": sharing_count_map.get(domain, 0),
            "depth": 0,
        }

    # Build SP nodes (deduped by domain) and edges
    edges_out: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    for row in edge_rows:
        parent_domain, child_domain, depth, purposes_raw, transfer_basis, \
            child_name, child_country_code, child_is_seed, child_service_category = row

        # SP node (only if not already added as seed)
        if child_domain not in nodes_by_domain:
            child_country = _effective_country(child_country_code, child_domain)
            nodes_by_domain[child_domain] = {
                "id": child_domain,
                "domain": child_domain,
                "type": "subprocessor",
                "label": child_name or child_domain,
                "country": child_country,
                "risk": _assess_risk(child_country_code, child_domain),
                "is_center": False,
                "is_seed": bool(child_is_seed),
                "service_category": child_service_category or "",
                "sharing_count": sharing_count_map.get(child_domain, 0),
                "depth": depth if depth is not None else 1,
            }

        # Edges (dedup)
        edge_key = (parent_domain, child_domain)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            edges_out.append({
                "source": parent_domain,
                "target": child_domain,
                "type": "data_flow",
                "purposes": _parse_purposes(purposes_raw),
                "transfer_basis": transfer_basis or "",
                "confirmed": False,
                "depth": depth if depth is not None else 1,
                "color": _EDGE_COLOR,
            })

    nodes_out = list(nodes_by_domain.values())

    # Stats
    sp_nodes = [n for n in nodes_out if not n["is_seed"]]
    countries_reached = {n["country"] for n in nodes_out if n["country"]}
    non_adequate = [n for n in nodes_out if n["risk"] in ("risky", "safeguarded")]

    transfer_basis_breakdown: dict[str, int] = {}
    for e in edges_out:
        tb = e["transfer_basis"] or "unknown"
        transfer_basis_breakdown[tb] = transfer_basis_breakdown.get(tb, 0) + 1

    stats = {
        "total_seeds": len(seed_rows),
        "total_subprocessors": len(sp_nodes),
        "countries_reached": len(countries_reached),
        "non_adequate_count": len(non_adequate),
        "transfer_basis_breakdown": transfer_basis_breakdown,
    }

    return {"nodes": nodes_out, "edges": edges_out, "stats": stats}


def build_neighborhood_graph(engine: Engine, domain: str, hops: int = 2) -> dict:
    """Build neighborhood graph within N hops of a domain.

    Returns {"nodes": [...], "edges": [...], "stats": {...}}.
    """
    raw_nodes, raw_edges = neighborhood(engine, domain, hops=hops)

    if not raw_nodes:
        return {"nodes": [], "edges": [], "stats": {
            "total_seeds": 0, "total_subprocessors": 0,
            "countries_reached": 0, "non_adequate_count": 0,
            "transfer_basis_breakdown": {},
        }}

    # Compute sharing_count from the subgraph edges
    sharing_count_map: dict[str, int] = {}
    for e in raw_edges:
        child = e["child_domain"]
        sharing_count_map[child] = sharing_count_map.get(child, 0) + 1

    nodes_out: list[dict] = []
    for n in raw_nodes:
        node_domain = n["domain"]
        country_code = n.get("hq_country_code")
        country = _effective_country(country_code, node_domain)
        is_seed = n.get("is_seed", False)
        nodes_out.append({
            "id": node_domain,
            "domain": node_domain,
            "type": "seed" if is_seed else "subprocessor",
            "label": n.get("company_name") or node_domain,
            "country": country,
            "risk": _assess_risk(country_code, node_domain),
            "is_center": node_domain == domain,
            "is_seed": is_seed,
            "service_category": n.get("service_category") or "",
            "sharing_count": sharing_count_map.get(node_domain, 0),
            "depth": 0 if node_domain == domain else 1,
        })

    edges_out: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()
    for e in raw_edges:
        edge_key = (e["parent_domain"], e["child_domain"])
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            depth = e.get("depth")
            edges_out.append({
                "source": e["parent_domain"],
                "target": e["child_domain"],
                "type": "data_flow",
                "purposes": _parse_purposes(e.get("purposes")),
                "transfer_basis": e.get("transfer_basis") or "",
                "confirmed": False,
                "depth": depth if depth is not None else 1,
                "color": _EDGE_COLOR,
            })

    # Stats
    sp_nodes = [n for n in nodes_out if not n["is_seed"]]
    countries_reached = {n["country"] for n in nodes_out if n["country"]}
    non_adequate = [n for n in nodes_out if n["risk"] in ("risky", "safeguarded")]

    transfer_basis_breakdown: dict[str, int] = {}
    for e in edges_out:
        tb = e["transfer_basis"] or "unknown"
        transfer_basis_breakdown[tb] = transfer_basis_breakdown.get(tb, 0) + 1

    seed_count = sum(1 for n in nodes_out if n["is_seed"])

    stats = {
        "total_seeds": seed_count,
        "total_subprocessors": len(sp_nodes),
        "countries_reached": len(countries_reached),
        "non_adequate_count": len(non_adequate),
        "transfer_basis_breakdown": transfer_basis_breakdown,
    }

    return {"nodes": nodes_out, "edges": edges_out, "stats": stats}
