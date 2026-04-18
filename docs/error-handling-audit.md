# Error Handling Audit

Audited: 2026-04-18
Scope: All Python files in the codebase

## Summary

- **0 bare `except:` blocks** -- all use typed exceptions
- **P1 (data loss / misleading):** 6 issues
- **P2 (hidden actionable errors):** 14 issues
- **P3 (acceptable):** ~25 blocks
- All `requests` calls are wrapped in try/except
- No unprotected network calls found

## P1 -- Silent failures that could lose data or mislead the user

| # | File:Line | Issue | Fix |
|---|-----------|-------|-----|
| 1 | `reply_monitor/fetcher.py:86` | `except Exception: return []` -- Gmail thread fetch fails silently. User sees "no replies" instead of an error. | Log warning: `print(f"[fetcher] thread {thread_id} fetch failed: {exc}")` |
| 2 | `contact_resolver/resolver.py:227` | `except Exception: return CompaniesDB()` -- If `companies.json` is corrupted, silently returns empty DB. All cached contacts vanish for one run. | Log: `print(f"[resolver] Warning: companies.json parse error: {exc}")` |
| 3 | `contact_resolver/resolver.py:423` | Same pattern on write-path -- corrupted file silently reset to empty before merge-write. | Same fix |
| 4 | `dashboard/app.py:1900` | `except Exception: pass` -- `promote_latest_attempt()` failure silently skips state merging. Dashboard undercounts companies. | Log: `print(f"[dashboard] promote_latest_attempt failed for {account}: {exc}")` |
| 5 | `contact_resolver/llm_searcher.py:104` | `except anthropic.APIError: return None` -- API errors (rate limit, auth) silently swallowed. No cost tracking, no user feedback. | Log: `print(f"[llm_search] API error for {domain}: {exc}")` |
| 6 | `contact_resolver/llm_searcher.py:215` | `except Exception: return None` -- Entire LLM search call fails silently. | Same |

## P2 -- Hides actionable information from operator

| # | File:Line | Issue | Fix |
|---|-----------|-------|-----|
| 7 | `reply_monitor/fetcher.py:159` | `except Exception: continue` -- Individual message fetch failure hidden. Replies silently dropped. | Log: `print(f"[fetcher] message {msg_id} fetch failed: {exc}")` |
| 8 | `reply_monitor/fetcher.py:181` | `except Exception: break` -- Pagination failure truncates results silently. | Log before break |
| 9 | `reply_monitor/classifier.py:653` | `except Exception: return ""` -- LLM summary generation failure hidden. | Log: `print(f"[classifier] summary LLM call failed: {exc}")` |
| 10 | `reply_monitor/classifier.py:740` | `except Exception: return None` -- LLM JSON parse failure hidden. | Log the raw text that failed to parse |
| 11 | `reply_monitor/attachment_handler.py:74` | `except Exception: return None` -- Gmail attachment download fails silently. User never learns attachment was lost. | Log: `print(f"[attachment] download failed for {attachment_id}: {exc}")` |
| 12 | `contact_resolver/resolver.py:276` | `except Exception: return None` -- Dataowners override load fails silently. | Log |
| 13 | `contact_resolver/resolver.py:307` | `except Exception: return None` -- GitHub API dir listing fails silently. Could be rate-limit. | Log |
| 14 | `scanner/inbox_reader.py:18` | `except Exception: return 0` -- Gmail profile fetch fails silently. | Log |
| 15 | `contact_resolver/subprocessor_fetcher.py:270` | `except Exception: return ""` -- Playwright fallback fails silently. | Log |
| 16 | `dashboard/app.py:369` | `except Exception: pass` -- Account discovery from state files fails silently. | Log |
| 17 | `dashboard/app.py:1929` | `except Exception: reply_states = {}` -- State load failure hidden from pipeline route. | Log |
| 18 | `dashboard/app.py:2301` | `except Exception: pass` -- Pipeline monitor status count fails silently. | Log |
| 19 | `dashboard/app.py:2325` | `except Exception: pass` -- Scan cooldown check fails silently. | Log |
| 20 | `contact_resolver/cost_tracker.py:93` | `except Exception: return []` -- Cost log read fails silently. | Log |

## P3 -- Acceptable or minor

| # | File:Line | Why acceptable |
|---|-----------|---------------|
| -- | `fetcher.py:132` | `except ValueError: pass` -- Date parse fallback, uses no filter instead. Safe. |
| -- | `fetcher.py:241` | Base64 decode fallback returns empty string. Defensive. |
| -- | `fetcher.py:349` | Date parse fallback to `now()`. Reasonable. |
| -- | `state_manager.py:65,92` | `except (JSONDecodeError, OSError)` -- Typed, expected on fresh install. |
| -- | `state_manager.py:165` | `except (ValueError, AttributeError): pass` -- Deadline parse fallback. |
| -- | `state_manager.py:206,220` | Deadline calc returns safe defaults (30 days). |
| -- | `app.py:334` | Flag emoji -- cosmetic, acceptable. |
| -- | `app.py:507,1549,1630,1875,2153,2200` | Companies.json reads -- 6 identical patterns returning `{}`. Should be a single helper (DRY issue, not error handling). |
| -- | `app.py:2121,2224,2449` | `except Exception: continue` -- Skipping invalid records in loops. Expected for partial data. |
| -- | `app.py:2256` | Returns `True` (stale) on failure -- safe, triggers re-fetch. |
| -- | `app.py:2761` | SSE queue timeout -- expected control flow. |
| -- | All `except ImportError: pass` | Optional dependency guards (dotenv, playwright). Standard pattern. |
| -- | `preprocessor.py` (4 blocks) | File parsing fallbacks -- defensive, returns partial results. |
| -- | `captcha_relay.py:79` | Cleanup -- acceptable to ignore. |
| -- | `submitter.py:228,245,254` | Screenshot/parse best-effort -- non-critical. |
| -- | `form_analyzer.py:103,131` | Typed (`ValueError`, `JSONDecodeError`) -- correct. |
| -- | `letter_engine/tracker.py:81` | Typed (`JSONDecodeError, OSError`) -- correct. |

## Recommendation

All 20 P1/P2 issues follow the same pattern: `except Exception` with no logging. The fix is the same for all -- capture `as exc` and add a single `print(f"[module] context: {exc}")` line. No structural changes needed.
