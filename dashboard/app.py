"""Flask dashboard for GDPR SAR monitoring.

All routes are registered via blueprints in dashboard/__init__.py (create_app).
This file is the entry point for ``python dashboard/app.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow OAuth over HTTP for local development (localhost)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# Ensure project root is on path when run directly
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env so ANTHROPIC_API_KEY is available for schema analysis
try:
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from dashboard import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)
