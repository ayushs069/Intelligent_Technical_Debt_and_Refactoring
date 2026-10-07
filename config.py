"""
Configuration for the Technical Debt Analyzer.

Phase 1–3 settings plus the research pipeline (dataset, LLM, evaluation,
refactoring).  Secrets such as ANTHROPIC_API_KEY belong in a git-ignored .env
file, never here.
"""

import os
import re

try:  # optional: load .env (ANTHROPIC_API_KEY, TECHDEBT_* overrides)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
except ImportError:
    pass

# ----- Target repository -----
# Override via CLI: python run_analysis.py --target /path/to/repo
TARGET_REPO_PATH = os.environ.get("TECHDEBT_TARGET", ".")

# ----- Output -----
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "debt_dataset.json")
REPORTS_DIR = os.path.join(PROJECT_DIR, "reports")

# ----- Tool toggles (all enabled by default) -----
ENABLE_RADON = True
ENABLE_RUFF = True
ENABLE_PYLINT = True
ENABLE_VULTURE = True

# ----- Research dataset (temporal split) -----
DEFAULT_REPO_NAME = os.environ.get("TECHDEBT_REPO", "requests")
DEFAULT_AS_OF = os.environ.get("TECHDEBT_AS_OF", "2024-01-01")
DEFAULT_HISTORY_SINCE = os.environ.get("TECHDEBT_HISTORY_SINCE", "2019-01-01")

# ----- LLM (Phase 4 / 6) -----
LLM_MODEL = os.environ.get("TECHDEBT_LLM_MODEL", "gpt-4.1-mini" if os.environ.get("OPENAI_API_KEY") else "claude-sonnet-4-6")
LLM_EFFORT = os.environ.get("TECHDEBT_LLM_EFFORT", "medium")
LLM_CACHE_DIR = os.path.join(DATA_DIR, "llm_cache")
RAG_TOP_K = 5

# ----- Evaluation (Phase 5) -----
EXPERT_DIR = os.path.join(PROJECT_DIR, "evaluation", "expert_rankings")
EXPERT_SAMPLE_SIZE = 30
PRECISION_KS = (5, 10, 20)

# ----- Refactoring (Phase 6) -----
REFACTOR_TOP_N = 5
REFACTOR_MAX_ATTEMPTS = 2
BRANCH_PREFIX = "ai-refactoring"


def dataset_paths(repo_name: str = DEFAULT_REPO_NAME) -> dict[str, str]:
    """Canonical file locations for one target repository."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", repo_name):
        raise ValueError("Repository name must contain only letters, numbers, underscores, dots or hyphens.")
    return {
        "items": os.path.join(DATA_DIR, f"{repo_name}_items.json"),
        "ground_truth": os.path.join(DATA_DIR, f"{repo_name}_ground_truth.json"),
        "meta": os.path.join(DATA_DIR, f"{repo_name}_dataset_meta.json"),
        "llm_rankings": os.path.join(DATA_DIR, f"{repo_name}_llm_rankings.json"),
        "llm_rankings_nocontext": os.path.join(DATA_DIR, f"{repo_name}_llm_rankings_nocontext.json"),
        "static_rankings": os.path.join(DATA_DIR, f"{repo_name}_static_rankings.json"),
        "expert_sample": os.path.join(DATA_DIR, f"{repo_name}_expert_sample.json"),
        "evaluation": os.path.join(DATA_DIR, f"{repo_name}_evaluation_results.json"),
        "refactoring": os.path.join(DATA_DIR, f"{repo_name}_refactoring_results.json"),
    }
