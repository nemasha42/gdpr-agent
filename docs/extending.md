# Extending the System

> Back to @ARCHITECTURE.md for the system overview.

---

## Adding a new resolver step

The resolver chain in `resolver.py` is explicit — it is not a plugin system. Steps are hardcoded in `ContactResolver.resolve()`. To add a step (e.g. a DuckDuckGo scraper as a free step before the LLM):

1. Write the lookup function in a new file under `contact_resolver/`, returning `CompanyRecord | None`. Accept `domain` and `company_name` as arguments.
2. Inject it as a callable in `ContactResolver.__init__()` (follow the `http_get`, `privacy_scrape`, `llm_search` pattern — this enables test injection).
3. Insert the call in `ContactResolver.resolve()` between the existing steps, calling `cost_tracker.record_resolver_result("your_source_name")` on success.
4. Add the new `source` literal to the `Literal[...]` type in `CompanyRecord.source` in `models.py`.
5. Add its TTL in `_STALENESS_DAYS` in `resolver.py`.
6. Write tests in `tests/unit/test_resolver.py` following the existing pattern — inject a mock callable and assert the correct fallthrough behaviour.

Do not change the source list in `CompanyRecord.source` without understanding that `data/companies.json` contains serialised records with old source values — Pydantic will reject records with unknown source literals on load.

---

## Adding a new classifier tag

Tags are defined in three places that must stay in sync:

1. `reply_monitor/models.py` — the `REPLY_TAGS` list (displayed in the dashboard)
2. `reply_monitor/classifier.py` — add a regex rule to `_RULES` (a list of `(tag, [(field, pattern), ...])` tuples), or describe the tag in `_llm_classify()`'s prompt
3. `reply_monitor/state_manager.py` — decide whether the new tag is terminal (`_TERMINAL_TAGS`), action-requiring (`_ACTION_TAGS`), or acknowledging (`_ACK_TAGS`), and add it accordingly

If the tag represents a terminal state, verify that `compute_status()` priority logic handles it correctly. Write tests in `test_reply_classifier.py` covering at least the regex path and the NON_GDPR interaction.

---

## Adding a new letter template

Templates are `letter_engine/templates/sar_email.txt` and `sar_postal.txt`. The available substitution variables are defined in `composer.py`. To add a new variable:

1. Add the placeholder `{variable_name}` to the template.
2. Add the substitution in `composer.py`'s `_fill_template()` function.
3. Ensure the value is available from either `settings` or the `CompanyRecord` — do not introduce new dependencies.
4. Update `test_letter_engine.py` to verify the substitution.

If you want a third template type (e.g. a GDPR erasure request rather than a SAR), you would need to add a new `preferred_method` value to `Contact.preferred_method`'s `Literal` type and update `sender.py`'s dispatch logic.

---

## Changing the LLM model

All six call sites hard-code `"claude-haiku-4-5-20251001"`. To change the model:

1. Update the model string at each call site.
2. Update `_PRICING` in `cost_tracker.py` with the new model's pricing.
3. Update `CLAUDE.md` to reflect the change.
4. Note that `web_search_20250305` tool compatibility must be verified for new model versions — it is an Anthropic-specific tool and may behave differently across model generations.

The `llm_searcher.py` call site uses the `web_search_20250305` tool, which is only available on certain models. If you switch to a model that does not support this tool, the API call will fail with an error and the function will return `None`.

---

## Scaling to 500+ companies

The primary bottlenecks at scale are:

1. **GitHub API rate limit (60 req/hour unauthenticated):** Add `GITHUB_TOKEN` to `.env` and pass it as a `Bearer` token header in `_fetch_dir_listing()`.
2. **Sequential resolver:** Wrap the resolve loop in `run.py` with `concurrent.futures.ThreadPoolExecutor` — the resolver is I/O-bound and safe to parallelize because each domain resolves independently. Limit concurrency to avoid hammering GitHub and privacy pages simultaneously.
3. **LLM call cost:** Pre-populate `data/dataowners_overrides.json` with well-known services (20 entries are already included). Every entry saved there saves ~$0.025 permanently. Use `--max-llm-calls N` to cap costs on any given run.
4. **Interactive Y/N prompt:** The current design requires a human to approve each letter. For bulk runs, consider adding a `--auto-send` flag that skips the prompt (with appropriate safety warnings).
