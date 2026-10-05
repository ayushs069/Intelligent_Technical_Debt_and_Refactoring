"""
Git Enricher — extracts per-file git history context using GitPython.

For each file in the dataset it computes:
  - recent_commits   : number of commits touching this file in the last 90 days
  - defect_commits   : subset whose message contains "fix", "bug", or "error"
  - commit_authors   : number of distinct authors (churn indicator)
  - last_modified_days: days since the file was last modified
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from typing import Optional


def _open_repo(repo_path: str):
    """Return a GitPython Repo, or None if repo_path is not a git repo."""
    try:
        import git
        return git.Repo(repo_path, search_parent_directories=True)
    except Exception:
        return None


def _days_ago(n: int) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=n)


def build_file_context(repo_path: str, lookback_days: int = 90) -> dict[str, dict]:
    """
    Walk the git log of *repo_path* and return a mapping:
        relative_file_path -> {
            recent_commits    : int,
            defect_commits    : int,
            commit_authors    : int,
            last_modified_days: int,
        }
    Returns an empty dict if the path is not a git repository.
    """
    repo = _open_repo(repo_path)
    if repo is None:
        print("[git_enricher] Not a git repository — skipping git context.")
        return {}

    since = _days_ago(lookback_days)
    defect_keywords = {"fix", "bug", "error", "patch", "hotfix", "issue"}

    file_stats: dict[str, dict] = {}

    try:
        # Walk ALL commits in the lookback window
        for commit in repo.iter_commits(since=since.isoformat()):
            commit_dt = datetime.fromtimestamp(commit.committed_date, tz=timezone.utc)
            is_defect = any(kw in commit.message.lower() for kw in defect_keywords)
            author = str(commit.author.email or commit.author.name)
            days_since = (datetime.now(tz=timezone.utc) - commit_dt).days

            for diff in commit.stats.files:
                # diff is a relative path within the repo
                if not diff.endswith(".py"):
                    continue
                if diff not in file_stats:
                    file_stats[diff] = {
                        "recent_commits": 0,
                        "defect_commits": 0,
                        "authors": set(),
                        "last_modified_days": days_since,
                    }
                s = file_stats[diff]
                s["recent_commits"] += 1
                if is_defect:
                    s["defect_commits"] += 1
                s["authors"].add(author)
                # keep the minimum (= most recent) days_since
                s["last_modified_days"] = min(s["last_modified_days"], days_since)

    except Exception as exc:
        print(f"[git_enricher] Error walking commits: {exc}")
        return {}

    # Serialise: convert sets to counts
    result: dict[str, dict] = {}
    for path, stats in file_stats.items():
        result[path] = {
            "recent_commits": stats["recent_commits"],
            "defect_commits": stats["defect_commits"],
            "commit_authors": len(stats["authors"]),
            "last_modified_days": stats["last_modified_days"],
        }

    return result
