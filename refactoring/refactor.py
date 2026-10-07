"""
Refactor — Phase 6: LLM-generated refactorings validated by a CI gate.

For each selected item:
  1. create branch ``ai-refactoring/<function>`` from the analysis snapshot in
     its own git worktree,
  2. ask the Refactoring agent to rewrite the function (given the code, the
     Phase 4 recommendation and the repository context),
  3. apply the patch and run the CI gate (tests vs. baseline, Radon, Ruff,
     duplication),
  4. on failure, feed the failure back and retry (up to ``max_attempts``),
  5. commit the final attempt on the branch and save the diff + result.

Success = the module parses, no test that passed on the baseline now fails,
and the target function's cyclomatic complexity is strictly lower.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from typing import Any

from agents.agents import refactor_code_agent
from agents.prompts import read_function_source, render_item
from llm.client import LLMClient, LLMError
from refactoring.code_utils import (PatchError, block_complexity, function_complexity,
                                    function_names_in_block, replace_function)
from refactoring.validate import duplicate_lines_in_file, ruff_findings, run_tests, tests_preserved

_BOT = ["-c", "user.name=techdebt-refactor-agent", "-c", "user.email=refactor-agent@localhost"]


def _git(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def slugify(qualname: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", qualname.lower()).strip("-")


def select_items(items: list[dict], rankings: list[dict], top_n: int) -> list[dict]:
    """Top-N by LLM rank, preferring HIGH priority and skipping 'no_action'."""
    by_id = {x["id"]: x for x in items}
    ranked = [r for r in rankings if "error" not in r and r["id"] in by_id
              and r.get("refactoring_type") != "no_action"]
    ranked.sort(key=lambda r: r["llm_rank"])
    high = [r for r in ranked if r["priority"] == "HIGH"]
    chosen = (high + [r for r in ranked if r not in high])[:top_n]
    return [{**by_id[r["id"]], "recommendation": r} for r in chosen]


def prepare_worktree(git_repo: str, snapshot_sha: str, branch: str, path: str) -> None:
    if os.path.exists(path):
        raise RuntimeError(f"Refusing to overwrite an existing worktree: {path}")
    _git(git_repo, "worktree", "add", "-b", branch, os.path.abspath(path), snapshot_sha)


def baseline_tests(cache_path: str, snapshot_dir: str, test_python: str, src_root: str,
                   tests_subdir: str, snapshot_sha: str) -> dict:
    """Run (or load cached) tests on the unmodified snapshot."""
    if os.path.isfile(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        if (cached.get("snapshot_sha") == snapshot_sha and cached.get("report_valid")
                and cached.get("test_python") == os.path.abspath(test_python)):
            return cached
    print("[refactor] Running baseline test suite on the unmodified snapshot …")
    result = {**run_tests(snapshot_dir, test_python, src_root, tests_subdir),
              "snapshot_sha": snapshot_sha, "test_python": os.path.abspath(test_python)}
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    return result


def _build_prompt(item: dict, source: str, feedback: list[str]) -> str:
    rec = item["recommendation"]
    context = render_item(item, source, with_context=True)
    prompt = (
        f"{context}\n\n"
        f"Phase 4 prioritisation: {rec['priority']} ({rec['priority_score']}/100) — {rec['reason']}\n"
        f"Recommended refactoring ({rec.get('refactoring_type', 'other')}): {rec['action']}\n\n"
        f"Rewrite `{item['function']}` now. The target's current cyclomatic complexity is "
        f"{item['complexity']}."
    )
    if feedback:
        prompt += ("\n\nYour previous attempt was rejected by CI:\n"
                   + "\n".join(f"- {f}" for f in feedback)
                   + "\nFix these problems in a new attempt (start again from the original code).")
    return prompt


def refactor_item(
    item: dict,
    llm: LLMClient,
    *,
    git_repo: str,
    snapshot_sha: str,
    worktree_root: str,
    output_root: str,
    test_python: str,
    src_root: str,
    source_subdir: str,
    tests_subdir: str,
    baseline: dict,
    branch_prefix: str,
    max_attempts: int,
) -> dict[str, Any]:
    slug = slugify(item["function"])
    if (os.path.exists(os.path.join(worktree_root, slug))
            or _git(git_repo, "branch", "--list", f"{branch_prefix}/{slug}").strip()):
        slug += "-" + uuid.uuid4().hex[:8]
    branch = f"{branch_prefix}/{slug}"
    worktree = os.path.join(worktree_root, slug)
    out_dir = os.path.join(output_root, slug)
    os.makedirs(out_dir, exist_ok=True)
    prepare_worktree(git_repo, snapshot_sha, branch, worktree)

    target = os.path.join(worktree, item["rel_file"])
    if os.path.commonpath([os.path.realpath(target), os.path.realpath(worktree)]) != os.path.realpath(worktree):
        raise PatchError("Target source path escapes its worktree")
    with open(target, encoding="utf-8") as fh:
        original = fh.read()
    source_dir = os.path.join(worktree, source_subdir)
    before = {
        "complexity_target": function_complexity(original, item["function"]),
        "ruff_findings": ruff_findings(target),
        "duplicate_lines": duplicate_lines_in_file(source_dir, worktree, item["rel_file"]),
    }
    baseline_failed = set(baseline.get("failed_ids", []))
    func_source = read_function_source(item)

    result: dict[str, Any] = {
        "id": item["id"], "function": item["function"], "file": item["file"],
        "branch": branch, "llm_rank": item["recommendation"]["llm_rank"],
        "priority": item["recommendation"]["priority"],
        "recommendation": item["recommendation"]["action"],
        "before": before, "attempts": [], "success": False,
    }
    feedback: list[str] = []
    for attempt in range(1, max_attempts + 1):
        record: dict[str, Any] = {"attempt": attempt}
        result["attempts"].append(record)
        try:
            proposal = refactor_code_agent.run(llm, _build_prompt(item, func_source, feedback))
        except LLMError as exc:
            record["error"] = f"LLM error: {exc}"
            break
        record["explanation"] = proposal["explanation"]
        try:
            patched, b_start, b_end = replace_function(original, item["function"],
                                                       proposal["new_code"])
        except PatchError as exc:
            record["error"] = str(exc)
            feedback = [f"The code could not be applied: {exc}"]
            continue

        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(patched)
        after = {
            "complexity_target": function_complexity(patched, item["function"]),
            "complexity_block": block_complexity(patched, b_start, b_end),
            "functions_in_block": function_names_in_block(patched, b_start, b_end),
            "ruff_findings": ruff_findings(target),
            "duplicate_lines": duplicate_lines_in_file(source_dir, worktree, item["rel_file"]),
        }
        print(f"    attempt {attempt}: cc {before['complexity_target']} -> "
              f"{after['complexity_target']} (block {after['complexity_block']}); running tests …")
        tests = run_tests(worktree, test_python, src_root, tests_subdir)
        new_failures = sorted(set(tests["failed_ids"]) - baseline_failed)
        tests_ok = tests_preserved(baseline, tests)
        quality_ok = (before["ruff_findings"] >= 0 and after["ruff_findings"] >= 0
                      and after["ruff_findings"] <= before["ruff_findings"]
                      and after["duplicate_lines"] <= before["duplicate_lines"])
        lower = after["complexity_target"] < before["complexity_target"]
        record.update(after=after, tests={k: tests[k] for k in
                                          ("passed", "failed", "errors", "duration_s")},
                      new_failures=new_failures, tests_ok=tests_ok, complexity_lower=lower,
                      quality_ok=quality_ok)
        if tests_ok and lower and quality_ok:
            result["success"] = True
            break
        feedback = []
        if not tests_ok:
            shown = ", ".join(new_failures[:10]) or "the test run did not complete"
            feedback.append(f"Tests that passed before now fail: {shown}\n{tests['tail'][-1500:]}")
        if not lower:
            feedback.append(f"Complexity of {item['function']} did not decrease "
                            f"({before['complexity_target']} -> {after['complexity_target']}).")
        if not quality_ok:
            feedback.append("Ruff or duplication worsened (or the quality tool failed); fix the new findings.")

    final = next((a for a in reversed(result["attempts"]) if "after" in a), None)
    if final:
        status = "passed CI gate" if result["success"] else "FAILED CI gate"
        _git(worktree, "add", item["rel_file"])
        _git(worktree, *_BOT, "commit", "--allow-empty", "-q", "-m",
             f"refactor({item['function']}): reduce technical debt [{status}]\n\n"
             f"{final.get('explanation', '')}\n\nGenerated by the LLM Refactoring agent; "
             f"recommendation: {item['recommendation']['action']}")
        result["after"] = final["after"]
        result["tests"] = final["tests"]
        result["new_failures"] = final["new_failures"]
        result["complexity_reduction_target"] = (before["complexity_target"]
                                                 - final["after"]["complexity_target"])
        result["complexity_reduction_block"] = (before["complexity_target"]
                                                - final["after"]["complexity_block"])
        result["ruff_delta"] = final["after"]["ruff_findings"] - before["ruff_findings"]
        result["duplicate_delta"] = final["after"]["duplicate_lines"] - before["duplicate_lines"]
        diff = _git(worktree, "diff", snapshot_sha, "HEAD")
        with open(os.path.join(out_dir, "patch.diff"), "w", encoding="utf-8") as fh:
            fh.write(diff)
    result["commit"] = _git(worktree, "rev-parse", "HEAD").strip()
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    return result


def summarise(results: list[dict]) -> dict:
    n = len(results)
    applied = [r for r in results if "after" in r]

    def mean(key: str, rows: list[dict]) -> float | None:
        vals = [r[key] for r in rows if key in r]
        return round(sum(vals) / len(vals), 2) if vals else None

    return {
        "attempted": n,
        "applied": len(applied),
        "succeeded": sum(r["success"] for r in results),
        "success_rate": round(sum(r["success"] for r in results) / n, 4) if n else 0.0,
        "test_pass_rate": round(sum(1 for r in applied if any(a.get("tests_ok") for a in r["attempts"])) / n, 4) if n else 0.0,
        "mean_complexity_reduction_target": mean("complexity_reduction_target", applied),
        "mean_complexity_reduction_block": mean("complexity_reduction_block", applied),
        "mean_ruff_delta": mean("ruff_delta", applied),
        "mean_duplicate_delta": mean("duplicate_delta", applied),
        "mean_attempts": round(sum(len(r["attempts"]) for r in results) / n, 2) if n else 0.0,
    }
