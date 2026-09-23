"""
Pylint Analyzer — comprehensive Python linter.

Runs:
  pylint <path> --output-format json --recursive=y
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
            timeout=180,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[pylint] command failed: {exc}")
        return ""


_PYLINT_TYPE_TO_SEVERITY = {
    "fatal": "HIGH",
    "error": "HIGH",
    "warning": "MEDIUM",
    "convention": "LOW",
    "refactor": "MEDIUM",
    "information": "LOW",
}


def _map_issue_type(msg_type: str) -> str:
    """Map pylint message type to our issue_type taxonomy."""
    if msg_type in ("fatal", "error"):
        return "lint"
    if msg_type == "refactor":
        return "convention"
    if msg_type == "convention":
        return "convention"
    return "lint"


def run(target_path: str) -> list[dict]:
    """
    Run Pylint on *target_path*.

    Returns a list of raw issue dicts (not yet normalised).
    """
    import os

    norm_path = os.path.normpath(target_path)
    if os.path.isdir(norm_path):
        py_files = []
        for root, dirs, files in os.walk(norm_path):
            # Exclude common non-source / heavy directories
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", "env", "data", "node_modules", "site-packages")]
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))
        if not py_files:
            return []
        cmd = ["pylint", *py_files, "--output-format", "json"]
    else:
        cmd = ["pylint", norm_path, "--output-format", "json"]

    raw = _run_cmd(cmd)
    if not raw.strip():
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    issues = []
    for item in data:
        msg_type = item.get("type", "convention")
        symbol = item.get("symbol", "")
        message_text = item.get("message", "")
        issues.append({
            "file": item.get("path", ""),
            "function": item.get("obj", ""),
            "line": item.get("line", 0),
            "col": item.get("column", 0),
            "issue_type": _map_issue_type(msg_type),
            "tool": "pylint",
            "raw_severity": _PYLINT_TYPE_TO_SEVERITY.get(msg_type, "LOW"),
            "complexity": 0,
            "loc": 0,
            "message": f"[{symbol}] {message_text}",
        })
    return issues
