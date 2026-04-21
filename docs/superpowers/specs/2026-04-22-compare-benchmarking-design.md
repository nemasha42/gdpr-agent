# Company Benchmarking & Comparison — Design Spec

**Date:** 2026-04-22
**Route:** `/compare` (new top-level nav tab)
**Stack:** Python (Flask + SQLAlchemy) backend, Chart.js + vanilla JS frontend

## Overview

A dedicated benchmarking page that automatically compares all seed companies across GDPR compliance dimensions, with an optional side-by-side detail view for up to 5 selected companies. No manual setup needed — all metrics are computed from existing DB data.

## Page Structure

Top-to-bottom layout, two zones:

### Zone 1: All-Company Benchmarking (always visible)

1. **Header + Filters** — page title + 3 dropdowns: country, sector, SP category. All three compose via query params and filter both charts and matrix.
2. **Charts Row 1** (4 charts, `grid 1fr 1fr 1fr 1fr`):
   - **SP Count** — horizontal bar, seeds ranked by subprocessor count
   - **Jurisdiction Risk** — stacked horizontal bar, adequate/safeguarded/risky % per company
   - **Cross-border Transfers** — stacked bar, EU vs non-EU SP count per company
   - **Most Shared SPs** — horizontal bar, top 10 SPs by % of seeds using them
3. **Charts Row 2** (3 charts, `grid 1fr 1fr 1fr`):
   - **Vendor Lock-in Risk** — stacked bar, unique vs shared SP % per company
   - **Geographic Concentration** — stacked bar, top country % per company
   - **Transparency Ranking** — horizontal bar, composite disclosure score (0-100)
4. **Sector Peer Benchmarking** — panel grouped by sector, shows each company vs sector average with up/down arrows per metric
5. **Common Alternatives** — table grouped by `service_category`, lists alternative vendors for the same function with "used by" attribution
6. **Comparison Matrix** — full table, all seeds as rows, sortable columns with heatmap cell coloring

### Zone 2: Side-by-Side Comparison (toggled by user)

Appears below the matrix when user checks 2-5 companies and clicks "Compare Selected":
- Side-by-side cards (one per company) showing all metrics + shared/unique SP tags
- Overlap summary bar: shared by all, shared by 2+, total unique, combined non-EU exposure

## Filters

| Filter | Param | Scope |
|--------|-------|-------|
| Country | `?country=NL` | Seeds by `hq_country_code` |
| Sector | `?sector=Fintech` | Seeds by `sector` |
| SP Category | `?category=hr` | Scopes all per-company metrics to edges where child `service_category` matches |

Country and sector filter seed companies shown. SP category filter scopes the metrics themselves — e.g., "show me only HR subprocessors" changes SP counts, jurisdiction splits, lock-in ratios, etc. to reflect only that category.

Country + sector are cached (precomputed for all seeds). SP category is computed at query time since there are only ~9 categories and queries are cheap on ~42 seeds.

## Comparison Matrix

### Columns

| Column | Source | Heatmap coloring |
|--------|--------|-----------------|
| Company | `companies.company_name` + country + sector | — |
| SPs | edge count per parent | — |
| Adequate % | child `hq_country_code` in adequate set | Green ≥70%, yellow ≥40%, red <40% |
| Risky % | child `hq_country_code` not in adequate or safeguarded | Green ≤10%, yellow ≤20%, red >20% |
| Basis % | `edges.transfer_basis IS NOT NULL` / total | Green ≥70%, yellow ≥40%, red <40% |
| Lock-in | unique SPs / total SPs | Green ≤30%, yellow ≤50%, red >50% |
| X-border | child country not EU/EEA, absolute count | — |
| Depth | `MAX(edges.depth)` | — |
| Quality | `_derive_quality()` from fetch_log | Badge: high/med/low |
| Score | Composite A/B/C/D | Color-coded letter |

### Features

- **Search field** — filters rows by company name, highlights matches with `<mark>` tag. Selected-but-hidden rows shown in summary row below results.
- **Sortable columns** — click header to sort asc/desc, arrow indicator on active sort column
- **Checkboxes** — select 2-5 companies for side-by-side. Counter on "Compare Selected" button updates live. Checkboxes disabled when 5 reached.
- **Row click** — arrow link navigates to `/company/<domain>` detail page
- **Selected row highlight** — `#f0f4ff` background on checked rows

### Info Tooltips

Every chart panel title and every matrix column header has an `ⓘ` icon with a `title` attribute explaining the metric in plain language. Tooltip text:

| Metric | Tooltip |
|--------|---------|
| SP Count | Number of subprocessors this company uses. Filtered when SP category is selected. |
| Jurisdiction Risk | Percentage of SPs in adequate (EU equivalence), safeguarded (SCCs/BCRs), or risky (no safeguards) jurisdictions. |
| Cross-border Transfers | Data flows from this EU/EEA company to non-EU/EEA subprocessors. Higher = more reliance on international transfer mechanisms. |
| Most Shared SPs | Subprocessors used by the highest % of seed companies. A breach at these SPs would affect many companies. |
| Vendor Lock-in | % of SPs unique to this company. High = harder vendor switching, less benchmarking data. |
| Geographic Concentration | How concentrated SPs are in one country. High single-country % = regulatory concentration risk. |
| Transparency Ranking | Disclosure posture: official SP list page, documented purposes and transfer bases, data quality score. |
| Sector Peer Benchmarking | Company vs sector average. Green arrow = above average, red = below. Identifies sector outliers. |
| Common Alternatives | Different vendors for the same function across companies. Useful for procurement benchmarking. |
| Adequate % | % of SPs in EU-adequate jurisdictions. |
| Risky % | % of SPs in jurisdictions with no known adequacy or safeguards. |
| Basis % | % of edges with a documented GDPR transfer basis (SCCs, adequacy, BCRs). |
| Lock-in | % of SPs unique to this company (not used by any other seed). |
| X-border | Count of data flows to non-EU/EEA subprocessors. |
| Score | Composite GDPR posture (A-D). Weighted: jurisdiction 30%, basis 25%, transparency 20%, lock-in 15%, geo concentration 10%. |

## Composite Score

Weighted formula producing a 0-100 numeric score, mapped to letter grades:

| Component | Weight | Input | Normalization |
|-----------|--------|-------|---------------|
| Jurisdiction risk | 30% | `adequate_pct` | Direct (higher = better) |
| Transfer basis coverage | 25% | `basis_pct` | Direct |
| Transparency | 20% | `transparency_score` (0-100) | Direct |
| Vendor lock-in | 15% | `lockin_pct` | Inverted (lower = better) |
| Geographic concentration | 10% | top country % | Inverted (lower = better) |

**Transparency score** sub-formula:
- Data quality high = 40pts, medium = 20pts, low/unknown = 0
- basis_pct * 30 (max 30pts)
- field_coverage_pct * 30 (max 30pts from existing `field_coverage` in analytics)

**Grade thresholds:** A ≥ 75, B ≥ 50, C ≥ 25, D < 25

## Backend Architecture

### New files

| File | Purpose |
|------|---------|
| `gdpr_universe/compare.py` | Metric computation — SQL queries, composite score, caching |
| `gdpr_universe/routes/compare.py` | Flask blueprint — `/compare`, `/compare/refresh`, `/api/compare/side-by-side` |
| `gdpr_universe/templates/compare.html` | Jinja template — page layout, charts, matrix |
| `gdpr_universe/static/js/compare.js` | Chart.js rendering, search, sort, checkbox logic, side-by-side fetch |

### Modified files

| File | Change |
|------|--------|
| `gdpr_universe/app.py` | Register `compare_bp` blueprint |
| `gdpr_universe/templates/base.html` | Add "Compare" nav link |

### Routes

| Route | Method | Returns |
|-------|--------|---------|
| `/compare` | GET | Full HTML page. Query params: `country`, `sector`, `category` |
| `/compare/refresh` | POST | JSON `{"keys_updated": [...]}`. Recomputes all comparison metrics. |
| `/api/compare/side-by-side` | GET | JSON with per-company metrics + overlap data. Query param: `domains` (comma-separated, 2-5) |

### Cached metrics (in `AnalyticsCache`)

| Key | Shape | Description |
|-----|-------|-------------|
| `compare_matrix` | `list[dict]` | Per-seed-company row with all metrics (domain, company_name, hq_country_code, sector, sp_count, category_count, adequate_pct, risky_pct, basis_pct, lockin_pct, xborder_count, xborder_total, max_depth, quality, transparency_score, geo_top_country, geo_top_pct, composite_score, grade) |
| `compare_shared_sps` | `dict[domain, {count, pct}]` | Per SP domain, how many seeds use it |
| `compare_alternatives` | `list[{category, vendors: [{domain, name, used_by: [seeds]}]}]` | Alternative vendors grouped by function |
| `compare_sector_averages` | `dict[sector, {avg_sp_count, avg_adequate_pct, ...}]` | Per sector, average of each metric |

SP category filter (`?category=X`) is not cached — recomputed at request time by passing the category into the SQL queries. The queries scope `JOIN` clauses to `WHERE child.service_category = :cat`.

**Dependency:** `refresh_compare()` reads `field_coverage` from `AnalyticsCache` (computed by `refresh_analytics()`) when building the transparency sub-score. If `field_coverage` is missing, the field_coverage component defaults to 0. The `/compare/refresh` route should call `refresh_analytics()` first if the analytics cache is empty.

### Jurisdiction classification

Reuses `_assess_risk()` / `_infer_country()` from `graph_builder.py` and the adequate/safeguarded country sets from `dashboard.services.jurisdiction`. EU/EEA set for cross-border: the 27 EU members + NO, IS, LI, CH.

### Side-by-side API response shape

```json
{
  "companies": [
    {
      "domain": "sap.com",
      "company_name": "SAP",
      "hq_country_code": "DE",
      "sector": "Enterprise SW",
      "metrics": { "sp_count": 42, "adequate_pct": 60, ... },
      "grade": "B",
      "sps": ["aws.amazon.com", "google.com", ...]
    }
  ],
  "overlap": {
    "shared_by_all": ["aws.amazon.com", "google.com"],
    "shared_by_some": {"salesforce.com": ["sap.com", "asml.com"], ...},
    "total_unique": 54,
    "combined_xborder_pct": 67
  }
}
```

## Frontend Architecture

### Chart.js setup

CDN-loaded (`<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>`) in `compare.html` `{% block scripts %}`. No build step.

7 charts rendered from the `compare-data` JSON blob:
- 3x horizontal bar: SP count, most shared SPs, transparency ranking
- 4x stacked horizontal bar: jurisdiction risk, cross-border transfers, vendor lock-in, geographic concentration

Chart.js config: `indexAxis: 'y'`, `responsive: true`, `maintainAspectRatio: false`, consistent color palette matching existing app badges.

### compare.js responsibilities

1. **Chart init** — read `#compare-data`, render 7 `<canvas>` charts on `DOMContentLoaded`
2. **Search** — `input` event listener on search field, filter `<tbody>` rows by `textContent.includes()`, wrap matches in `<mark>`, show summary of hidden-but-selected rows
3. **Sort** — click handler on `<th>` elements, sort `<tr>` array by `dataset` attributes, toggle direction, move sorted rows in DOM
4. **Checkboxes** — `change` listener, update counter badge, disable unchecked when 5 reached, toggle row background
5. **Compare button** — `click` → collect checked domains → `fetch('/api/compare/side-by-side?domains=...')` → build side-by-side cards DOM → append to page → `scrollIntoView({behavior: 'smooth'})`

No framework, no build tooling — vanilla JS consistent with existing `universe-graph.js` and dashboard inline scripts.

### Template structure (`compare.html`)

```
{% extends "base.html" %}
{% block content %}
  Header + filters (form, GET)
  Charts row 1 (4x <div class="card"><canvas>)
  Charts row 2 (3x <div class="card"><canvas>)
  Sector peer panel (<div class="card">)
  Alternatives table (<div class="card"><table>)
  Matrix (<div class="card"> with search + table + checkboxes)
  Side-by-side container (<div id="side-by-side"> — empty, populated by JS)
  <script id="compare-data" type="application/json">{{ compare_data | tojson }}</script>
{% endblock %}
{% block scripts %}
  Chart.js CDN
  compare.js
{% endblock %}
```

## Styling

All styling uses existing `universe.css` classes — no new CSS file needed:
- Cards: `.card`, `.card-header`
- Tables: `.table`, `.table-hover`, `.table-sm`
- Badges: `.badge`, `.bg-success`, `.bg-warning`, `.bg-danger`
- Hovers: `.table-hover tbody tr:hover` (`#f0f4ff`)
- Fetch status: `.fetch-status-*`
- Nav: `.nav-link-custom`

Minor inline styles only for:
- Heatmap cell coloring (conditional `style` in Jinja)
- Search match highlight (`<mark>` tag)
- Side-by-side panel border (`border: 2px solid #0d6efd`)
- Grid layouts (`display: grid; grid-template-columns: ...`)

## Testing

New test file: `tests/unit/test_universe_compare.py`

| Test | What it verifies |
|------|-----------------|
| `test_compare_matrix_computation` | All per-company metrics computed correctly from known test data |
| `test_composite_score_grades` | Score formula produces correct A/B/C/D at boundary values |
| `test_category_filter_scopes_metrics` | SP category param limits metrics to matching edges only |
| `test_shared_sps_computation` | Shared SP counts and percentages correct |
| `test_alternatives_grouping` | Alternatives grouped by category, vendor dedup correct |
| `test_sector_averages` | Per-sector averages computed correctly |
| `test_side_by_side_overlap` | Overlap sets (shared_by_all, shared_by_some, unique) correct for 2-5 companies |
| `test_compare_route_renders` | `/compare` returns 200 with expected template |
| `test_compare_route_with_filters` | Filters compose correctly via query params |
| `test_side_by_side_api_validation` | Rejects <2 or >5 domains, returns 400 |
| `test_lockin_pct_edge_case` | Company with 0 SPs gets 0% lock-in, not division error |
| `test_xborder_unknown_country` | SPs with unknown country excluded from adequate/risky, counted as unknown |

All tests use `tmp_path` SQLite DB with controlled seed data. No real API calls.

## Scope boundaries

**In scope:**
- Everything described above — charts, matrix, side-by-side, all 7 insight dimensions, filters, search, sort, tooltips

**Out of scope (future extensions):**
- Privacy policy text comparison (requires scraping + storing policy content)
- CSV/PDF export of comparison data
- Saved comparison sets (bookmarking a set of 5 companies)
- Real-time WebSocket updates during crawl
