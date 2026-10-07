"""
Validate — the local CI gate for a refactoring branch (mirrors techdebt.yml):

  * Pytest on the target's test suite (compared with the unmodified baseline,
    so pre-existing failures are not blamed on the patch)
  * Radon complexity of the refactored function
  * Ruff findings in the modified file
  * Duplicated lines in the modified file
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import tempfile
import xml.etree.ElementTree as ET

from dataset.duplication import find_duplicate_lines
from dataset.function_index import iter_python_files
from enricher.function_history import canonical_path

_SUMMARY_RE = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)")


def run_tests(worktree: str, test_python: str, src_root: str, tests_subdir: str,
              timeout: int = 1800) -> dict:
    """Run the target's pytest suite in *worktree*; return counts and failing ids."""
    env = dict(os.environ, PYTHONPATH=os.path.join(worktree, src_root),
               PYTHONDONTWRITEBYTECODE="1")
    started = time.time()
    fd, report = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        proc = subprocess.run(
            [test_python, "-m", "pytest", tests_subdir, "-q", "-p", "no:cacheprovider",
             "--tb=line", "-rfE", f"--junitxml={report}"],
            cwd=worktree, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False)
        output = proc.stdout + proc.stderr
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        output, code = (exc.stdout or "") if isinstance(exc.stdout, str) else "", -1
    counts = {"passed": 0, "failed": 0, "errors": 0}
    for n, kind in _SUMMARY_RE.findall(output[-3000:]):
        key = "errors" if kind.startswith("error") else kind
        if key in counts:
            counts[key] = int(n)
    failing = sorted({line.split(" ", 1)[1].split(" - ")[0].strip()
                      for line in output.splitlines()
                      if line.startswith(("FAILED ", "ERROR "))})
    passed_ids, collected_ids = [], []
    report_valid = False
    try:
        root = ET.parse(report).getroot()
        cases = list(root.iter("testcase"))
        counts = {"passed": 0, "failed": 0, "errors": 0}
        failing = []
        for case in cases:
            identity = case.get("classname", "") + "::" + case.get("name", "")
            collected_ids.append(identity)
            if case.find("error") is not None:
                counts["errors"] += 1
                failing.append(identity)
            elif case.find("failure") is not None:
                counts["failed"] += 1
                failing.append(identity)
            elif case.find("skipped") is None:
                counts["passed"] += 1
                passed_ids.append(identity)
        report_valid = bool(cases)
    except (OSError, ET.ParseError):
        pass
    finally:
        os.unlink(report)
    return {**counts, "failed_ids": failing, "returncode": code,
            "passed_ids": sorted(passed_ids), "collected_ids": sorted(collected_ids),
            "report_valid": report_valid,
            "duration_s": round(time.time() - started, 1),
            "tail": "\n".join(output.strip().splitlines()[-25:])}


def ruff_findings(path: str) -> int:
    """Number of Ruff findings in one file (same tool as Phase 1)."""
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", path, "--output-format",
                           "json", "--exit-zero"], capture_output=True, text=True, check=False)
    try:
        if proc.returncode != 0 or not proc.stdout.strip():
            return -1
        return len(json.loads(proc.stdout or "[]"))
    except json.JSONDecodeError:
        return -1


def tests_preserved(baseline: dict, current: dict) -> bool:
    """Reject crashes, collection loss, skipped prior passes and new failures."""
    return bool(
        baseline.get("report_valid") and current.get("report_valid")
        and baseline.get("returncode") in (0, 1) and current.get("returncode") in (0, 1)
        and baseline.get("passed", 0) > 0 and current.get("errors", 0) == 0
        and set(baseline.get("passed_ids", [])) <= set(current.get("passed_ids", []))
        and set(baseline.get("collected_ids", [])) <= set(current.get("collected_ids", []))
        and not (set(current.get("failed_ids", [])) - set(baseline.get("failed_ids", [])))
    )


def duplicate_lines_in_file(source_dir: str, repo_root: str, rel_file: str) -> int:
    sources = {}
    for path in iter_python_files(source_dir):
        with open(path, encoding="utf-8") as fh:
            sources[canonical_path(os.path.relpath(path, repo_root))] = fh.read()
    return len(find_duplicate_lines(sources).get(canonical_path(rel_file), set()))
