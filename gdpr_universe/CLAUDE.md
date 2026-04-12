# GDPR Universe — CLAUDE.md

Standalone subproject inside `gdpr_universe/`. Maps the subprocessor dependency graph for ~1,000 EU+UK listed companies.

## Commands

```bash
# Start the dashboard (port 5003)
.venv/bin/python -m gdpr_universe.app

# Run universe tests only
.venv/bin/pytest tests/unit/test_universe_*.py -v

# Import seed companies from CSV
.venv/bin/python -m gdpr_universe.seed_importer --csv seeds.csv

# Database stats
.venv/bin/python -m gdpr_universe.db --stats

# Crawl via CLI (uses Anthropic API — avoid unless needed)
.venv/bin/python -m gdpr_universe.crawl_scheduler --wave 0 --max-llm 500
```

All commands run from the project root (`gdpr-agent/`), not from `gdpr_universe/`.

## Architecture

SQLite database (`gdpr_universe/data/universe.db`, gitignored) with five tables: `companies`, `index_constituents`, `edges`, `fetch_log`, `analytics_cache`.

- `companies` — all entities (seeds + discovered SPs). `is_seed` distinguishes them.
- `edges` — one row per parent→child relationship. `depth` column: 0 = seed→SP, 1-4 = deeper layers. `source` column tracks origin.
- `fetch_log` — crawl history per domain.

**Shared modules** (imported from parent project, not copied):
- `contact_resolver.subprocessor_fetcher` — SP discovery pipeline
- `contact_resolver.service_categorizer` — domain classification
- `dashboard.services.jurisdiction` — risk assessment, country inference

## Data Population

Waves 0-4 were populated manually (Python scripts inserting directly into SQLite) using Claude Code's knowledge of major companies' published subprocessor lists — **no Anthropic API calls**. The `source` column in `edges` tracks origin:
- `manual_bulk` — wave 0 (seed → direct SPs)
- `manual_wave1` through `manual_wave4` — deeper layers

To add more data, use the same pattern: `ensure_company(domain, name, country)` + `add_edge(parent, child, depth)` in a Python script against `universe.db`.

Current totals: 42 seeds, 79 SPs, 622 edges across 5 depth layers.

## Seed Data Quality

All seed companies **must** have `hq_country_code` (ISO 2-letter) and `sector` populated. The dashboard filters, graph highlighting, and country stats depend on these fields. If importing from CSV, ensure these columns exist. If missing after import, populate manually.

## Dashboard (Flask, port 5003)

**Routes:** `/` (main dashboard), `/company/<domain>` (detail + neighborhood graph), `/contagion/<domain>` (blast radius), `/analytics` (6 insight panels), `/api/graph` (D3 JSON API), `/crawl` (trigger + status).

### Graph-Table Interaction (dashboard)

- **Single-click** a table row → highlights that company + all downstream SPs in the D3 graph (blue left-border on the row)
- **Double-click** a table row → navigates to `/company/<domain>`
- **Arrow link** (→) appears on row hover for direct navigation
- **Country/sector filter** dropdowns auto-submit and highlight all matching companies' downstream chains in the graph
- **Escape** clears all highlights (table + graph)
- **Mouse wheel** on graph scrolls the page normally (zoom only via +/- buttons) — deliberate design to prevent scroll hijacking

### Graph Visualization (`static/js/universe-graph.js`)

D3.js v7 force-directed graph. Two modes via `data-mode` attribute on `#graph-data` script tag:
- `full` (dashboard) — x-force layering by depth, click highlights chain, exposed via `window._universeGraph`
- `neighborhood` (company detail) — center force, click navigates to `/company/<domain>`

Dark background on graph cards for node/edge contrast against the light page theme.

**Exposed API** (`window._universeGraph`):
- `highlightDomain(domain)` — highlight one company + downstream SPs
- `highlightDomains([domains])` — highlight multiple companies + union of downstream chains
- `clearHighlight()` — reset to default view

### Graph Builder (`graph_builder.py`)

- `build_full_graph(engine)` — all seeds + SPs for dashboard
- `build_neighborhood_graph(engine, domain, hops=2)` — scoped for company detail
- Inline risk assessment via `_assess_risk()` / `_infer_country()`
- Output shape: `{nodes, edges, stats}`

## Key Constraints

- Zero changes to parent project code — shared modules imported read-only
- `gdpr_universe/data/universe.db` is gitignored
- `sys.path.insert` used in `routes/graph.py` to import `jurisdiction.py` — fragile but functional
- No Wikipedia table scraper yet — seed import only supports CSV
- `Subprocessor` Pydantic model lacks `service_category` field — adapter sets it on Company rows directly

## Testing

36+ tests in `tests/unit/test_universe_*.py`. Use `tmp_path` for isolated SQLite DBs. Crawl scheduler tests mock `_do_fetch`. Integration tests use `create_app(db_path)` with test client.
