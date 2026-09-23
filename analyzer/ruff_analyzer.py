"""
Ruff Analyzer — fast Python linter.

Runs:
  ruff check <path> --output-format json
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
        print(f"[ruff] command failed: {exc}")
        return ""


def _severity_from_code(code: str) -> str:
    """Map ruff rule code to severity level."""
    # E = error, W = warning, F = pyflakes, C = convention, etc.
    if code.startswith(("E9", "F")):
        return "HIGH"
    if code.startswith(("E", "W")):
        return "MEDIUM"
    return "LOW"


def run(target_path: str) -> list[dict]:
    """
    Run Ruff linter on *target_path*.

    Returns a list of raw issue dicts (not yet normalised).
    """
    import os
    norm_path = os.path.normpath(target_path)
    raw = _run_cmd(["ruff", "check", norm_path, "--output-format", "json"])
    if not raw.strip():
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    issues = []
    for item in data:
        code = item.get("code", "")
        location = item.get("location", {})
        issues.append({
            "file": item.get("filename", ""),
            "function": "",
            "line": location.get("row", 0),
            "col": location.get("column", 0),
            "issue_type": "lint",
            "tool": "ruff",
            "raw_severity": _severity_from_code(code),
            "complexity": 0,
            "loc": 0,
            "message": f"[{code}] {item.get('message', '')}",
        })
    return issues
