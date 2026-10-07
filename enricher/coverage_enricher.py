"""
Coverage Enricher — reads a pytest --cov JSON report and extracts
per-file test coverage percentage.

Usage assumption: the caller has already run:
    pytest --cov=<src_dir> --cov-report=json --cov-report=term-missing

and the output file (.coverage.json or coverage.json) lives in repo root.
"""

from __future__ import annotations

import json
import os
import subprocess


_COVERAGE_FILENAMES = ["coverage.json", ".coverage.json"]


def _find_coverage_file(repo_path: str) -> str | None:
    for name in _COVERAGE_FILENAMES:
        candidate = os.path.join(repo_path, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def run_pytest_coverage(repo_path: str) -> bool:
    """
    Attempt to run pytest with coverage inside *repo_path*.
    Returns True on success, False if pytest is not available or fails.
    """
    output_path = os.path.join(repo_path, "coverage.json")
    cmd = [
        "python", "-m", "pytest",
        "--cov", repo_path,
        "--cov-report", f"json:{output_path}",
        "-q", "--tb=no",
    ]
    try:
        subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return os.path.isfile(output_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def build_coverage_map(repo_path: str) -> dict[str, float]:
    """
    Return a mapping:  absolute_file_path -> coverage_pct (0.0 – 100.0)

    Strategy:
      1. Look for an existing coverage.json in repo_path.
      2. If not found, try to run pytest --cov to generate one.
      3. If still not available, return an empty dict (issues get 0.0).
    """
    cov_file = _find_coverage_file(repo_path)
    if cov_file is None:
        print("[coverage_enricher] No coverage.json found — attempting to run pytest --cov ...")
        success = run_pytest_coverage(repo_path)
        if success:
            cov_file = _find_coverage_file(repo_path)
        else:
            print("[coverage_enricher] pytest --cov failed or timed out — defaulting to 0% coverage.")
            return {}

    if cov_file is None:
        return {}

    try:
        with open(cov_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}

    coverage_map: dict[str, float] = {}
    files_section = data.get("files", {})
    for filepath, stats in files_section.items():
        pct = stats.get("summary", {}).get("percent_covered", 0.0)
        # coverage.json paths are relative to where pytest ran (repo_path),
        # not to this process's cwd.
        if not os.path.isabs(filepath):
            filepath = os.path.join(repo_path, filepath)
        abs_path = os.path.normpath(os.path.abspath(filepath))
        coverage_map[abs_path] = round(pct, 1)

    return coverage_map
