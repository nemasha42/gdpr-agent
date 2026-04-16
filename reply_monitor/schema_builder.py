"""LLM-powered schema analysis for GDPR data exports.

Uses a two-layer approach:
  1. Pure-Python preprocessor extracts structural metadata (folder tree,
     CSV headers, JSON key paths, record counts, Twitter JS unwrapping).
  2. Enriched LLM prompt receives pre-extracted metadata and focuses on
     understanding the data, not parsing it.

Output follows the dataowners.org card format with enhancements:
  - structure_type: "object" | "array" per category
  - record_count: number of records per category
  - provenance: "provided" | "observed" | "derived" | "inferred" per category and field
  - sensitive: boolean per field (Article 9 special category data)
"""

from __future__ import annotations

import json
from pathlib import Path

from reply_monitor.preprocessor import (
    PreprocessResult,
    build_context_summary,
    preprocess,
)

_MAX_CONTEXT_BYTES = 60_000


def build_schema(file_path: Path, api_key: str, company_name: str = "") -> dict:
    """Analyze a data export file and return an LLM-inferred schema.

    Args:
        file_path:    Path to the downloaded file (ZIP, JSON, CSV, JS)
        api_key:      Anthropic API key
        company_name: Company name for cost tracking (falls back to file stem)

    Returns:
        Dict with keys: categories, services, export_meta.
        Returns empty dict on failure or missing api_key.
    """
    if not api_key:
        return {}

    pp = preprocess(file_path)

    if not pp.file_samples:
        return {}

    return _call_llm(pp, api_key, company_name=company_name or file_path.stem)


def _call_llm(pp: PreprocessResult, api_key: str, *, company_name: str = "") -> dict:
    """Send preprocessed context + file samples to Claude and parse the returned schema."""
    try:
        import anthropic
    except ImportError:
        return {}

    context_summary = build_context_summary(pp)

    max_per_file = min(2000, _MAX_CONTEXT_BYTES // max(len(pp.file_samples), 1))
    file_blocks = "\n".join(
        f"=== {s['filename']} ===\n{s['content'][:max_per_file]}"
        for s in pp.file_samples
    )

    prompt = f"""You are analyzing a GDPR Subject Access Request (SAR) data export.

STRUCTURAL CONTEXT (pre-extracted, accurate):
{context_summary}

FILE SAMPLES (first ~2000 chars per file):
{file_blocks}

Produce a structured data schema describing the personal data held.
Return a JSON object — no markdown, no explanation, only valid JSON.

Schema format:
{{
  "categories": [
    {{
      "name": "Human-readable category name (e.g. Profile, Streaming History, Search Queries)",
      "description": "One sentence: what personal data this category contains",
      "structure_type": "object | array",
      "record_count": 0,
      "provenance": "provided | observed | derived | inferred",
      "fields": [
        {{
          "name": "field_name_from_data",
          "type": "string | string/date | string/date-time | string/email | integer | number | boolean | array | object",
          "example": "actual example value from the file samples",
          "description": "Short description of what this field means to the user",
          "sensitive": false,
          "provenance": "provided | observed | derived | inferred"
        }}
      ]
    }}
  ],
  "services": [
    {{
      "name": "Name of one product/service the company offers",
      "description": "One sentence describing what this service does for the user"
    }}
  ],
  "export_meta": {{
    "format": "ZIP | JSON | CSV | etc.",
    "formats_found": ["json", "csv"],
    "delivery": "How the data was delivered (e.g. Download link, Email attachment)",
    "timeline": "How long it took or the statutory period (e.g. Provided within 30 days)",
    "structure": "One sentence: how files are organized (e.g. Organized by service in folders)",
    "total_files": {pp.total_files},
    "total_records_estimate": {pp.total_records_estimate}
  }}
}}

Rules:
- Use clear, human-readable category names
- One category per logical data group (not one per file)
- Include only the 5-8 most important / personal fields per category
- Use real values from the samples as examples
- structure_type: "array" for lists of records (streaming history, search queries), "object" for single records (profile, settings)
- record_count: use the pre-extracted counts from STRUCTURAL CONTEXT; 0 if unknown
- provenance per category and field:
  - "provided" = user directly supplied this (name, email, address, preferences)
  - "observed" = collected through usage (streaming history, search queries, login times, IP addresses)
  - "derived" = calculated from other data (total spend, account age, usage statistics)
  - "inferred" = predicted/profiled (interest categories, recommendations, ad targeting segments)
- sensitive: true for Article 9 special category data (health, biometric, genetic, racial/ethnic, political, religious, trade union, sexual orientation) and criminal data; false otherwise
- types: use "string/date" for dates, "string/date-time" for timestamps, "string/email" for emails, "integer" for whole numbers
- services: list the distinct products/features the company's export reveals
- export_meta: use pre-extracted file counts and record estimates; infer format, delivery, timeline from context
- Return ONLY the JSON object"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        if isinstance(result, list):
            result = {"categories": result, "services": [], "export_meta": {}}
        if isinstance(result, dict):
            # Ensure new fields have defaults for any the LLM missed
            for cat in result.get("categories", []):
                cat.setdefault("structure_type", "object")
                cat.setdefault("record_count", 0)
                cat.setdefault("provenance", "provided")
                for field in cat.get("fields", []):
                    field.setdefault("sensitive", False)
                    field.setdefault("provenance", cat.get("provenance", "provided"))

            meta = result.setdefault("export_meta", {})
            meta.setdefault("formats_found", pp.formats_found)
            meta.setdefault("total_files", pp.total_files)
            meta.setdefault("total_records_estimate", pp.total_records_estimate)

            try:
                from contact_resolver import cost_tracker

                cost_tracker.record_llm_call(
                    company_name=company_name,
                    input_tokens=msg.usage.input_tokens,
                    output_tokens=msg.usage.output_tokens,
                    model="claude-haiku-4-5-20251001",
                    found=bool(result),
                    source="schema_builder",
                    purpose="Received data schema analysis",
                )
            except Exception:
                pass  # cost tracking failure should not lose the schema result
            return result
    except Exception as exc:
        print(f"[schema_builder] LLM call failed: {exc}")
    return {}
