"""Health check: detect when major UI components or graph files are missing.

This test file catches the class of bug where the transfer graph (or other
key UI pieces) disappears because a feature branch wasn't merged, a file
was accidentally deleted, or a template reference points to a non-existent
static asset.

Run:
    .venv/bin/pytest tests/unit/test_ui_health.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_TEMPLATES = _ROOT / "dashboard" / "templates"
_STATIC = _ROOT / "dashboard" / "static"
_SERVICES = _ROOT / "dashboard" / "services"


# ── Required files ──────────────────────────────────────────────────────────

REQUIRED_TEMPLATES = [
    "base.html",
    "dashboard.html",
    "pipeline.html",
    "cards.html",
    "transfers.html",
    "costs.html",
    "company_detail.html",
]

REQUIRED_STATIC = [
    "js/transfer-graph.js",
]

REQUIRED_SERVICES = [
    "__init__.py",
    "jurisdiction.py",
    "graph_data.py",
]


@pytest.mark.parametrize("tpl", REQUIRED_TEMPLATES)
def test_template_exists(tpl: str) -> None:
    path = _TEMPLATES / tpl
    assert path.exists(), f"Missing template: {path}"
    assert path.stat().st_size > 0, f"Template is empty: {path}"


@pytest.mark.parametrize("asset", REQUIRED_STATIC)
def test_static_asset_exists(asset: str) -> None:
    path = _STATIC / asset
    assert path.exists(), f"Missing static asset: {path}"
    assert path.stat().st_size > 0, f"Static asset is empty: {path}"


@pytest.mark.parametrize("mod", REQUIRED_SERVICES)
def test_service_module_exists(mod: str) -> None:
    path = _SERVICES / mod
    assert path.exists(), f"Missing service module: {path}"


# ── Graph integration health ───────────────────────────────────────────────


def test_transfers_template_has_graph_container() -> None:
    """transfers.html must reference the graph SVG container."""
    html = (_TEMPLATES / "transfers.html").read_text()
    assert 'id="transfer-graph"' in html, "transfers.html missing #transfer-graph SVG"
    assert 'id="graph-data"' in html, "transfers.html missing #graph-data JSON element"
    assert 'id="graph-depth-select"' in html, "transfers.html missing depth selector"
    assert 'id="coverage-donut"' in html, "transfers.html missing coverage donut SVG"


def test_transfers_template_loads_d3() -> None:
    """transfers.html must include the D3.js CDN script tag."""
    html = (_TEMPLATES / "transfers.html").read_text()
    assert "d3.v7" in html or "d3@7" in html, "transfers.html missing D3.js v7 CDN"


def test_transfers_template_loads_graph_js() -> None:
    """transfers.html must include transfer-graph.js."""
    html = (_TEMPLATES / "transfers.html").read_text()
    assert (
        "transfer-graph.js" in html
    ), "transfers.html missing transfer-graph.js script"


def test_graph_js_has_key_elements() -> None:
    """transfer-graph.js must define essential graph components."""
    js = (_STATIC / "js" / "transfer-graph.js").read_text()
    assert "d3.forceSimulation" in js, "transfer-graph.js missing D3 force simulation"
    assert "graph-zoom-in" in js, "transfer-graph.js missing zoom controls"
    assert "highlightCompany" in js, "transfer-graph.js missing highlight function"
    assert "panToNode" in js, "transfer-graph.js missing panToNode function"


# ── graph_data.py integrity ────────────────────────────────────────────────


def test_build_graph_data_empty() -> None:
    """build_graph_data should handle empty input without crashing."""
    from dashboard.services.graph_data import build_graph_data

    result = build_graph_data([], None)
    assert "nodes" in result
    assert "edges" in result
    assert "stats" in result
    # Should always have the "user" node
    assert len(result["nodes"]) == 1
    assert result["nodes"][0]["id"] == "user"


def test_build_graph_data_with_rows() -> None:
    """build_graph_data produces correct structure for sample input."""
    from dashboard.services.graph_data import build_graph_data

    rows = [
        {
            "domain": "example.com",
            "company_name": "Example Corp",
            "subprocessors": {
                "fetch_status": "ok",
                "fetched_at": "2026-01-01T00:00:00",
                "subprocessors": [
                    {
                        "domain": "analytics.io",
                        "company_name": "Analytics Inc",
                        "hq_country_code": "US",
                        "hq_country": "United States",
                        "purposes": ["analytics"],
                        "data_categories": ["usage data"],
                        "transfer_basis": "SCCs",
                    },
                ],
            },
            "has_email": True,
            "request_sent": False,
            "sp_status": None,
            "sp_replies": [],
        },
    ]
    result = build_graph_data(rows, None, max_depth=4)
    assert result["stats"]["total_companies"] == 1
    assert result["stats"]["total_subprocessors"] == 1
    # user + 1 company + 1 SP = 3 nodes
    assert len(result["nodes"]) == 3
    # user→company + company→SP = 2 edges
    assert len(result["edges"]) == 2


def test_build_graph_data_respects_max_depth() -> None:
    """max_depth=0 should still produce company nodes but no subprocessors."""
    from dashboard.services.graph_data import build_graph_data

    rows = [
        {
            "domain": "example.com",
            "company_name": "Example Corp",
            "subprocessors": {
                "fetch_status": "ok",
                "fetched_at": "2026-01-01T00:00:00",
                "subprocessors": [
                    {
                        "domain": "sp.io",
                        "company_name": "SP",
                        "purposes": [],
                        "data_categories": [],
                        "transfer_basis": "unknown",
                    },
                ],
            },
            "has_email": False,
            "request_sent": False,
            "sp_status": None,
            "sp_replies": [],
        },
    ]
    # max_depth is clamped to min 1 in the route, but graph_data accepts it
    result = build_graph_data(rows, None, max_depth=1)
    assert result["stats"]["total_subprocessors"] == 1  # depth 1 = direct SPs

    # With max_depth=0, no SPs should be added (only user + company)
    result0 = build_graph_data(rows, None, max_depth=0)
    assert len(result0["nodes"]) == 2  # user + company only


# ── jurisdiction.py integrity ──────────────────────────────────────────────


def test_jurisdiction_assess_risk() -> None:
    from dashboard.services.jurisdiction import assess_risk

    assert assess_risk("DE", "SCCs") == "adequate"
    assert assess_risk("US", "DPF") == "adequate"
    assert assess_risk("CN", "SCCs") == "safeguarded"
    assert assess_risk("CN", "unknown") == "risky"
    assert assess_risk("", "unknown") == "unknown"
    assert assess_risk(None, "unknown") == "unknown"


def test_jurisdiction_infer_country() -> None:
    from dashboard.services.jurisdiction import infer_country_code

    assert infer_country_code("example.de") == "DE"
    assert infer_country_code("example.co.uk") == "GB"
    assert infer_country_code("example.com") == ""
    assert infer_country_code("example.io") == ""
    assert infer_country_code("") == ""


# ── Navbar integrity ───────────────────────────────────────────────────────


def test_base_html_has_all_nav_links() -> None:
    """base.html must have links to all major dashboard pages."""
    html = (_TEMPLATES / "base.html").read_text()
    for page in ["Dashboard", "Pipeline", "Data Cards", "Costs", "Transfers"]:
        assert page in html, f"base.html missing nav link: {page}"


def test_base_html_has_nav_extra_block() -> None:
    """base.html must define the nav_extra block for per-page controls."""
    html = (_TEMPLATES / "base.html").read_text()
    assert "{% block nav_extra %}" in html, "base.html missing nav_extra block"


# ── Jinja2 syntax validation ─────────────────────────────────────────────


def test_all_templates_parse_without_syntax_errors() -> None:
    """Every .html template must be valid Jinja2 — catches unclosed if/for blocks."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)))
    errors = []
    for tpl_path in sorted(_TEMPLATES.glob("*.html")):
        try:
            env.get_template(tpl_path.name)
        except Exception as exc:
            errors.append(f"{tpl_path.name}: {exc}")
    assert not errors, "Jinja2 syntax errors:\n" + "\n".join(errors)
