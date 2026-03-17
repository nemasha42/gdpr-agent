"""Standalone LLM cost calculator — Flask app on port 5002.

Reads user_data/cost_log.json and presents three views:
  /           — overview: summary stats + full call log (newest first)
  /by-company — calls grouped by company, sorted by total cost desc
  /by-model   — calls grouped by model
"""

import sys
from pathlib import Path

# Ensure project root is importable when run directly
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from collections import defaultdict

from flask import Flask, render_template

from contact_resolver.cost_tracker import load_persistent_log

app = Flask(__name__)

# Human-readable labels for old log records that predate the `purpose` field
_SOURCE_LABELS: dict[str, str] = {
    "contact_resolver": "GDPR contact address lookup",
    "reply_classifier": "Reply email classification",
    "schema_builder":   "Received data schema analysis",
}


def _purpose(record: dict) -> str:
    """Return a human-readable purpose string for a log record."""
    return record.get("purpose") or _SOURCE_LABELS.get(record.get("source", ""), record.get("source", ""))


def _load() -> list[dict]:
    """Load log and inject a resolved purpose into each record."""
    records = load_persistent_log()
    for r in records:
        r["_purpose"] = _purpose(r)
    return records


@app.route("/")
def index():
    calls = list(reversed(_load()))  # newest first

    total_cost = sum(r.get("cost_usd", 0.0) for r in calls)
    total_calls = len(calls)
    total_input = sum(r.get("input_tokens", 0) for r in calls)
    total_output = sum(r.get("output_tokens", 0) for r in calls)

    resolver_cost = sum(r.get("cost_usd", 0.0) for r in calls if r.get("source") == "contact_resolver")
    classifier_cost = sum(r.get("cost_usd", 0.0) for r in calls if r.get("source") == "reply_classifier")
    schema_cost = sum(r.get("cost_usd", 0.0) for r in calls if r.get("source") == "schema_builder")

    stats = {
        "total_cost": total_cost,
        "total_calls": total_calls,
        "total_input": total_input,
        "total_output": total_output,
        "resolver_cost": resolver_cost,
        "classifier_cost": classifier_cost,
        "schema_cost": schema_cost,
    }

    return render_template("index.html", calls=calls, stats=stats)


@app.route("/by-company")
def by_company():
    calls = _load()

    groups: dict[str, dict] = defaultdict(lambda: {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "sources": set(),
    })

    for r in calls:
        name = r.get("company_name") or "unknown"
        g = groups[name]
        g["calls"] += 1
        g["input_tokens"] += r.get("input_tokens", 0)
        g["output_tokens"] += r.get("output_tokens", 0)
        g["cost_usd"] += r.get("cost_usd", 0.0)
        src = r.get("source", "")
        if src:
            g["sources"].add(src)

    # Convert sets to sorted strings for template
    rows = [
        {
            "company_name": name,
            "calls": g["calls"],
            "input_tokens": g["input_tokens"],
            "output_tokens": g["output_tokens"],
            "cost_usd": g["cost_usd"],
            "sources": ", ".join(sorted(g["sources"])),
        }
        for name, g in sorted(groups.items(), key=lambda x: x[1]["cost_usd"], reverse=True)
    ]

    return render_template("by_company.html", rows=rows)


@app.route("/by-model")
def by_model():
    calls = _load()

    groups: dict[str, dict] = defaultdict(lambda: {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    })

    for r in calls:
        model = r.get("model") or "unknown"
        g = groups[model]
        g["calls"] += 1
        g["input_tokens"] += r.get("input_tokens", 0)
        g["output_tokens"] += r.get("output_tokens", 0)
        g["cost_usd"] += r.get("cost_usd", 0.0)

    rows = [
        {
            "model": model,
            "calls": g["calls"],
            "input_tokens": g["input_tokens"],
            "output_tokens": g["output_tokens"],
            "cost_usd": g["cost_usd"],
            "avg_cost": g["cost_usd"] / g["calls"] if g["calls"] else 0.0,
        }
        for model, g in sorted(groups.items(), key=lambda x: x[1]["cost_usd"], reverse=True)
    ]

    return render_template("by_model.html", rows=rows)


if __name__ == "__main__":
    app.run(port=5002, debug=True)
