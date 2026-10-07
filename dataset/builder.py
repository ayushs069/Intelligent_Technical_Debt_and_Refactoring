"""
Dataset Builder — turns a repository snapshot into function-level debt items.

Pipeline (all inputs are taken *as of* the snapshot cutoff):

  1. Phase 1 static analysis (Radon / Ruff / Pylint / Vulture) on the source dir.
  2. Function index: span, complexity, LOC, params, docstring, MI.
  3. Each Phase 1 issue is attributed to the innermost function containing it.
  4. Repository context (Phase 2): callers, test references, file git history
     and per-function git history before the cutoff, function/file coverage,
     duplicated lines.
  5. Candidate selection: functions that carry at least one debt signal.

Ground truth is computed separately from commits *after* the cutoff and written
to its own file so that no ranking method can read it.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime

from analyzer.analyzer import run_all
from analyzer.normalizer import _compute_static_score, _map_severity
from dataset.duplication import find_duplicate_lines
from dataset.function_index import function_coverage, index_functions, iter_python_files
from enricher.callgraph_enricher import build_call_counts
from enricher.function_history import build_function_history, canonical_path, innermost_function
from enricher.git_enricher import build_file_context

# Pylint/Ruff categories that are pure style and say little about debt.
_STYLE_ONLY_TYPES = {"convention"}


def _rel_canon(path: str, repo_root: str) -> str:
    """Canonical repo-relative path for an analyzer-reported file path."""
    if os.path.isabs(path):
        try:
            path = os.path.relpath(path, repo_root)
        except ValueError:
            pass
    return canonical_path(path)


def attach_issues(functions: list[dict], issues: list[dict], repo_root: str) -> None:
    """Attribute each Phase 1 issue to the innermost function containing its line."""
    by_file: dict[str, list[dict]] = defaultdict(list)
    for fn in functions:
        by_file[fn["file"]].append(fn)
    spans = {f: [(fn["function"], fn["start_line"], fn["end_line"]) for fn in fns]
             for f, fns in by_file.items()}
    lookup = {fn["id"]: fn for fn in functions}

    for fn in functions:
        fn.update(code_smells=0, style_issues=0, dead_code=False, smell_types=[],
                  tool_messages=[])

    for issue in issues:
        canon = _rel_canon(issue.get("file", ""), repo_root)
        if canon not in spans or issue.get("tool") == "radon":
            continue  # radon metrics are recomputed per function in the index
        qual = innermost_function(spans[canon], int(issue.get("line", 0) or 0))
        if not qual:
            continue
        fn = lookup[f"{canon}::{qual}"]
        itype = issue.get("issue_type", "")
        if issue.get("tool") == "vulture":
            # Only flag the function itself as dead, not unused locals inside it.
            if issue.get("line") == fn["line"] and any(
                w in issue.get("message", "") for w in ("unused function", "unused method")
            ):
                fn["dead_code"] = True
            continue
        if itype in _STYLE_ONLY_TYPES:
            fn["style_issues"] += 1
            continue
        fn["code_smells"] += 1
        msg = issue.get("message", "")
        fn["tool_messages"].append(f"[{issue.get('tool')}] {msg}"[:160])
        symbol = msg.split("(")[-1].rstrip(")") if msg.endswith(")") else itype
        fn["smell_types"].append(symbol)

    for fn in functions:
        fn["smell_types"] = sorted(set(fn["smell_types"]))
        fn["tool_messages"] = fn["tool_messages"][:8]


def is_candidate(fn: dict, min_complexity: int = 6) -> bool:
    """A function is a debt candidate if it carries at least one debt signal."""
    return (
        fn["complexity"] >= min_complexity
        or fn["code_smells"] > 0
        or fn["dead_code"]
        or fn["duplicate_lines"] >= 6
        or fn["loc"] >= 60
    )


def build_dataset(
    repo_root: str,
    source_dir: str,
    as_of: datetime,
    history_since: datetime,
    horizon: datetime,
    coverage_json: str | None = None,
    tests_dir: str | None = None,
    git_repo: str | None = None,
    file_lookback_days: int = 730,
    repo_name: str = "",
) -> tuple[list[dict], dict[str, dict], dict]:
    """
    Build items for *source_dir* inside the snapshot *repo_root*.

    *git_repo* is the clone holding the full history (defaults to repo_root).
    Returns ``(items, ground_truth, metadata)``.
    """
    git_repo = git_repo or repo_root
    repo_name = repo_name or os.path.basename(os.path.abspath(git_repo))

    # 1. Phase 1 static analysis
    raw_issues, _ = run_all(source_dir)

    # 2. Function index
    functions = index_functions(repo_root, source_dir)
    print(f"[dataset] Indexed {len(functions)} function(s).")

    # 3. Attribute issues
    attach_issues(functions, raw_issues, repo_root)

    # 4a. Duplication
    sources = {}
    for path in iter_python_files(source_dir):
        rel = canonical_path(os.path.relpath(path, repo_root))
        with open(path, encoding="utf-8") as fh:
            sources[rel] = fh.read()
    dup = find_duplicate_lines(sources)

    # 4b. Call graph (library call sites) and test references
    callers = build_call_counts(source_dir)
    test_refs = build_call_counts(tests_dir) if tests_dir and os.path.isdir(tests_dir) else {}

    # 4c. File-level git context before the cutoff (Phase 2 enricher)
    raw_ctx = build_file_context(git_repo, lookback_days=file_lookback_days, as_of=as_of)
    file_ctx: dict[str, dict] = {}
    for path, ctx in raw_ctx.items():
        canon = canonical_path(path)
        merged = file_ctx.setdefault(canon, {"recent_commits": 0, "defect_commits": 0,
                                             "commit_authors": 0, "last_modified_days": -1})
        merged["recent_commits"] += ctx["recent_commits"]
        merged["defect_commits"] += ctx["defect_commits"]
        merged["commit_authors"] = max(merged["commit_authors"], ctx["commit_authors"])
        lm = ctx["last_modified_days"]
        if merged["last_modified_days"] < 0 or (0 <= lm < merged["last_modified_days"]):
            merged["last_modified_days"] = lm

    # 4d. Function-level history before the cutoff
    print("[dataset] Mining function-level history before cutoff …")
    pre = build_function_history(git_repo, history_since, as_of)

    # 4e. Coverage
    cov_files: dict[str, dict] = {}
    if coverage_json and os.path.isfile(coverage_json):
        with open(coverage_json, encoding="utf-8") as fh:
            for path, entry in json.load(fh).get("files", {}).items():
                cov_files[_rel_canon(path, repo_root)] = entry

    for fn in functions:
        fn["repo"] = repo_name
        fn["duplicate_lines"] = sum(1 for ln in dup.get(fn["file"], ())
                                    if fn["start_line"] <= ln <= fn["end_line"])
        fn["callers"] = callers.get(fn["name"], 0)
        fn["test_references"] = test_refs.get(fn["name"], 0)
        ctx = file_ctx.get(fn["file"], {})
        fn["recent_commits"] = ctx.get("recent_commits", 0)
        fn["defect_commits"] = ctx.get("defect_commits", 0)
        fn["commit_authors"] = ctx.get("commit_authors", 0)
        fn["last_modified_days"] = ctx.get("last_modified_days", -1)
        hist = pre.get(fn["id"], {})
        fn["func_commits"] = hist.get("commits", 0)
        fn["func_fix_commits"] = hist.get("fix_commits", 0)
        entry = cov_files.get(fn["file"])
        fn["test_coverage"] = round(entry["summary"]["percent_covered"], 1) if entry else 0.0
        fc = function_coverage(entry, fn["start_line"], fn["end_line"]) if entry else None
        fn["function_coverage"] = fc if fc is not None else fn["test_coverage"]
        # Static score with the existing Phase 1 formula (severity from CC rank)
        fn["severity"] = _map_severity(fn["complexity_rank"])
        fn["static_score"] = _compute_static_score(fn)
        # Fields expected by the Phase 3 document formatter
        fn["issue_type"] = "function_debt"
        fn["tool"] = "aggregate"
        fn["message"] = "; ".join(fn["tool_messages"][:3]) or (
            f"Complexity {fn['complexity']} (rank {fn['complexity_rank']}), {fn['loc']} LOC")

    items = [fn for fn in functions if is_candidate(fn)]
    # Static baseline rank: composite static score, ties by complexity then LOC
    items.sort(key=lambda x: (-x["static_score"], -x["complexity"], -x["loc"], x["id"]))
    for rank, item in enumerate(items, 1):
        item["static_rank"] = rank

    # Ground truth: commits after the cutoff (never shown to rankers)
    print("[dataset] Mining function-level history after cutoff (ground truth) …")
    post = build_function_history(git_repo, as_of, horizon)
    ground_truth = {}
    for item in items:
        h = post.get(item["id"], {})
        ground_truth[item["id"]] = {
            "future_commits": h.get("commits", 0),
            "future_fix_commits": h.get("fix_commits", 0),
            "future_defect": h.get("fix_commits", 0) > 0,
            "fix_shas": h.get("fix_shas", []),
        }

    meta = {
        "repo": repo_name,
        "as_of": as_of.isoformat(),
        "history_since": history_since.isoformat(),
        "horizon": horizon.isoformat(),
        "functions_indexed": len(functions),
        "candidates": len(items),
        "phase1_issues": len(raw_issues),
        "phase1_by_tool": dict(Counter(i["tool"] for i in raw_issues)),
        "candidates_with_future_defect": sum(g["future_defect"] for g in ground_truth.values()),
        "candidates_with_future_change": sum(g["future_commits"] > 0 for g in ground_truth.values()),
    }
    return items, ground_truth, meta
