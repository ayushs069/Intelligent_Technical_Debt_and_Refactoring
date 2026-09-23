"""
Configuration for the Technical Debt Analyzer.

Default settings for Phase 1 — Static Analysis Foundation.
"""

import os

# ----- Target repository -----
# Override via CLI: python run_analysis.py --target /path/to/repo
TARGET_REPO_PATH = os.environ.get("TECHDEBT_TARGET", ".")

# ----- Output -----
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "debt_dataset.json")

# ----- Tool toggles (all enabled by default) -----
ENABLE_RADON = True
ENABLE_RUFF = True
ENABLE_PYLINT = True
ENABLE_VULTURE = True
