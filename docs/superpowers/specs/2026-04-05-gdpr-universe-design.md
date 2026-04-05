# GDPR Universe — Design Spec

A standalone web application that maps the subprocessor dependency graph for EU+UK listed companies under GDPR, revealing data flow interconnections, concentration risks, and contagion paths across the European economy.

## 1. Goals

1. **Map the EU+UK data processing ecosystem** starting with ~1,000 listed companies from major stock indices, expanding organically as subprocessors are discovered.
2. **Surface business intelligence**: which companies depend on which subprocessors, sharing counts ("Stripe is used by 847 companies"), customer insight.
3. **Trace contagion paths**: if a subprocessor is breached or non-compliant, instantly show every company (and chain) affected.
4. **Assess concentration risk**: identify single points of failure where a handful of SPs dominate a processing category.
5. **Evaluate jurisdiction risk**: flag data transfers to non-adequate countries without proper safeguards.
6. **Measure chain depth**: how many layers deep does personal data travel before reaching a risky jurisdiction?

## 2. Architecture

### 2.1 Project Structure

```
gdpr_universe/
├── app.py                    # Flask app, port 5002
├── db.py                     # SQLAlchemy models + SQLite setup
├── adapters.py               # Bridges shared modules → SQLite writes
├── seed_importer.py          # Parse index constituent lists → companies table
├── graph_queries.py          # Recursive CTEs: blast radius, contagion, concentration
├── analytics.py              # Aggregate stats, precomputed cache refresh
├── crawl_scheduler.py        # Wave-based SP fetching with rate limiting
├── routes/
│   ├── dashboard.py          # GET / — main table view with filters + stats
│   ├── company.py            # GET /company/<domain> — detail + local graph
│   ├── graph.py              # GET /graph — neighborhood graph API (D3 JSON)
│   ├── contagion.py          # GET /contagion/<domain> — blast radius trace
│   ├── analytics_routes.py   # GET /analytics — insights dashboard
│   └── crawl.py              # POST /crawl — trigger fetch waves
├── templates/
├── static/
└── data/
    └── universe.db           # SQLite database (gitignored)
```

### 2.2 Relationship to Main Project

Lives inside the `gdpr-agent` repo as a sibling directory. Shares modules via Python imports:

| Shared Module | What It Provides |
|---------------|-----------------|
| `contact_resolver.subprocessor_fetcher` | 4-stage SP discovery pipeline |
| `contact_resolver.service_categorizer` | Domain → service category classification |
| `dashboard.services.jurisdiction` | `assess_risk()`, `infer_country_code()`, adequacy country lists |
| `contact_resolver.models` | `Subprocessor`, `SubprocessorRecord` types |

### 2.3 Deployment

- **Phase 1 (now):** Local Flask app on port 5002
- **Phase 2 (later):** Public deployment. SQLite → Postgres via SQLAlchemy.

## 3. Database Schema

SQLite with five tables: `companies`, `index_constituents`, `edges`, `fetch_log`, `analytics_cache`.

Key design: explicit `edges` table enables recursive CTE graph queries (blast radius, contagion paths, concentration risk).

## 4. Seed Import Pipeline

Free stock index constituent lists (FTSE 350, EURO STOXX 600, SMI, etc.) → ~1,000 unique companies after dedup.

## 5. Crawl Scheduler

Wave-based: Wave 0 (seeds) → Wave 1 (discovered SPs) → Wave 2+ (sub-SPs). Rate-limited, resumable, cost-capped.

## 6. Dashboard Views

Tabular-first with graph drill-down: main table, company detail, neighborhood graph, blast radius/contagion, analytics (6 insight panels).
