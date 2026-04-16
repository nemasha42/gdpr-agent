"""Costs blueprint — LLM cost history and calculator."""

from __future__ import annotations

from flask import Blueprint, render_template

costs_bp = Blueprint("costs", __name__)


@costs_bp.route("/costs")
def costs():
    """LLM cost history and calculator."""
    from contact_resolver import cost_tracker

    records = cost_tracker.load_persistent_log()

    # Aggregate by model
    model_totals: dict[str, dict] = {}
    for r in records:
        m = r.get("model", "unknown")
        if m not in model_totals:
            model_totals[m] = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            }
        model_totals[m]["calls"] += 1
        model_totals[m]["input_tokens"] += r.get("input_tokens", 0)
        model_totals[m]["output_tokens"] += r.get("output_tokens", 0)
        model_totals[m]["cost_usd"] += r.get("cost_usd", 0.0)

    # Aggregate by purpose/source
    source_totals: dict[str, dict] = {}
    for r in records:
        src = r.get("purpose") or r.get("source") or "unknown"
        if src not in source_totals:
            source_totals[src] = {"calls": 0, "cost_usd": 0.0}
        source_totals[src]["calls"] += 1
        source_totals[src]["cost_usd"] += r.get("cost_usd", 0.0)

    # Compute averages per call for calculator defaults
    avg_resolver = 0.025
    avg_classifier = 0.010
    avg_schema = 0.080
    avg_subprocessor = 0.030

    resolver_calls = [
        r for r in records if "contact" in (r.get("purpose") or r.get("source") or "")
    ]
    classifier_calls = [
        r for r in records if "classif" in (r.get("purpose") or r.get("source") or "")
    ]
    schema_calls = [
        r for r in records if "schema" in (r.get("purpose") or r.get("source") or "")
    ]
    subprocessor_calls = [
        r
        for r in records
        if "subprocessor" in (r.get("purpose") or r.get("source") or "")
    ]

    if resolver_calls:
        avg_resolver = sum(r["cost_usd"] for r in resolver_calls) / len(resolver_calls)
    if classifier_calls:
        avg_classifier = sum(r["cost_usd"] for r in classifier_calls) / len(
            classifier_calls
        )
    if schema_calls:
        avg_schema = sum(r["cost_usd"] for r in schema_calls) / len(schema_calls)
    if subprocessor_calls:
        avg_subprocessor = sum(r["cost_usd"] for r in subprocessor_calls) / len(
            subprocessor_calls
        )

    grand_total = sum(r.get("cost_usd", 0.0) for r in records)

    return render_template(
        "costs.html",
        records=list(reversed(records[-200:])),  # most recent first, cap display
        model_totals=model_totals,
        source_totals=source_totals,
        grand_total=grand_total,
        total_calls=len(records),
        avg_resolver=avg_resolver,
        avg_classifier=avg_classifier,
        avg_schema=avg_schema,
        avg_subprocessor=avg_subprocessor,
    )
