"""
Normalizer — merge raw analyzer outputs into a common schema.

Each normalised issue has:
  file, function, line, issue_type, tool, severity,
  complexity, maintainability_index, loc, message, static_score
"""

from __future__ import annotations


_SEVERITY_SCORES = {"HIGH": 2, "MEDIUM": 1, "LOW": 0}


def _map_severity(raw: str) -> str:
    """Normalise severity string."""
    raw_upper = str(raw).upper()
    # Radon complexity ranks: A=best … F=worst
    rank_map = {"A": "LOW", "B": "LOW", "C": "MEDIUM", "D": "MEDIUM",
                "E": "HIGH", "F": "HIGH"}
    if raw_upper in rank_map:
        return rank_map[raw_upper]
    if raw_upper in _SEVERITY_SCORES:
        return raw_upper
    return "LOW"


def _compute_static_score(issue: dict) -> float:
    """
    Compute a 0–10 composite debt score (higher = worse).

    Components:
      • Complexity contribution: min(complexity / 5, 4.0)   → up to 4 pts
      • Maintainability contribution: max(0, (100-MI) / 25) → up to 4 pts
      • Severity bonus: HIGH=2, MEDIUM=1, LOW=0              → up to 2 pts
    """
    cx = min(issue.get("complexity", 0) / 5, 4.0)
    mi = issue.get("maintainability_index", 100.0)
    mi_score = max(0.0, min((100 - mi) / 25, 4.0))
    sev = _SEVERITY_SCORES.get(issue.get("severity", "LOW"), 0)
    return round(cx + mi_score + sev, 2)


def normalize(raw_issues: list[dict], repo_name: str = "") -> list[dict]:
    """
    Convert a list of raw issue dicts (from any analyzer) into
    the unified schema with ``static_score`` and ``repo``.
    """
    normalised: list[dict] = []
    for raw in raw_issues:
        issue = {
            "repo": repo_name or raw.get("repo", ""),
            "file": raw.get("file", ""),
            "function": raw.get("function", ""),
            "line": raw.get("line", 0),
            "issue_type": raw.get("issue_type", ""),
            "tool": raw.get("tool", ""),
            "severity": _map_severity(raw.get("raw_severity", "LOW")),
            "complexity": raw.get("complexity", 0),
            "maintainability_index": raw.get("maintainability_index", 100.0),
            "loc": raw.get("loc", 0),
            "message": raw.get("message", ""),
        }
        issue["static_score"] = _compute_static_score(issue)
        normalised.append(issue)
    return normalised
