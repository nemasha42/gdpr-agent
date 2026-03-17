#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Activate virtualenv if present
if [[ -f ".venv/bin/activate" ]]; then
    source ".venv/bin/activate"
elif [[ -f "venv/bin/activate" ]]; then
    source "venv/bin/activate"
fi

PYTHON="${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python3}"
PYTHON="${PYTHON:-python3}"

echo ""
echo "=== GDPR Agent — Fresh Start ==="
echo ""
echo "This will delete:"
echo "  • LLM contact cache        (reset_cache.py)"
echo "  • Sent letters log         (user_data/sent_letters.json)"
echo "  • Reply state              (user_data/reply_state.json + .bak)"
echo "  • Cost log                 (user_data/cost_log.json)"
echo "  • Received data exports    (user_data/received/*)"
echo ""
echo "This will NOT touch:"
echo "  • OAuth tokens             (user_data/tokens/)"
echo "  • Hand-curated overrides   (data/dataowners_overrides.json)"
echo ""
read -rp "This will wipe all previous run data. Continue? [y/N] " answer
if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "--- Deleting state ---"

"$PYTHON" reset_cache.py
echo "  ✓ Contact cache cleared"

rm -f user_data/sent_letters.json
echo "  ✓ user_data/sent_letters.json"

rm -f user_data/reply_state.json user_data/reply_state.json.bak
echo "  ✓ user_data/reply_state.json (+ .bak)"

rm -f user_data/cost_log.json
echo "  ✓ user_data/cost_log.json"

rm -rf user_data/received/*
echo "  ✓ user_data/received/*"

echo ""
echo "--- All state wiped. Launching pipeline ---"
echo ""
"$PYTHON" run.py
