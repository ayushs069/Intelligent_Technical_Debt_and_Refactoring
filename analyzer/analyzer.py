"""
Analyzer Orchestrator — run all static-analysis tools,
normalise results, save JSON, and print a summary.
"""

from __future__ import annotations

import json
import os
from collections import Counter

from analyzer import radon_analyzer, ruff_analyzer, pylint_analyzer, vulture_analyzer
from analyzer.normalizer import normalize


import shutil
import subprocess
import tempfile


def is_url(target: str) -> bool:
    """Check if target string is a Git/HTTP URL."""
    target_lower = target.lower()
    return target_lower.startswith("http://") or target_lower.startswith("https://") or target_lower.startswith("git@") or target_lower.endswith(".git")


def extract_repo_name(target: str) -> str:
    """Extract repo name from a local path or Git URL."""
    clean_target = target.rstrip("/")
    if clean_target.endswith(".git"):
        clean_target = clean_target[:-4]
    if is_url(target):
        name = os.path.basename(clean_target)
    else:
        name = os.path.basename(os.path.abspath(clean_target))
    return name or "repository"


def run_all(target_path: str) -> tuple[list[dict], str]:
    """
    Run every enabled analyzer on *target_path* (local path or GitHub URL)
    and return a tuple of (normalised issues, repo_name).
    """
    temp_dir = None
    actual_path = target_path
    repo_name = extract_repo_name(target_path)

    if is_url(target_path):
        temp_dir = tempfile.mkdtemp(prefix=f"techdebt_{repo_name}_")
        print(f"\n[git] Cloning remote repository {target_path} into temporary directory...")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", target_path, temp_dir],
                check=True,
                capture_output=True,
                text=True,
            )
            actual_path = temp_dir
        except subprocess.CalledProcessError as exc:
            print(f"[git] Error cloning repository: {exc.stderr}")
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to clone repository: {target_path}") from exc

    try:
        print(f"\n{'='*60}")
        print(f"  Technical Debt Analyzer — Phase 1")
        print(f"  Repository : {repo_name}")
        print(f"  Target     : {os.path.abspath(actual_path)}")
        print(f"{'='*60}\n")

        raw_issues: list[dict] = []

        # --- Radon (complexity + maintainability) ---
        print("[1/4] Running Radon …")
        radon_results = radon_analyzer.run(actual_path)
        print(f"       Found {len(radon_results)} issue(s)")
        raw_issues.extend(radon_results)

        # --- Ruff ---
        print("[2/4] Running Ruff …")
        ruff_results = ruff_analyzer.run(actual_path)
        print(f"       Found {len(ruff_results)} issue(s)")
        raw_issues.extend(ruff_results)

        # --- Pylint ---
        print("[3/4] Running Pylint …")
        pylint_results = pylint_analyzer.run(actual_path)
        print(f"       Found {len(pylint_results)} issue(s)")
        raw_issues.extend(pylint_results)

        # --- Vulture ---
        print("[4/4] Running Vulture …")
        vulture_results = vulture_analyzer.run(actual_path)
        print(f"       Found {len(vulture_results)} issue(s)")
        raw_issues.extend(vulture_results)

        # --- Normalise ---
        normalised = normalize(raw_issues, repo_name=repo_name)

        # --- Sort by static_score descending ---
        normalised.sort(key=lambda x: x["static_score"], reverse=True)

        return normalised, repo_name
    finally:
        if temp_dir and os.path.exists(temp_dir):
            print(f"\n[git] Cleaning up temporary directory {temp_dir}...")
            shutil.rmtree(temp_dir, ignore_errors=True)


def save_results(issues: list[dict], output_path: str) -> str:
    """Save normalised issues to JSON.  Returns the absolute path written."""
    dir_name = os.path.dirname(output_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(issues, fh, indent=2, ensure_ascii=False)
    return os.path.abspath(output_path)


def print_summary(issues: list[dict]) -> None:
    """Print a human-readable summary table to stdout."""
    total = len(issues)
    if total == 0:
        print("\n  No issues found.\n")
        return

    by_tool = Counter(i["tool"] for i in issues)
    by_severity = Counter(i["severity"] for i in issues)
    by_type = Counter(i["issue_type"] for i in issues)

    print(f"\n{'='*60}")
    print(f"  SUMMARY — {total} issue(s) found")
    print(f"{'='*60}")

    print("\n  By tool:")
    for tool, count in sorted(by_tool.items()):
        print(f"    {tool:12s}  {count:>5}")

    print("\n  By severity:")
    for sev in ("HIGH", "MEDIUM", "LOW"):
        print(f"    {sev:12s}  {by_severity.get(sev, 0):>5}")

    print("\n  By issue type:")
    for itype, count in sorted(by_type.items()):
        print(f"    {itype:20s}  {count:>5}")

    # Top-5 worst static_score
    print("\n  Top 5 highest static_score:")
    for i, issue in enumerate(issues[:5], 1):
        print(
            f"    {i}. [{issue['tool']}] {issue['file']}:"
            f"{issue['line']} — score {issue['static_score']}"
        )
        print(f"       {issue['message'][:80]}")

    print(f"\n{'='*60}\n")
