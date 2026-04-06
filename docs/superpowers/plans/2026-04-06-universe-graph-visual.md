# Universe Graph Visual Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the same D3 force-directed graph visualization used on the main dashboard's transfers page to the GDPR Universe dashboard (full graph on `/` and enhanced neighborhood graph on `/company/<domain>`).

**Architecture:** Create `gdpr_universe/graph_builder.py` to query SQLite and produce the same `{nodes, edges, stats}` JSON shape as `dashboard/services/graph_data.py`. Create `gdpr_universe/static/js/universe-graph.js` adapted from `dashboard/static/js/transfer-graph.js` with dark-theme colors. Update both templates and routes.

**Tech Stack:** D3.js v7, Flask/Jinja2, SQLite, Bootstrap 5 dark theme

---

### Task 1: Create `universe-graph.js` (dark-theme D3 visualization)

**Files:**
- Create: `gdpr_universe/static/js/universe-graph.js`

This is adapted from `dashboard/static/js/transfer-graph.js` with these changes:
- Dark theme colors (node fills `#1a1d23`, text `#c9d1d9`, tooltip dark bg)
- No "user" center node concept — seeds are the top-level
- Configurable via `data-mode` attribute on `#graph-data`: `"full"` (dashboard) or `"neighborhood"` (company detail)
- In neighborhood mode: center node highlighted blue, click navigates to `/company/<domain>`
- In full mode: click highlights chain (same as transfer-graph.js), no navigation

- [ ] **Step 1: Create the JS file**

```javascript
// gdpr_universe/static/js/universe-graph.js
// Universe graph — D3.js v7 force-directed visualization (dark theme)
// Adapted from dashboard/static/js/transfer-graph.js
(function() {
  'use strict';

  const dataEl = document.getElementById('graph-data');
  if (!dataEl) return;

  const graphData = JSON.parse(dataEl.textContent);
  const { nodes, edges, stats } = graphData;
  const mode = dataEl.dataset.mode || 'full'; // 'full' or 'neighborhood'
  const centerDomain = dataEl.dataset.center || '';

  const svg = d3.select('#universe-graph');
  if (svg.empty()) return;

  const container = svg.node().parentElement;
  let width = container.clientWidth;
  let height = container.clientHeight || 500;

  svg.attr('viewBox', [0, 0, width, height]);

  // Risk → border color
  const riskColor = { adequate: '#3fb950', safeguarded: '#d29922', risky: '#f85149', unknown: '#8b949e' };

  // Favicon / initials helpers
  const FALLBACK_COLORS = [
    '#3d2f4f', '#2f3d4f', '#2f4f3d', '#4f3d2f',
    '#4f2f3d', '#3d4f2f', '#2f2f4f', '#4f4f2f',
  ];

  function domainHash(domain) {
    let hash = 0;
    for (let i = 0; i < domain.length; i++) {
      hash = ((hash << 5) - hash) + domain.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash);
  }

  function getInitials(label) {
    const words = label.split(/[\s\-_.]+/).filter(Boolean);
    if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
    return label.slice(0, 2).toUpperCase();
  }

  // Node sizing by type/depth
  function nodeSize(d) {
    if (d.is_center) return 24;
    if (d.type === 'seed') return 20;
    return 15;
  }
  function nodeOpacity(d) {
    if (d.depth !== undefined && d.depth > 1) return 0.7;
    return 1;
  }

  // Layer x-targets for full mode
  function layerX(d) {
    if (mode !== 'full') return width / 2; // no column layout in neighborhood
    if (d.type === 'seed') return width * 0.25;
    if (d.depth === 1) return width * 0.60;
    return width * 0.85;
  }

  // Edge style
  function edgeStyle(d) {
    if (d.confirmed) {
      return { stroke: '#3fb950', width: 2, dasharray: 'none', opacity: 0.7 };
    }
    if ((d.depth || 0) <= 1) {
      return { stroke: '#484f58', width: 1.5, dasharray: '4,3', opacity: 0.5 };
    }
    return { stroke: '#484f58', width: 1, dasharray: '2,3', opacity: 0.35 };
  }

  // Tooltip
  const tooltip = d3.select('body').append('div')
    .style('position', 'absolute')
    .style('background', '#1a1d23')
    .style('color', '#c9d1d9')
    .style('border', '1px solid #30363d')
    .style('border-radius', '6px')
    .style('padding', '8px 12px')
    .style('font-size', '0.78rem')
    .style('pointer-events', 'none')
    .style('opacity', 0)
    .style('z-index', 1000)
    .style('box-shadow', '0 2px 8px rgba(0,0,0,0.4)');

  // Zoom
  const g = svg.append('g');
  const zoom = d3.zoom()
    .scaleExtent([0.3, 4])
    .on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);

  // Defs for gradients, clips, glow
  const defs = svg.append('defs');

  // Glow filter
  const filter = defs.append('filter').attr('id', 'glow');
  filter.append('feGaussianBlur').attr('stdDeviation', '2').attr('result', 'blur');
  const merge = filter.append('feMerge');
  merge.append('feMergeNode').attr('in', 'blur');
  merge.append('feMergeNode').attr('in', 'SourceGraphic');

  // Per-edge gradients
  edges.forEach((e, i) => {
    const eStyle = edgeStyle(e);
    const color = e.confirmed ? eStyle.stroke : (e.color || '#484f58');
    const grad = defs.append('linearGradient')
      .attr('id', `edge-grad-${i}`)
      .attr('gradientUnits', 'userSpaceOnUse');
    grad.append('stop').attr('offset', '0%')
      .attr('stop-color', color).attr('stop-opacity', 0.85);
    grad.append('stop').attr('offset', '70%')
      .attr('stop-color', color).attr('stop-opacity', 0.25);
    grad.append('stop').attr('offset', '100%')
      .attr('stop-color', color).attr('stop-opacity', 0.05);
  });

  // Force simulation
  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(80).strength(0.3))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('collision', d3.forceCollide().radius(d => nodeSize(d) + 4))
    .force('y', d3.forceY(height / 2).strength(0.05));

  if (mode === 'full') {
    simulation.force('x', d3.forceX(d => layerX(d)).strength(0.4));
  } else {
    simulation.force('center', d3.forceCenter(width / 2, height / 2));
  }

  // Draw edges
  const link = g.append('g')
    .selectAll('line')
    .data(edges)
    .join('line')
    .attr('stroke', (d, i) => `url(#edge-grad-${i})`)
    .attr('stroke-width', d => edgeStyle(d).width)
    .attr('stroke-dasharray', d => edgeStyle(d).dasharray)
    .attr('opacity', d => edgeStyle(d).opacity);

  // Draw nodes
  const node = g.append('g')
    .selectAll('g')
    .data(nodes)
    .join('g')
    .attr('cursor', 'pointer')
    .call(d3.drag()
      .on('start', dragstarted)
      .on('drag', dragged)
      .on('end', dragended));

  node.each(function(d) {
    const el = d3.select(this);
    const s = nodeSize(d);
    const isCenter = d.is_center;
    const isSeed = d.type === 'seed';

    // Rounded rect
    el.append('rect')
      .attr('x', -s).attr('y', -s)
      .attr('width', s * 2).attr('height', s * 2)
      .attr('rx', isSeed ? 10 : 8)
      .attr('fill', isCenter ? '#0d6efd' : '#1a1d23')
      .attr('stroke', isCenter ? '#58a6ff' : riskColor[d.risk] || '#8b949e')
      .attr('stroke-width', isCenter ? 2.5 : 1.5);

    // Favicon
    const favSize = isSeed ? 26 : 20;
    const clipId = 'fc-' + (d.id || d.domain || '').replace(/[:.]/g, '-');
    defs.append('clipPath').attr('id', clipId)
      .append('rect')
      .attr('x', -favSize / 2).attr('y', -favSize / 2)
      .attr('width', favSize).attr('height', favSize)
      .attr('rx', 4);

    const fav = el.append('image')
      .attr('href', `https://www.google.com/s2/favicons?domain=${d.domain}&sz=32`)
      .attr('x', -favSize / 2).attr('y', -favSize / 2)
      .attr('width', favSize).attr('height', favSize)
      .attr('clip-path', `url(#${clipId})`);

    // Initials fallback
    const fallbackColor = FALLBACK_COLORS[domainHash(d.domain) % FALLBACK_COLORS.length];
    const initials = el.append('text')
      .text(getInitials(d.label))
      .attr('text-anchor', 'middle')
      .attr('dy', '0.35em')
      .attr('fill', '#c9d1d9')
      .attr('font-size', isSeed ? '14px' : '11px')
      .attr('font-weight', 700)
      .style('display', 'none');

    fav.on('error', function() {
      d3.select(this).style('display', 'none');
      initials.style('display', null);
      if (!isCenter) el.select('rect').attr('fill', fallbackColor);
    });
  });

  node.attr('opacity', d => nodeOpacity(d));

  // Tooltip on hover
  node.on('mouseover', function(event, d) {
    let html = `<strong>${d.label}</strong>`;
    if (d.domain) html += `<br><span style="color:#8b949e">${d.domain}</span>`;
    if (d.country) html += `<br>${d.country}`;
    if (d.risk) html += `<br>Risk: <span style="color:${riskColor[d.risk]}">${d.risk}</span>`;
    if (d.sharing_count > 1) html += `<br>Shared by ${d.sharing_count} companies`;
    if (d.service_category) html += `<br>Category: ${d.service_category.replace(/_/g, ' ')}`;
    tooltip.html(html)
      .style('left', (event.pageX + 12) + 'px')
      .style('top', (event.pageY - 12) + 'px')
      .transition().duration(150).style('opacity', 1);
  })
  .on('mousemove', function(event) {
    tooltip.style('left', (event.pageX + 12) + 'px')
      .style('top', (event.pageY - 12) + 'px');
  })
  .on('mouseout', function() {
    tooltip.transition().duration(150).style('opacity', 0);
  });

  // Simulation tick
  simulation.on('tick', () => {
    link.each(function(d, i) {
      d3.select(this)
        .attr('x1', d.source.x).attr('y1', d.source.y)
        .attr('x2', d.target.x).attr('y2', d.target.y);
      d3.select(`#edge-grad-${i}`)
        .attr('x1', d.source.x).attr('y1', d.source.y)
        .attr('x2', d.target.x).attr('y2', d.target.y);
    });
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.1).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(event, d) { d.fx = event.x; d.fy = event.y; }
  function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
  }

  // Zoom controls
  d3.select('#graph-zoom-in').on('click', () => svg.transition().call(zoom.scaleBy, 1.3));
  d3.select('#graph-zoom-out').on('click', () => svg.transition().call(zoom.scaleBy, 0.7));
  d3.select('#graph-reset').on('click', () => svg.transition().call(zoom.transform, d3.zoomIdentity));

  // Resize
  window.addEventListener('resize', () => {
    width = container.clientWidth;
    height = container.clientHeight || 500;
    svg.attr('viewBox', [0, 0, width, height]);
    if (mode === 'full') {
      simulation.force('x', d3.forceX(d => layerX(d)).strength(0.4));
    }
    simulation.force('y', d3.forceY(height / 2).strength(0.05));
    simulation.alpha(0.3).restart();
  });

  // ── Highlighting (full mode only) ──
  if (mode === 'full') {
    let activeHighlight = null;
    const _adjForward = {};
    const _adjReverse = {};
    edges.forEach(e => {
      const src = typeof e.source === 'object' ? e.source.id : e.source;
      const tgt = typeof e.target === 'object' ? e.target.id : e.target;
      if (!_adjForward[src]) _adjForward[src] = new Set();
      _adjForward[src].add(tgt);
      if (!_adjReverse[tgt]) _adjReverse[tgt] = new Set();
      _adjReverse[tgt].add(src);
    });

    function _collectChain(startId) {
      const visited = new Set();
      const queue = [startId];
      while (queue.length) {
        const cur = queue.shift();
        if (visited.has(cur)) continue;
        visited.add(cur);
        const children = _adjForward[cur];
        if (children) children.forEach(c => { if (!visited.has(c)) queue.push(c); });
      }
      const upQueue = [startId];
      while (upQueue.length) {
        const cur = upQueue.shift();
        if (visited.has(cur) && cur !== startId) continue;
        visited.add(cur);
        const parents = _adjReverse[cur];
        if (parents) parents.forEach(p => { if (!visited.has(p)) upQueue.push(p); });
      }
      return visited;
    }

    function _applyHighlight(chainNodes) {
      const chainEdges = new Set();
      edges.forEach(e => {
        const src = typeof e.source === 'object' ? e.source.id : e.source;
        const tgt = typeof e.target === 'object' ? e.target.id : e.target;
        if (chainNodes.has(src) && chainNodes.has(tgt)) chainEdges.add(e);
      });
      node.style('opacity', d => chainNodes.has(d.id) ? 1 : 0.08);
      link.style('opacity', d => chainEdges.has(d) ? 1 : 0.03)
          .attr('filter', d => chainEdges.has(d) ? 'url(#glow)' : null);
    }

    function clearHighlight() {
      node.style('opacity', d => nodeOpacity(d));
      link.style('opacity', null).attr('filter', null);
      activeHighlight = null;
    }

    node.on('click', function(event, d) {
      event.stopPropagation();
      if (activeHighlight === d.id) { clearHighlight(); return; }
      _applyHighlight(_collectChain(d.id));
      activeHighlight = d.id;
    });
    svg.on('click', function(event) {
      if (event.target === svg.node()) clearHighlight();
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') clearHighlight();
    });
  }

  // ── Navigation (neighborhood mode only) ──
  if (mode === 'neighborhood') {
    node.on('click', function(event, d) {
      if (d.domain) window.location = `/company/${d.domain}`;
    });
  }
})();
```

- [ ] **Step 2: Verify file was created**

Run: `ls -la gdpr_universe/static/js/universe-graph.js`
Expected: file exists with ~300 lines

- [ ] **Step 3: Commit**

```bash
git add gdpr_universe/static/js/universe-graph.js
git commit -m "feat(universe): add dark-theme D3 force graph JS"
```

---

### Task 2: Create `graph_builder.py` (SQLite → graph JSON)

**Files:**
- Create: `gdpr_universe/graph_builder.py`
- Test: `tests/unit/test_universe_graph_builder.py`

Produces `{nodes, edges, stats}` JSON matching the shape of `dashboard/services/graph_data.py` but reading from the Universe SQLite DB.

Two modes:
- `build_full_graph(engine)` — all seeds + their SPs (for dashboard `/`)
- `build_neighborhood_graph(engine, domain, hops=2)` — scoped to one company (for `/company/<domain>`)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_universe_graph_builder.py
"""Tests for gdpr_universe.graph_builder."""

import pytest
from sqlalchemy import text

from gdpr_universe.db import get_engine, init_db


@pytest.fixture
def engine(tmp_path):
    db_path = str(tmp_path / "test.db")
    eng = get_engine(db_path)
    init_db(eng)
    return eng


def _seed(engine, domain, name, country="", is_seed=True, service_category=""):
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT OR IGNORE INTO companies (domain, company_name, hq_country_code, is_seed, service_category) "
            "VALUES (:d, :n, :c, :s, :sc)"
        ), {"d": domain, "n": name, "c": country, "s": 1 if is_seed else 0, "sc": service_category})
        conn.commit()


def _edge(engine, parent, child, transfer_basis="", purposes="[]"):
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO edges (parent_domain, child_domain, depth, transfer_basis, purposes) "
            "VALUES (:p, :c, 1, :tb, :pu)"
        ), {"p": parent, "c": child, "tb": transfer_basis, "pu": purposes})
        conn.commit()


class TestBuildFullGraph:
    def test_empty_db(self, engine):
        from gdpr_universe.graph_builder import build_full_graph
        result = build_full_graph(engine)
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["stats"]["total_seeds"] == 0

    def test_seed_with_sps(self, engine):
        from gdpr_universe.graph_builder import build_full_graph
        _seed(engine, "shell.com", "Shell", "NL", is_seed=True)
        _seed(engine, "aws.com", "AWS", "US", is_seed=False)
        _edge(engine, "shell.com", "aws.com", transfer_basis="SCCs")

        result = build_full_graph(engine)
        assert result["stats"]["total_seeds"] == 1
        assert result["stats"]["total_subprocessors"] == 1
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1

        # Check node shapes
        seed_node = next(n for n in result["nodes"] if n["domain"] == "shell.com")
        assert seed_node["type"] == "seed"
        assert seed_node["id"] == "shell.com"
        sp_node = next(n for n in result["nodes"] if n["domain"] == "aws.com")
        assert sp_node["type"] == "subprocessor"
        assert sp_node["risk"] in ("adequate", "safeguarded", "risky", "unknown")

    def test_shared_sp_gets_sharing_count(self, engine):
        from gdpr_universe.graph_builder import build_full_graph
        _seed(engine, "shell.com", "Shell", "NL")
        _seed(engine, "bp.com", "BP", "GB")
        _seed(engine, "stripe.com", "Stripe", "US", is_seed=False)
        _edge(engine, "shell.com", "stripe.com")
        _edge(engine, "bp.com", "stripe.com")

        result = build_full_graph(engine)
        sp = next(n for n in result["nodes"] if n["domain"] == "stripe.com")
        assert sp["sharing_count"] == 2


class TestBuildNeighborhoodGraph:
    def test_neighborhood(self, engine):
        from gdpr_universe.graph_builder import build_neighborhood_graph
        _seed(engine, "shell.com", "Shell", "NL")
        _seed(engine, "aws.com", "AWS", "US", is_seed=False)
        _edge(engine, "shell.com", "aws.com")

        result = build_neighborhood_graph(engine, "shell.com", hops=2)
        assert len(result["nodes"]) == 2
        center = next(n for n in result["nodes"] if n["domain"] == "shell.com")
        assert center["is_center"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_universe_graph_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gdpr_universe.graph_builder'`

- [ ] **Step 3: Write minimal implementation**

```python
# gdpr_universe/graph_builder.py
"""Build D3-ready graph JSON from Universe SQLite data."""
from __future__ import annotations

from collections import Counter
import json

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import get_session
from gdpr_universe.graph_queries import ADEQUATE_COUNTRIES, neighborhood

# Inline risk assessment (mirrors dashboard/services/jurisdiction.py)
_SAFEGUARDED = {"IN", "BR", "PH", "SG", "MY", "TH", "MX", "CO", "CL", "AE"}

_CCTLD_MAP = {
    "uk": "GB", "de": "DE", "fr": "FR", "nl": "NL", "be": "BE", "it": "IT",
    "es": "ES", "pt": "PT", "at": "AT", "ch": "CH", "se": "SE", "no": "NO",
    "dk": "DK", "fi": "FI", "ie": "IE", "pl": "PL", "cz": "CZ", "ro": "RO",
    "hu": "HU", "bg": "BG", "hr": "HR", "sk": "SK", "si": "SI", "lt": "LT",
    "lv": "LV", "ee": "EE", "gr": "GR", "cy": "CY", "mt": "MT", "lu": "LU",
    "us": "US", "ca": "CA", "au": "AU", "nz": "NZ", "jp": "JP", "kr": "KR",
    "in": "IN", "br": "BR", "mx": "MX", "cn": "CN", "ru": "RU", "za": "ZA",
    "sg": "SG", "hk": "HK", "il": "IL", "ar": "AR",
}

_GENERIC_TLDS = {"com", "org", "net", "io", "ai", "co", "dev", "app", "tech", "xyz", "info"}


def _infer_country(domain: str) -> str:
    parts = domain.lower().rstrip(".").split(".")
    if len(parts) < 2:
        return ""
    if len(parts) >= 3 and parts[-2] in _GENERIC_TLDS:
        return _CCTLD_MAP.get(parts[-1], "")
    tld = parts[-1]
    if tld in _GENERIC_TLDS:
        return ""
    return _CCTLD_MAP.get(tld, "")


def _assess_risk(country_code: str) -> str:
    if not country_code:
        return "unknown"
    if country_code in ADEQUATE_COUNTRIES:
        return "adequate"
    if country_code in _SAFEGUARDED:
        return "safeguarded"
    return "risky"


def build_full_graph(engine: Engine) -> dict:
    """Build graph of all seeds → subprocessors for the dashboard page."""
    with get_session(engine) as session:
        # All seed companies
        seed_rows = session.execute(text(
            "SELECT domain, company_name, hq_country_code, service_category "
            "FROM companies WHERE is_seed = 1"
        )).fetchall()

        # All edges from seeds
        edge_rows = session.execute(text(
            "SELECT e.parent_domain, e.child_domain, e.depth, e.purposes, "
            "       e.transfer_basis, c.company_name, c.hq_country_code, "
            "       c.service_category "
            "FROM edges e "
            "JOIN companies c ON e.child_domain = c.domain"
        )).fetchall()

    nodes = []
    edges = []
    sp_registry: dict[str, dict] = {}
    country_set: set[str] = set()
    non_adequate: set[str] = set()
    basis_counter: Counter = Counter()
    seed_domains: set[str] = set()

    # Seed nodes
    for row in seed_rows:
        domain, name, country, scat = row
        seed_domains.add(domain)
        country = country or _infer_country(domain)
        if country:
            country_set.add(country)
        nodes.append({
            "id": domain,
            "domain": domain,
            "type": "seed",
            "label": name or domain,
            "country": country,
            "risk": _assess_risk(country),
            "is_center": False,
            "is_seed": True,
            "service_category": scat or "",
            "sharing_count": 0,
            "depth": 0,
        })

    # Edges + SP nodes
    for row in edge_rows:
        parent, child, depth, purposes_raw, basis, sp_name, sp_country, sp_scat = row
        sp_country = sp_country or _infer_country(child)
        basis = basis or "unknown"
        basis_counter[basis] += 1

        if sp_country:
            country_set.add(sp_country)
        if sp_country and sp_country not in ADEQUATE_COUNTRIES:
            non_adequate.add(child)

        # Parse purposes
        purposes = []
        if purposes_raw:
            try:
                purposes = json.loads(purposes_raw)
            except (json.JSONDecodeError, TypeError):
                purposes = [purposes_raw] if purposes_raw else []

        # Dedup SP nodes
        if child not in sp_registry and child not in seed_domains:
            sp_node = {
                "id": child,
                "domain": child,
                "type": "subprocessor",
                "label": sp_name or child,
                "country": sp_country,
                "risk": _assess_risk(sp_country),
                "is_center": False,
                "is_seed": False,
                "service_category": sp_scat or "",
                "sharing_count": 1,
                "depth": depth or 1,
            }
            sp_registry[child] = sp_node
            nodes.append(sp_node)
        elif child in sp_registry:
            sp_registry[child]["sharing_count"] += 1

        edges.append({
            "source": parent,
            "target": child,
            "type": "data_flow",
            "purposes": purposes,
            "transfer_basis": basis,
            "confirmed": False,
            "depth": depth or 1,
            "color": "#484f58",
        })

    stats = {
        "total_seeds": len(seed_rows),
        "total_subprocessors": len(sp_registry),
        "countries_reached": len(country_set),
        "non_adequate_count": len(non_adequate),
        "transfer_basis_breakdown": dict(basis_counter),
    }

    return {"nodes": nodes, "edges": edges, "stats": stats}


def build_neighborhood_graph(engine: Engine, domain: str, *, hops: int = 2) -> dict:
    """Build graph scoped to N-hop neighborhood of a single company."""
    raw_nodes, raw_edges = neighborhood(engine, domain, hops=hops)

    nodes = []
    edges = []
    sp_count = 0

    for n in raw_nodes:
        country = n.get("hq_country_code") or _infer_country(n["domain"])
        is_center = n["domain"] == domain
        is_seed = n.get("is_seed", False)
        nodes.append({
            "id": n["domain"],
            "domain": n["domain"],
            "type": "seed" if is_seed else "subprocessor",
            "label": n.get("company_name") or n["domain"],
            "country": country,
            "risk": _assess_risk(country),
            "is_center": is_center,
            "is_seed": is_seed,
            "service_category": n.get("service_category", ""),
            "sharing_count": 0,
            "depth": 0 if is_center else 1,
        })
        if not is_seed and not is_center:
            sp_count += 1

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
            "type": "data_flow",
            "purposes": purposes,
            "transfer_basis": e.get("transfer_basis", ""),
            "confirmed": False,
            "depth": e.get("depth") or 1,
            "color": "#484f58",
        })

    stats = {
        "total_seeds": sum(1 for n in nodes if n["is_seed"]),
        "total_subprocessors": sp_count,
        "countries_reached": len({n["country"] for n in nodes if n["country"]}),
        "non_adequate_count": sum(1 for n in nodes if n["country"] and n["country"] not in ADEQUATE_COUNTRIES),
        "transfer_basis_breakdown": {},
    }

    return {"nodes": nodes, "edges": edges, "stats": stats}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_universe_graph_builder.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add gdpr_universe/graph_builder.py tests/unit/test_universe_graph_builder.py
git commit -m "feat(universe): add graph_builder for D3 graph JSON"
```

---

### Task 3: Update dashboard template and route (full graph on `/`)

**Files:**
- Modify: `gdpr_universe/templates/dashboard.html`
- Modify: `gdpr_universe/routes/dashboard.py`

Add the full force-directed graph above the existing seed companies table, with a legend and zoom controls matching the transfers page style.

- [ ] **Step 1: Update the route to pass graph JSON**

In `gdpr_universe/routes/dashboard.py`, add graph_builder import and pass graph data:

```python
# At top of file, add import:
from gdpr_universe.graph_builder import build_full_graph

# In index(), before render_template, add:
    graph_data = build_full_graph(engine)

# In render_template, add:
        graph_json=graph_data,
```

Full updated `index()` function:

```python
@bp.route("/")
def index():
    """Main dashboard page."""
    engine = _get_engine()
    stats = _get_stats(engine)

    search = request.args.get("search", "")
    sort = request.args.get("sort", "company_name")
    order = request.args.get("order", "asc")

    companies = _get_company_rows(engine, search=search, sort=sort, order=order)

    # Top 20 subprocessors by sharing count
    sc = sharing_counts(engine, limit=20)
    top_sps: list[dict] = []
    if sc:
        with get_session(engine) as session:
            for domain, count in sc.items():
                row = session.execute(
                    text("SELECT company_name FROM companies WHERE domain = :d"),
                    {"d": domain},
                ).fetchone()
                top_sps.append({
                    "domain": domain,
                    "company_name": row[0] if row and row[0] else domain,
                    "count": count,
                })

    graph_data = build_full_graph(engine)

    return render_template(
        "dashboard.html",
        active_tab="dashboard",
        stats=stats,
        companies=companies,
        top_sps=top_sps,
        search=search,
        sort=sort,
        order=order,
        graph_json=graph_data,
    )
```

- [ ] **Step 2: Update the template**

Replace `gdpr_universe/templates/dashboard.html` with:

```html
{% extends "base.html" %}

{% block title %}Dashboard — GDPR Universe{% endblock %}

{% block content %}
<!-- Stats bar -->
<div class="row g-3 mb-4">
    <div class="col">
        <div class="stat-card">
            <div class="stat-value">{{ stats.total_companies }}</div>
            <div class="stat-label">Seed Companies</div>
        </div>
    </div>
    <div class="col">
        <div class="stat-card">
            <div class="stat-value">{{ stats.total_sps }}</div>
            <div class="stat-label">Subprocessors</div>
        </div>
    </div>
    <div class="col">
        <div class="stat-card">
            <div class="stat-value">{{ stats.total_edges }}</div>
            <div class="stat-label">Data Flows</div>
        </div>
    </div>
    <div class="col">
        <div class="stat-card">
            <div class="stat-value">{{ stats.countries_reached }}</div>
            <div class="stat-label">Countries</div>
        </div>
    </div>
    <div class="col">
        <div class="stat-card">
            <div class="stat-value">{{ stats.coverage_pct }}%</div>
            <div class="stat-label">Coverage</div>
        </div>
    </div>
</div>

{# ── Graph Visualization ── #}
{% if graph_json.nodes %}
<div class="card bg-dark border-secondary mb-4">
  <div class="card-body p-0 position-relative" style="height:550px;overflow:hidden">
    <svg id="universe-graph" width="100%" height="100%"></svg>
    {# Legend — bottom-left #}
    <div class="position-absolute bottom-0 start-0 m-2 bg-dark bg-opacity-90 border border-secondary rounded p-2" style="font-size:0.72rem;z-index:10">
      <div class="d-flex gap-2 flex-wrap align-items-center text-light">
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #3fb950;vertical-align:middle"></span> adequate</span>
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #d29922;vertical-align:middle"></span> safeguarded</span>
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #f85149;vertical-align:middle"></span> risky</span>
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #8b949e;vertical-align:middle"></span> unknown</span>
      </div>
      <div class="d-flex gap-2 flex-wrap align-items-center mt-1 text-muted">
        <span>■ seed company</span>
        <span>□ subprocessor</span>
      </div>
    </div>
    {# Controls — bottom-right #}
    <div class="position-absolute bottom-0 end-0 m-2 d-flex gap-1" style="z-index:10">
      <button class="btn btn-sm btn-outline-secondary py-0 px-2" id="graph-zoom-in" style="font-size:0.75rem">+</button>
      <button class="btn btn-sm btn-outline-secondary py-0 px-2" id="graph-zoom-out" style="font-size:0.75rem">-</button>
      <button class="btn btn-sm btn-outline-secondary py-0 px-2" id="graph-reset" style="font-size:0.75rem">reset</button>
    </div>
  </div>
</div>
{% endif %}

<div class="row">
    <!-- Company table -->
    <div class="col-lg-9">
        <div class="card bg-dark border-secondary">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h5 class="mb-0">Seed Companies</h5>
                <form class="d-flex" method="get" action="/">
                    <input type="text" class="form-control form-control-sm me-2"
                           name="search" placeholder="Search companies..."
                           value="{{ search }}">
                    <button type="submit" class="btn btn-sm btn-outline-light">Search</button>
                </form>
            </div>
            <div class="card-body p-0">
                {% if companies %}
                <div class="table-responsive">
                    <table class="table table-dark table-hover company-table mb-0">
                        <thead>
                            <tr>
                                {% set cols = [
                                    ('company_name', 'Name'),
                                    ('hq_country_code', 'Country'),
                                    ('sector', 'Sector'),
                                    ('sp_count', 'SPs'),
                                    ('fetch_status', 'Status'),
                                ] %}
                                {% for col_key, col_label in cols %}
                                <th>
                                    <a href="?sort={{ col_key }}&order={{ 'desc' if sort == col_key and order == 'asc' else 'asc' }}{% if search %}&search={{ search }}{% endif %}"
                                       class="text-decoration-none text-light">
                                        {{ col_label }}
                                        {% if sort == col_key %}
                                            {{ '&#9650;' if order == 'asc' else '&#9660;' }}
                                        {% endif %}
                                    </a>
                                </th>
                                {% endfor %}
                            </tr>
                        </thead>
                        <tbody>
                            {% for c in companies %}
                            <tr class="clickable-row" data-href="/company/{{ c.domain }}">
                                <td>
                                    <img src="https://www.google.com/s2/favicons?domain={{ c.domain }}&sz=16"
                                         alt="" class="me-2" width="16" height="16">
                                    {{ c.company_name }}
                                </td>
                                <td>{{ c.hq_country_code }}</td>
                                <td>{{ c.sector }}</td>
                                <td>{{ c.sp_count }}</td>
                                <td><span class="fetch-status fetch-status-{{ c.fetch_status }}">{{ c.fetch_status }}</span></td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="text-center text-muted py-5">
                    {% if search %}
                        No companies matching "{{ search }}".
                    {% else %}
                        No seed companies yet. Import an index to get started.
                    {% endif %}
                </div>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- Top Subprocessors leaderboard -->
    <div class="col-lg-3">
        <div class="card bg-dark border-secondary">
            <div class="card-header">
                <h5 class="mb-0">Top Subprocessors</h5>
            </div>
            <div class="card-body p-0">
                {% if top_sps %}
                <div class="list-group list-group-flush">
                    {% for sp in top_sps %}
                    <a href="/contagion/{{ sp.domain }}"
                       class="list-group-item list-group-item-action bg-dark text-light leaderboard-item">
                        <div class="d-flex justify-content-between align-items-center">
                            <span class="text-truncate me-2">{{ sp.company_name }}</span>
                            <span class="badge bg-primary rounded-pill">{{ sp.count }}</span>
                        </div>
                    </a>
                    {% endfor %}
                </div>
                {% else %}
                <div class="text-center text-muted py-4">
                    No subprocessor data yet.
                </div>
                {% endif %}
            </div>
        </div>
    </div>
</div>

<script id="graph-data" type="application/json" data-mode="full">
{{ graph_json | tojson }}
</script>
{% endblock %}

{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script src="{{ url_for('static', filename='js/universe-graph.js') }}"></script>
<script>
document.querySelectorAll('.clickable-row').forEach(row => {
    row.addEventListener('click', () => {
        window.location.href = row.dataset.href;
    });
});
</script>
{% endblock %}
```

- [ ] **Step 3: Verify the page renders**

Run: `curl -s http://localhost:5002/ | grep -c 'universe-graph'`
Expected: at least 1 match (the SVG element)

- [ ] **Step 4: Commit**

```bash
git add gdpr_universe/routes/dashboard.py gdpr_universe/templates/dashboard.html
git commit -m "feat(universe): add full D3 graph to dashboard page"
```

---

### Task 4: Update company detail template and route (enhanced neighborhood graph)

**Files:**
- Modify: `gdpr_universe/templates/company.html`
- Modify: `gdpr_universe/routes/company.py`

Replace the inline D3 graph with the same `universe-graph.js` component, using `data-mode="neighborhood"` and `data-center="<domain>"`.

- [ ] **Step 1: Update the route to use graph_builder**

In `gdpr_universe/routes/company.py`, replace raw `graph_nodes`/`graph_edges` JSON with `graph_json` from `build_neighborhood_graph`:

```python
# Replace import:
# from gdpr_universe.graph_queries import neighborhood
from gdpr_universe.graph_builder import build_neighborhood_graph

# Replace lines 74-85 with:
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
```

- [ ] **Step 2: Update the template**

Replace `gdpr_universe/templates/company.html` with:

```html
{% extends "base.html" %}

{% block title %}{{ company.company_name or company.domain }} — GDPR Universe{% endblock %}

{% block content %}
<a href="/" class="btn btn-sm btn-outline-secondary mb-3">&larr; Back to Dashboard</a>

{# ── Company header ── #}
<div class="d-flex align-items-center mb-4">
    <img src="https://www.google.com/s2/favicons?domain={{ company.domain }}&sz=32"
         alt="" width="32" height="32" class="me-3 rounded">
    <div>
        <h3 class="mb-0">
            {{ company.company_name or company.domain }}
            <small class="text-muted fs-6">{{ company.domain }}</small>
        </h3>
        <span class="text-muted small">
            {% if company.hq_country_code %}{{ company.hq_country_code }}{% endif %}
            {% if company.sector %}&middot; {{ company.sector }}{% endif %}
        </span>
    </div>
    <div class="ms-auto d-flex align-items-center gap-2">
        {% for idx in indices %}
        <span class="badge bg-info">{{ idx.index_name }}{% if idx.ticker %} ({{ idx.ticker }}){% endif %}</span>
        {% endfor %}
        {% if latest_fetch %}
        <span class="badge {% if latest_fetch.fetch_status == 'ok' %}bg-success{% elif latest_fetch.fetch_status == 'error' %}bg-danger{% else %}bg-secondary{% endif %}">
            {{ latest_fetch.fetch_status or 'pending' }}
        </span>
        {% else %}
        <span class="badge bg-secondary">pending</span>
        {% endif %}
    </div>
</div>

{# ── Two-column layout ── #}
<div class="row">
    {# Left: SP table #}
    <div class="col-lg-6 mb-4">
        <div class="card bg-dark border-secondary">
            <div class="card-header border-secondary">
                <strong>Subprocessors</strong>
                <span class="badge bg-secondary ms-2">{{ sps|length }}</span>
            </div>
            <div class="card-body p-0">
                {% if sps %}
                <div class="table-responsive">
                    <table class="table table-dark table-hover table-sm mb-0">
                        <thead>
                            <tr>
                                <th>Company</th>
                                <th>Country</th>
                                <th>Category</th>
                                <th>Transfer Basis</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for sp in sps %}
                            <tr role="button" onclick="window.location='/company/{{ sp.domain }}'">
                                <td>
                                    <img src="https://www.google.com/s2/favicons?domain={{ sp.domain }}&sz=16"
                                         alt="" width="16" height="16" class="me-1">
                                    {{ sp.company_name }}
                                </td>
                                <td>{{ sp.hq_country_code }}</td>
                                <td>{{ sp.service_category }}</td>
                                <td>{{ sp.transfer_basis }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <p class="text-muted p-3 mb-0">No subprocessors discovered yet.</p>
                {% endif %}
            </div>
        </div>
    </div>

    {# Right: Graph #}
    <div class="col-lg-6 mb-4">
        <div class="card bg-dark border-secondary">
            <div class="card-header border-secondary d-flex justify-content-between align-items-center">
                <strong>Data Flow Graph (2 hops)</strong>
                <div class="d-flex gap-1">
                  <button class="btn btn-sm btn-outline-secondary py-0 px-2" id="graph-zoom-in" style="font-size:0.75rem">+</button>
                  <button class="btn btn-sm btn-outline-secondary py-0 px-2" id="graph-zoom-out" style="font-size:0.75rem">-</button>
                  <button class="btn btn-sm btn-outline-secondary py-0 px-2" id="graph-reset" style="font-size:0.75rem">reset</button>
                </div>
            </div>
            <div class="card-body p-0 position-relative" style="height:450px;overflow:hidden">
                <svg id="universe-graph" width="100%" height="100%"></svg>
                {# Legend — bottom-left #}
                <div class="position-absolute bottom-0 start-0 m-2 bg-dark bg-opacity-90 border border-secondary rounded p-2" style="font-size:0.72rem;z-index:10">
                  <div class="d-flex gap-2 flex-wrap align-items-center text-light">
                    <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #3fb950;vertical-align:middle"></span> adequate</span>
                    <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #d29922;vertical-align:middle"></span> safeguarded</span>
                    <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #f85149;vertical-align:middle"></span> risky</span>
                    <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;border:2px solid #8b949e;vertical-align:middle"></span> unknown</span>
                  </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script id="graph-data" type="application/json" data-mode="neighborhood" data-center="{{ company.domain }}">
{{ graph_json | tojson }}
</script>
{% endblock %}

{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<script src="{{ url_for('static', filename='js/universe-graph.js') }}"></script>
{% endblock %}
```

- [ ] **Step 3: Run all tests**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: all pass (existing + new graph_builder tests)

- [ ] **Step 4: Commit**

```bash
git add gdpr_universe/routes/company.py gdpr_universe/templates/company.html
git commit -m "feat(universe): replace inline D3 with universe-graph.js on company page"
```

---

### Task 5: Verify both pages render correctly

- [ ] **Step 1: Restart the app**

Run: kill any running universe app, then `cd /Users/nemasha/MyProjects/gdpr-agent && .venv/bin/python -m gdpr_universe.app &`

- [ ] **Step 2: Check dashboard renders with graph**

Run: `curl -s http://localhost:5002/ | grep -c 'universe-graph'`
Expected: 1

- [ ] **Step 3: Seed a test company and check company detail**

If DB is already seeded (42 companies from earlier), pick one:
Run: `curl -s http://localhost:5002/company/shell.com | grep -c 'universe-graph'`
Expected: 1

- [ ] **Step 4: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: all pass

- [ ] **Step 5: Final commit (if any remaining changes)**

```bash
git add -A
git status
# Only commit if there are changes
git commit -m "feat(universe): complete graph visual upgrade"
```
