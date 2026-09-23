"""
Radon Analyzer — Cyclomatic Complexity & Maintainability Index.

Runs:
  radon cc <path> -s -j   → complexity per function (JSON)
  radon mi <path> -s -j   → maintainability index per file (JSON)
"""

import json
import subprocess


def _run_cmd(cmd: list[str]) -> str:
    """Run a command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[radon] command failed: {exc}")
        return ""


def _parse_complexity(raw_json: str) -> list[dict]:
    """Parse radon cc JSON output into raw issue dicts."""
    issues = []
    try:
        data = json.loads(raw_json) if raw_json.strip() else {}
    except json.JSONDecodeError:
        return issues

    if not isinstance(data, dict):
        return issues

    # data is {filepath: [block, ...]} or {filepath: {"error": ...}}
    for filepath, blocks in data.items():
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            complexity = block.get("complexity", 0)
            rank = block.get("rank", "A")
            # Only report issues grade B or worse (complexity >= 6)
            if complexity < 6:
                continue
            issues.append({
                "file": filepath,
                "function": block.get("name", ""),
                "line": block.get("lineno", 0),
                "col": 0,
                "issue_type": "high_complexity",
                "tool": "radon",
                "raw_severity": rank,
                "complexity": complexity,
                "loc": block.get("endline", 0) - block.get("lineno", 0) + 1,
                "message": (
                    f"Cyclomatic complexity of {complexity} "
                    f"(rank {rank}) in {block.get('type', 'block')} "
                    f"'{block.get('name', '')}'"
                ),
            })
    return issues


def _parse_maintainability(raw_json: str) -> list[dict]:
    """Parse radon mi JSON output into raw issue dicts."""
    issues = []
    try:
        data = json.loads(raw_json) if raw_json.strip() else {}
    except json.JSONDecodeError:
        return issues

    if not isinstance(data, dict):
        return issues

    # data is {filepath: {"mi": float, "rank": str}}
    for filepath, info in data.items():
        if not isinstance(info, dict):
            continue
        mi = info.get("mi", 100.0)
        rank = info.get("rank", "A")
        # Only report files with MI below 65 (rank B or worse)
        if mi >= 65:
            continue
        issues.append({
            "file": filepath,
            "function": "(module)",
            "line": 1,
            "col": 0,
            "issue_type": "maintainability",
            "tool": "radon",
            "raw_severity": rank,
            "complexity": 0,
            "maintainability_index": round(mi, 2),
            "loc": 0,
            "message": (
                f"Low maintainability index {mi:.1f} (rank {rank}) "
                f"for module {filepath}"
            ),
        })
    return issues


def run(target_path: str) -> list[dict]:
    """
    Run Radon complexity and maintainability analysis on *target_path*.

    Returns a list of raw issue dicts (not yet normalised).
    """
    import os
    norm_path = os.path.normpath(target_path)
    cc_json = _run_cmd(["radon", "cc", norm_path, "-s", "-j", "-a"])
    mi_json = _run_cmd(["radon", "mi", norm_path, "-s", "-j"])

    issues = _parse_complexity(cc_json) + _parse_maintainability(mi_json)
    return issues
