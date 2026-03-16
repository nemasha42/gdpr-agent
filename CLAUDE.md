# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all unit tests
python -m pytest tests/unit/ -q

# Run a single test file
python -m pytest tests/unit/test_resolver.py -v

# Run a single test by name
python -m pytest tests/unit/test_resolver.py::TestContactResolver::test_cache_hit -v

# Full pipeline (real Gmail + sending)
python run.py

# Full pipeline — preview only, nothing sent
python run.py --dry-run

# Smoke test Phase 4 letter engine interactively
python test_phase4.py --dry-run
```

## Architecture

The pipeline runs in four stages:

```
Gmail inbox → service detection → contact resolution → SAR letter send
```

**Stage 1 — Scan** (`scanner/`): `inbox_reader.py` fetches email headers only (no body, `gmail.readonly` scope). `service_extractor.py` classifies senders as HIGH/MEDIUM/LOW confidence services and deduplicates by domain. `company_normalizer.py` maps raw domains to display names (strips noise subdomains, handles co.uk/com.au, hardcoded exceptions for t.co → Twitter/X etc.).

**Stage 2 — Resolve** (`contact_resolver/`): `resolver.py` runs a 5-step chain per domain, stopping at first success:
1. Local cache `data/companies.json` — TTLs: datarequests/overrides=180d, scrape/llm=90d
2. `data/dataowners_overrides.json` — hand-curated records
3. datarequests.org via GitHub API — open-source GDPR DB, matches on domain root + company name words, verifies against company's `runs` array
4. `privacy_page_scraper.py` — tries `/privacy-policy`, `/privacy`, `/legal/privacy`, `/gdpr`; regex-extracts `privacy@`/`dpo@` emails and DSAR portal URLs
5. `llm_searcher.py` — Claude Haiku with `web_search_20250305` tool, `max_uses=1`, ~$0.025/call

All successful lookups are written back to `data/companies.json`. `cost_tracker.py` records every LLM call and prints a summary table at end of run.

**Stage 3 — Compose** (`letter_engine/`): `composer.py` fills `templates/sar_email.txt` or `templates/sar_postal.txt` based on `CompanyRecord.contact.preferred_method`. `sender.py` prints a preview, prompts Y/N, then sends via Gmail API (`gmail.send` scope, separate `user_data/token_send.json`) or prints instructions for portal/postal. `tracker.py` logs sent letters to `user_data/sent_letters.json`.

## Key constraints

- `data/companies.json` stores public contact info only — committed to repo, never contains PII
- `user_data/` is gitignored — contains OAuth tokens and the sent letters log
- Gmail uses two separate OAuth tokens: `token.json` (readonly, scanning) and `token_send.json` (send, created on first letter send)
- LLM is last resort only — free lookup paths always attempted first
- `record_llm_call()` must be called **after** JSON extraction so `found=` reflects the actual result

## Data models

`contact_resolver/models.py` is the source of truth. `CompanyRecord` is the central type — flows from resolver through composer into `SARLetter`. `preferred_method` on `Contact` drives which template and dispatch path is used (`email` / `portal` / `postal`).

## Testing

All tests in `tests/unit/` use dependency injection or `unittest.mock` — no real network, Gmail, or Anthropic calls. `ContactResolver` accepts injectable `http_get`, `llm_search`, and `privacy_scrape` callables. Mock Anthropic responses must set `response.usage.input_tokens` and `response.usage.output_tokens` as integers (not MagicMock auto-attributes) or cost recording will fail.
