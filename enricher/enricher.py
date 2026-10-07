"""
Enricher Orchestrator — Phase 2.

Loads a Phase 1 debt dataset, runs all three enrichers
(git history, call graph, test coverage), merges the results
into each issue record, and writes a new enriched dataset.

New fields added to each issue:
  - recent_commits     : int   (commits touching the file in last 90 days)
  - defect_commits     : int   (commits with fix/bug/error in message)
  - commit_authors     : int   (distinct authors of this file)
  - last_modified_days : int   (days since last commit on this file)
  - callers            : int   (call sites for this function in the repo)
  - test_coverage      : float (pytest coverage % for this file, 0-100)
"""

from __future__ import annotations

import json
import os

from enricher.git_enricher import build_file_context
from enricher.callgraph_enricher import build_call_counts
from enricher.coverage_enricher import build_coverage_map


_DEFAULT_GIT_CONTEXT = {
    "recent_commits": 0,
    "defect_commits": 0,
    "commit_authors": 0,
    "last_modified_days": -1,
}


def _resolve_file_key(issue_file: str, repo_path: str, git_context: dict) -> dict:
    """
    Try several path representations to match an issue's file path
    against the git_context keys (which are repo-relative paths).
    """
    norm = os.path.normpath(issue_file)
    repo_norm = os.path.normpath(os.path.abspath(repo_path))

    candidates = [issue_file, os.path.basename(norm)]

    # os.path.relpath fails on Windows when paths are on different drives
    try:
        candidates.append(os.path.relpath(norm, repo_norm).replace("\\", "/"))
        candidates.append(os.path.relpath(norm, repo_norm))
    except ValueError:
        pass  # different drive letters on Windows — skip relpath

    for key in candidates:
        if key in git_context:
            return git_context[key]
    return {}


def _resolve_coverage(issue_file: str, coverage_map: dict) -> float:
    """Match an issue's file path against coverage_map keys (absolute paths)."""
    norm = os.path.normpath(os.path.abspath(issue_file))
    if norm in coverage_map:
        return coverage_map[norm]
    # Fallback: basename match
    bname = os.path.basename(norm)
    for k, v in coverage_map.items():
        if os.path.basename(k) == bname:
            return v
    return 0.0


def enrich(
    phase1_path: str,
    repo_path: str,
    output_path: str,
    lookback_days: int = 90,
    as_of=None,
) -> str:
    """
    Load *phase1_path* (Phase 1 JSON dataset), run all enrichers against
    *repo_path*, merge results, and save to *output_path*.

    *as_of* (timezone-aware datetime) restricts git history to commits before
    that moment — see ``git_enricher.build_file_context``.

    Returns the absolute path of the written file.
    """
    print(f"\n{'='*60}")
    print("  Technical Debt Enricher — Phase 2")
    print(f"  Repository : {os.path.basename(os.path.abspath(repo_path))}")
    print(f"  Input      : {phase1_path}")
    print(f"  Output     : {output_path}")
    print(f"{'='*60}\n")

    # ── Load Phase 1 dataset ──────────────────────────────────────────────
    with open(phase1_path, encoding="utf-8") as fh:
        issues: list[dict] = json.load(fh)
    print(f"  Loaded {len(issues)} issue(s) from Phase 1 dataset.")

    # ── Run enrichers ─────────────────────────────────────────────────────
    print("\n[1/3] Running Git history enricher …")
    git_context = build_file_context(repo_path, lookback_days=lookback_days, as_of=as_of)
    print(f"       Got context for {len(git_context)} file(s).")

    print("[2/3] Running Call-graph enricher …")
    call_counts = build_call_counts(repo_path)
    print(f"       Found call sites for {len(call_counts)} unique function name(s).")

    print("[3/3] Running Coverage enricher …")
    coverage_map = build_coverage_map(repo_path)
    print(f"       Coverage data for {len(coverage_map)} file(s).")

    # ── Merge into each issue ─────────────────────────────────────────────
    enriched: list[dict] = []
    for issue in issues:
        record = dict(issue)  # copy all Phase 1 fields

        # Git context
        git_data = _resolve_file_key(issue.get("file", ""), repo_path, git_context)
        record["recent_commits"] = git_data.get("recent_commits", 0)
        record["defect_commits"] = git_data.get("defect_commits", 0)
        record["commit_authors"] = git_data.get("commit_authors", 0)
        record["last_modified_days"] = git_data.get("last_modified_days", -1)

        # Callers (by function name)
        func_name = issue.get("function", "")
        record["callers"] = call_counts.get(func_name, 0)

        # Coverage
        record["test_coverage"] = _resolve_coverage(issue.get("file", ""), coverage_map)

        enriched.append(record)

    # ── Save enriched dataset ─────────────────────────────────────────────
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(enriched, fh, indent=2, ensure_ascii=False)

    abs_out = os.path.abspath(output_path)
    print(f"\n  Enriched dataset saved to: {abs_out}")
    print(f"  Total issues enriched    : {len(enriched)}")
    return abs_out


def print_enrichment_summary(enriched_path: str) -> None:
    """Print a short summary of the enriched dataset."""
    with open(enriched_path, encoding="utf-8") as fh:
        issues: list[dict] = json.load(fh)

    total = len(issues)
    with_defects = sum(1 for i in issues if i.get("defect_commits", 0) > 0)
    with_callers = sum(1 for i in issues if i.get("callers", 0) > 0)
    with_coverage = sum(1 for i in issues if i.get("test_coverage", 0.0) > 0)
    avg_commits = (
        sum(i.get("recent_commits", 0) for i in issues) / total if total else 0
    )

    print(f"\n{'='*60}")
    print(f"  ENRICHMENT SUMMARY — {total} issue(s)")
    print(f"{'='*60}")
    print(f"  Issues with defect commits : {with_defects}")
    print(f"  Issues with known callers  : {with_callers}")
    print(f"  Issues with coverage data  : {with_coverage}")
    print(f"  Avg recent commits/file    : {avg_commits:.1f}")

    # Top-5 by defect_commits
    top5 = sorted(issues, key=lambda x: x.get("defect_commits", 0), reverse=True)[:5]
    print("\n  Top 5 most defect-prone files:")
    for i, issue in enumerate(top5, 1):
        print(
            f"    {i}. {issue.get('file', '?')} — "
            f"{issue.get('defect_commits', 0)} defect commit(s), "
            f"{issue.get('callers', 0)} caller(s), "
            f"{issue.get('test_coverage', 0):.1f}% coverage"
        )
    print(f"\n{'='*60}\n")
