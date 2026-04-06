"""Transform subprocessor records into graph-ready JSON for D3.js."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from dashboard.services.jurisdiction import ADEQUATE_COUNTRIES, assess_risk, infer_country_code

# Purpose category keywords → category name (priority order, highest first)
_PURPOSE_CATEGORIES: list[tuple[str, list[str]]] = [
    ("advertising", ["advertising", "marketing", "ads", "retargeting", "audience"]),
    ("analytics", ["analytics", "tracking", "monitoring", "logging", "observability", "error"]),
    ("payment", ["payment", "billing", "invoice", "subscription", "financial"]),
    ("communication", ["email", "communication", "notification", "messaging", "support", "crm"]),
    ("infrastructure", ["hosting", "infrastructure", "cloud", "storage", "cdn", "dns", "compute"]),
]

# Display labels for service_category values (acronyms, special casing)
CATEGORY_LABELS: dict[str, str] = {
    "ai_ml": "AI/ML",
    "crm": "CRM",
    "hr": "HR",
    "cdn": "CDN",
    "dns": "DNS",
}


def category_label(raw: str) -> str:
    """Return a human-friendly label for a service_category slug."""
    if raw in CATEGORY_LABELS:
        return CATEGORY_LABELS[raw]
    return raw.replace("_", " ").title()


_PURPOSE_COLORS = {
    "advertising": "#fd7e14",
    "analytics": "#6f42c1",
    "payment": "#198754",
    "communication": "#20c997",
    "infrastructure": "#0d6efd",
    "other": "#adb5bd",
}


def _classify_purpose(purposes: list[str]) -> str:
    """Bucket a list of purpose strings into the highest-priority category."""
    text = " ".join(purposes).lower()
    for category, keywords in _PURPOSE_CATEGORIES:
        if any(kw in text for kw in keywords):
            return category
    return "other"


def _edge_thickness(data_category_count: int) -> int:
    if data_category_count <= 1:
        return 2
    if data_category_count <= 3:
        return 3
    return 5


def _add_sp_edges(
    parent_id: str,
    parent_domain: str,
    sp_list: list[dict],
    company_domains: set[str],
    sp_registry: dict[str, dict],
    nodes: list[dict],
    edges: list[dict],
    all_sp_domains: set[str],
    country_set: set[str],
    non_adequate: set[str],
    basis_counter: Counter,
    category_counter: Counter,
    unknown_basis_count_box: list[int],
    *,
    edge_depth: int = 1,
    confirmed_set: set[str] | None = None,
    node_depths: dict[str, int] | None = None,
    service_category_set: set[str] | None = None,
) -> None:
    """Add subprocessor nodes and edges from *parent_id* to its SP list."""
    if confirmed_set is None:
        confirmed_set = set()
    if node_depths is None:
        node_depths = {}
    if service_category_set is None:
        service_category_set = set()

    for sp in sp_list:
        sp_domain = sp.get("domain", "")
        if not sp_domain:
            continue
        sp_name = sp.get("company_name", sp_domain)
        sp_country = sp.get("hq_country_code", "") or infer_country_code(sp_domain)
        sp_country_name = sp.get("hq_country", "")
        sp_purposes = sp.get("purposes", [])
        sp_categories = sp.get("data_categories", [])
        sp_basis = sp.get("transfer_basis", "unknown")
        sp_source_url = sp.get("source_url", "")
        sp_service_category = sp.get("service_category", "")

        if sp_service_category:
            service_category_set.add(sp_service_category)

        risk = assess_risk(sp_country, sp_basis)
        is_confirmed = sp_domain in confirmed_set

        # Stats
        all_sp_domains.add(sp_domain)
        if sp_country:
            country_set.add(sp_country)
        if sp_country and sp_country not in ADEQUATE_COUNTRIES:
            non_adequate.add(sp_domain)
        if sp_basis in ("unknown", "none"):
            unknown_basis_count_box[0] += 1
        basis_counter[sp_basis] += 1
        for cat in sp_categories:
            category_counter[cat] += 1

        # Cross-link: if SP domain matches a SAR company, reuse company node
        if sp_domain in company_domains:
            if sp_domain == parent_domain:
                continue  # skip self-reference
            edge_target = f"company:{sp_domain}"
            # Track min-depth for cross-linked node
            if sp_domain in node_depths:
                node_depths[sp_domain] = min(node_depths[sp_domain], edge_depth)
            else:
                node_depths[sp_domain] = edge_depth
        else:
            # Dedup subprocessor node
            sp_id = f"sp:{sp_domain}"
            edge_target = sp_id
            if sp_domain not in sp_registry:
                sp_node = {
                    "id": sp_id,
                    "type": "subprocessor",
                    "label": sp_name,
                    "domain": sp_domain,
                    "country_code": sp_country,
                    "country": sp_country_name,
                    "risk": risk,
                    "transfer_basis": sp_basis,
                    "sharing_count": 1,
                    "depth": edge_depth,
                    "service_category": sp_service_category,
                    "confirmed": is_confirmed,
                }
                sp_registry[sp_domain] = sp_node
                node_depths[sp_domain] = edge_depth
                nodes.append(sp_node)
            else:
                sp_registry[sp_domain]["sharing_count"] += 1
                # Update min-depth
                cur = node_depths.get(sp_domain, edge_depth)
                node_depths[sp_domain] = min(cur, edge_depth)

        # Parent → Subprocessor edge
        purpose_cat = _classify_purpose(sp_purposes)
        edges.append({
            "source": parent_id,
            "target": edge_target,
            "type": "data_flow",
            "purpose_category": purpose_cat,
            "color": _PURPOSE_COLORS.get(purpose_cat, "#adb5bd"),
            "thickness": _edge_thickness(len(sp_categories)),
            "purposes": sp_purposes,
            "data_categories": sp_categories,
            "transfer_basis": sp_basis,
            "source_url": sp_source_url,
            "confirmed": is_confirmed,
            "depth": edge_depth,
        })


def build_graph_data(rows: list[dict], companies_raw: dict | None = None) -> dict:
    """Transform transfer page rows into {nodes, edges, stats} for D3.js.

    Args:
        rows: list of row dicts from transfers_page(), each with keys:
              domain, company_name, subprocessors (SubprocessorRecord dict or None),
              has_email, request_sent, sp_status, sp_replies
        companies_raw: optional full companies.json dict — when provided, SP domains
              that have their own subprocessor records will be rendered as deeper
              graph layers (SP → sub-SP edges).
    Returns:
        dict with keys: nodes, edges, stats
    """
    nodes: list[dict] = [{"id": "user", "type": "user", "label": "You", "depth": -1}]
    edges: list[dict] = []

    # Track subprocessor dedup: sp_domain → {node_dict, sharing_count}
    sp_registry: dict[str, dict] = {}

    # Track min-depth per domain (for cross-linked nodes)
    node_depths: dict[str, int] = {}

    # Stats accumulators
    all_sp_domains: set[str] = set()
    country_set: set[str] = set()
    non_adequate: set[str] = set()
    basis_counter: Counter = Counter()
    category_counter: Counter = Counter()
    coverage_counter: Counter = Counter()
    unknown_basis_count_box: list[int] = [0]
    service_category_set: set[str] = set()

    # Pre-collect all SAR company domains for cross-link deduplication
    company_domains: set[str] = set()
    for row in rows:
        company_domains.add(row["domain"])

    for row in rows:
        domain = row["domain"]
        company_name = row["company_name"]
        sp_record = row.get("subprocessors")
        sp_list = []
        fetch_status = "pending"

        if sp_record:
            sp_list = sp_record.get("subprocessors", [])
            fetch_status = sp_record.get("fetch_status", "pending")

        coverage_counter[fetch_status] += 1

        # Determine coverage: "mapped" if subprocessors successfully fetched
        coverage = "mapped" if fetch_status == "ok" else "unmapped"

        # Confirmed subprocessors for this company
        confirmed_sps = set(row.get("confirmed_subprocessors", []))

        # Compute staleness from subprocessor fetched_at
        fetched_at = row.get("subprocessors", {}).get("fetched_at", "") if sp_record else ""
        is_stale = False
        if fetched_at:
            try:
                fetched_dt = datetime.fromisoformat(fetched_at)
                if fetched_dt.tzinfo is None:
                    fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - fetched_dt).days
                is_stale = age_days > 60
            except Exception:
                pass

        # Company node — always depth 0
        company_id = f"company:{domain}"
        node_depths[domain] = 0
        nodes.append({
            "id": company_id,
            "type": "company",
            "label": company_name,
            "domain": domain,
            "fetched": sp_record is not None,
            "fetch_status": fetch_status,
            "subprocessor_count": len(sp_list),
            "depth": 0,
            "coverage": coverage,
            "confirmed": False,
            "stale": is_stale,
            "fetched_at": fetched_at,
        })

        # User → Company edge (structural, thin grey)
        edges.append({
            "source": "user",
            "target": company_id,
            "type": "structural",
            "purpose_category": "other",
            "color": "#adb5bd",
            "thickness": 1,
            "confirmed": True,
            "depth": 0,
        })

        # Subprocessor nodes and edges
        _add_sp_edges(
            company_id, domain, sp_list, company_domains, sp_registry,
            nodes, edges, all_sp_domains, country_set, non_adequate,
            basis_counter, category_counter, unknown_basis_count_box,
            edge_depth=1,
            confirmed_set=confirmed_sps,
            node_depths=node_depths,
            service_category_set=service_category_set,
        )

    # Deep SP → sub-SP edges: traverse SP nodes that have their own records
    if companies_raw:
        # Iterate in waves to handle multiple layers
        processed_sp: set[str] = set(company_domains)
        pending_sp = set(sp_registry.keys()) - processed_sp
        wave_depth = 2  # L1 SPs already added above; deeper layers start at 2
        while pending_sp:
            next_wave: set[str] = set()
            for sp_domain in pending_sp:
                processed_sp.add(sp_domain)
                cr = companies_raw.get(sp_domain, {})
                sp_rec = cr.get("subprocessors")
                if not sp_rec or sp_rec.get("fetch_status") != "ok":
                    continue
                sub_list = sp_rec.get("subprocessors", [])
                if not sub_list:
                    continue
                parent_id = f"sp:{sp_domain}"
                if sp_domain in company_domains:
                    parent_id = f"company:{sp_domain}"
                before = len(sp_registry)
                _add_sp_edges(
                    parent_id, sp_domain, sub_list, company_domains, sp_registry,
                    nodes, edges, all_sp_domains, country_set, non_adequate,
                    basis_counter, category_counter, unknown_basis_count_box,
                    edge_depth=wave_depth,
                    node_depths=node_depths,
                    service_category_set=service_category_set,
                )
                # Newly added SP domains become candidates for the next wave
                if len(sp_registry) > before:
                    next_wave |= set(sp_registry.keys()) - processed_sp
            pending_sp = next_wave
            wave_depth += 1

    # Apply min-depth to all nodes
    for node in nodes:
        domain = node.get("domain")
        if domain and domain in node_depths:
            node["depth"] = node_depths[domain]
        # Also update SP nodes in registry (they share the same dict)

    # Build stats
    top_categories = sorted(category_counter.items(), key=lambda x: -x[1])[:10]
    stale_count = sum(1 for n in nodes if n.get("type") == "company" and n.get("stale"))
    stats = {
        "total_companies": len(rows),
        "total_subprocessors": len(all_sp_domains),
        "countries_reached": len(country_set),
        "non_adequate_count": len(non_adequate),
        "unknown_basis_count": unknown_basis_count_box[0],
        "coverage_gaps": sum(1 for r in rows if r.get("subprocessors") is None),
        "transfer_basis_breakdown": dict(basis_counter),
        "top_data_categories": top_categories,
        "coverage_breakdown": dict(coverage_counter),
        "purpose_colors": _PURPOSE_COLORS,
        "service_categories": sorted(service_category_set),
        "service_category_labels": {c: category_label(c) for c in service_category_set},
        "stale_count": stale_count,
    }

    return {"nodes": nodes, "edges": edges, "stats": stats}
