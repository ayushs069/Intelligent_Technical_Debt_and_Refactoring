"""
Vulture Analyzer — dead-code detection.

Runs:
  vulture <path>

Output is plain text, one line per finding:
  filepath:line: unused function 'foo' (60% confidence)
"""

import re
import subprocess

# Pattern: file.py:42: unused function 'name' (90% confidence)
_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\s+(?P<message>.+?)\s+"
    r"\((?P<confidence>\d+)%\s+confidence\)\s*$"
)


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
        print(f"[vulture] command failed: {exc}")
        return ""


def _severity_from_confidence(confidence: int) -> str:
    """Map confidence percentage to severity."""
    if confidence >= 90:
        return "HIGH"
    if confidence >= 60:
        return "MEDIUM"
    return "LOW"


def _extract_name(message: str) -> str:
    """Try to extract the identifier name from the vulture message."""
    # e.g. "unused function 'process_payment'"
    match = re.search(r"'([^']+)'", message)
    return match.group(1) if match else ""


def run(target_path: str) -> list[dict]:
    """
    Run Vulture dead-code detection on *target_path*.

    Returns a list of raw issue dicts (not yet normalised).
    """
    import os
    norm_path = os.path.normpath(target_path)
    raw = _run_cmd(["vulture", norm_path])
    if not raw.strip():
        return []

    issues = []
    for line in raw.strip().splitlines():
        m = _LINE_RE.match(line.strip())
        if not m:
            continue
        confidence = int(m.group("confidence"))
        message = m.group("message")
        issues.append({
            "file": m.group("file"),
            "function": _extract_name(message),
            "line": int(m.group("line")),
            "col": 0,
            "issue_type": "dead_code",
            "tool": "vulture",
            "raw_severity": _severity_from_confidence(confidence),
            "complexity": 0,
            "loc": 0,
            "message": f"{message} ({confidence}% confidence)",
            "confidence": confidence,
        })
    return issues
