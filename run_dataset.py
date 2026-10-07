#!/usr/bin/env python3
"""
run_dataset.py - build the function-level research dataset with a temporal split.

The target repository is checked out at the last commit before ``--as-of``
(a git worktree, so the main clone is untouched).  Static analysis and all
repository context are computed from that snapshot and from history *before*
the cutoff; commits *after* the cutoff become the ground truth.

Usage:
    git clone https://github.com/psf/requests.git targets/requests
    python run_dataset.py --repo targets/requests --as-of 2024-01-01 \\
        --source-subdir src/requests --test-python targets/.venv-requests/Scripts/python

Outputs (data/):
    <repo>_items.json          candidate debt items (what rankers see)
    <repo>_ground_truth.json   post-cutoff history per item (evaluation only)
    <repo>_dataset_meta.json   cutoff, snapshot commit, counts
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import config
from dataset.builder import build_dataset


def _date(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def ensure_snapshot(repo: str, as_of: datetime, snapshot_dir: str) -> str:
    """Create (or reuse) a detached worktree at the last commit before *as_of*."""
    sha = _git(repo, "rev-list", "-1", f"--before={as_of.isoformat()}", "HEAD")
    if os.path.isdir(snapshot_dir):
        current = _git(snapshot_dir, "rev-parse", "HEAD")
        if current != sha:
            raise SystemExit(f"[error] {snapshot_dir} is at {current[:8]}, expected {sha[:8]}. "
                             "Remove it (git worktree remove) or pass --snapshot-dir.")
    else:
        _git(repo, "worktree", "add", "--detach", os.path.abspath(snapshot_dir), sha)
    return sha


def ensure_coverage(snapshot_dir: str, source_subdir: str, tests_subdir: str,
                    test_python: str) -> str | None:
    cov = os.path.join(snapshot_dir, "coverage.json")
    if os.path.isfile(cov):
        return cov
    if not test_python:
        print("[dataset] No coverage.json and no --test-python given - coverage will be 0.")
        return None
    print("[dataset] Running the snapshot's test suite with coverage …")
    src_root = os.path.dirname(source_subdir) or "."
    env = dict(os.environ, PYTHONPATH=os.path.join(snapshot_dir, src_root))
    subprocess.run([os.path.abspath(test_python), "-m", "pytest", tests_subdir, "-q",
                    "-p", "no:cacheprovider", "--tb=no", f"--cov={source_subdir}",
                    "--cov-report=json:coverage.json"],
                   cwd=snapshot_dir, env=env, check=False)
    return cov if os.path.isfile(cov) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the function-level debt dataset.")
    parser.add_argument("--repo", required=True, help="Full-history git clone of the target.")
    parser.add_argument("--as-of", default=config.DEFAULT_AS_OF, help="Cutoff date YYYY-MM-DD.")
    parser.add_argument("--history-since", default=config.DEFAULT_HISTORY_SINCE,
                        help="Start of the pre-cutoff history window (YYYY-MM-DD).")
    parser.add_argument("--horizon", default="",
                        help="End of the ground-truth window (YYYY-MM-DD, default: today).")
    parser.add_argument("--source-subdir", default="src", help="Package dir inside the repo.")
    parser.add_argument("--tests-subdir", default="tests", help="Tests dir inside the repo.")
    parser.add_argument("--snapshot-dir", default="", help="Worktree path for the snapshot.")
    parser.add_argument("--test-python", default="",
                        help="Python interpreter with the target's test deps (for coverage).")
    parser.add_argument("--file-lookback", type=int, default=730,
                        help="Days of file-level git history before the cutoff (default 730).")
    args = parser.parse_args()

    repo = os.path.abspath(args.repo)
    repo_name = os.path.basename(repo.rstrip("/\\"))
    as_of = _date(args.as_of)
    horizon = _date(args.horizon) if args.horizon else datetime.now(tz=timezone.utc)
    snapshot_dir = args.snapshot_dir or os.path.join(os.path.dirname(repo), f"{repo_name}_snapshot")

    sha = ensure_snapshot(repo, as_of, snapshot_dir)
    print(f"[dataset] Snapshot {sha[:8]} (last commit before {args.as_of}) at {snapshot_dir}")
    coverage = ensure_coverage(snapshot_dir, args.source_subdir, args.tests_subdir,
                               args.test_python)

    items, ground_truth, meta = build_dataset(
        repo_root=snapshot_dir,
        source_dir=os.path.join(snapshot_dir, args.source_subdir),
        as_of=as_of,
        history_since=_date(args.history_since),
        horizon=horizon,
        coverage_json=coverage,
        tests_dir=os.path.join(snapshot_dir, args.tests_subdir),
        git_repo=repo,
        file_lookback_days=args.file_lookback,
        repo_name=repo_name,
    )
    meta.update(snapshot_sha=sha, snapshot_dir=os.path.abspath(snapshot_dir),
                git_repo=repo, source_subdir=args.source_subdir,
                tests_subdir=args.tests_subdir, coverage_json=coverage or "")

    os.makedirs(config.DATA_DIR, exist_ok=True)
    paths = config.dataset_paths(repo_name)
    for key, payload in (("items", items), ("ground_truth", ground_truth), ("meta", meta)):
        with open(paths[key], "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}\n  DATASET SUMMARY - {repo_name} @ {args.as_of}\n{'='*60}")
    for key in ("functions_indexed", "candidates", "candidates_with_future_defect",
                "candidates_with_future_change"):
        print(f"  {key:32s} {meta[key]}")
    print("\n  Top 5 by static score:")
    for item in items[:5]:
        print(f"    {item['static_rank']:>3}. {item['id']}  score={item['static_score']} "
              f"cc={item['complexity']} callers={item['callers']}")
    for key in ("items", "ground_truth", "meta"):
        print(f"  wrote {key:13s} -> {paths[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
