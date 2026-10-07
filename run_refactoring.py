#!/usr/bin/env python3
"""
run_refactoring.py — Phase 6: LLM refactoring + CI validation.

Takes the top-N items from the Phase 4 LLM ranking, generates a refactoring for
each on its own branch (ai-refactoring/<function>) in the target clone, and
validates it with the CI gate (tests vs. baseline, Radon, Ruff, duplication).

Usage:
    python run_refactoring.py --test-python targets/.venv-requests/Scripts/python
    python run_refactoring.py --dry-run          # show the selected items only

Branches are created locally in the target clone and never pushed.  To run the
same checks on GitHub Actions, push a branch to your fork together with
refactoring/ci/target-techdebt.yml (see README).

Outputs:
    data/<repo>_refactoring_results.json
    refactoring/branches/<function>/{patch.diff,result.json}
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import config
from llm.client import make_client
from refactoring.refactor import baseline_tests, refactor_item, select_items, summarise


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 6 — refactoring + CI validation.")
    parser.add_argument("--repo-name", default=config.DEFAULT_REPO_NAME)
    parser.add_argument("--test-python", default="",
                        help="Interpreter with the target's test dependencies.")
    parser.add_argument("--top-n", type=int, default=config.REFACTOR_TOP_N)
    parser.add_argument("--max-attempts", type=int, default=config.REFACTOR_MAX_ATTEMPTS)
    parser.add_argument("--model", default=config.LLM_MODEL)
    parser.add_argument("--effort", default="high",
                        choices=("low", "medium", "high", "xhigh", "max"),
                        help="Code generation benefits from more effort (default high).")
    parser.add_argument("--dry-run", action="store_true", help="Only list the selected items.")
    args = parser.parse_args()
    if args.top_n < 1 or args.max_attempts < 1:
        parser.error("top-n and max-attempts must be positive")

    paths = config.dataset_paths(args.repo_name)
    for key in ("items", "meta", "llm_rankings"):
        if not os.path.isfile(paths[key]):
            hint = "run_prioritisation.py" if key == "llm_rankings" else "run_dataset.py"
            print(f"[error] {paths[key]} not found — run {hint} first.")
            return 1
    items, meta = _load(paths["items"]), _load(paths["meta"])
    selected = select_items(items, _load(paths["llm_rankings"]), args.top_n)

    print(f"\n{'='*60}\n  Refactoring + CI — Phase 6 ({args.repo_name})\n{'='*60}")
    for item in selected:
        rec = item["recommendation"]
        print(f"  #{rec['llm_rank']:<3} [{rec['priority']}] {item['id']} (cc={item['complexity']})")
        print(f"        {rec['action'][:150]}")
    if args.dry_run:
        return 0
    if not selected:
        print("[error] No successful LLM rankings are available. Complete prioritisation first.")
        return 1
    if not args.test_python or not os.path.isfile(args.test_python):
        print("[error] --test-python must point to an interpreter with the target's test deps.")
        return 1

    test_python = os.path.abspath(args.test_python)
    src_root = os.path.dirname(meta["source_subdir"]) or "."
    baseline = baseline_tests(
        os.path.join(config.DATA_DIR, f"{args.repo_name}_test_baseline.json"),
        meta["snapshot_dir"], test_python, src_root, meta["tests_subdir"], meta["snapshot_sha"])
    print(f"[refactor] Baseline: {baseline['passed']} passed, {baseline['failed']} failed "
          f"(pre-existing failures are not counted against patches)")
    if not baseline.get("report_valid") or baseline["returncode"] not in (0, 1) or baseline["passed"] == 0 or baseline["errors"]:
        print("[error] Baseline test suite did not complete reliably. Fix the target test environment first.")
        return 1

    llm = make_client(args.model, args.effort, config.LLM_CACHE_DIR)
    worktree_root = os.path.join(os.path.dirname(meta["git_repo"]), "branches")
    output_root = os.path.join(config.PROJECT_DIR, "refactoring", "branches")
    results = []
    for n, item in enumerate(selected, 1):
        print(f"\n[{n}/{len(selected)}] {item['id']}")
        res = refactor_item(
            item, llm, git_repo=meta["git_repo"], snapshot_sha=meta["snapshot_sha"],
            worktree_root=worktree_root, output_root=output_root, test_python=test_python,
            src_root=src_root, source_subdir=meta["source_subdir"],
            tests_subdir=meta["tests_subdir"], baseline=baseline,
            branch_prefix=config.BRANCH_PREFIX, max_attempts=args.max_attempts)
        verdict = "SUCCESS" if res["success"] else "FAILED"
        print(f"    -> {verdict} on branch {res['branch']}")
        results.append(res)

    payload = {"model": args.model, "baseline": {k: baseline[k] for k in ("passed", "failed")},
               "summary": summarise(results), "results": results}
    with open(paths["refactoring"], "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    s = payload["summary"]
    print(f"\n{'='*60}\n  SUMMARY\n{'='*60}")
    print(f"  Success rate        : {s['succeeded']}/{s['attempted']} = {s['success_rate']:.0%}")
    print(f"  Test pass rate      : {s['test_pass_rate']:.0%}")
    print(f"  Complexity reduction: {s['mean_complexity_reduction_target']} (target fn), "
          f"{s['mean_complexity_reduction_block']} (incl. helpers)")
    print(f"  Ruff delta / dup delta: {s['mean_ruff_delta']} / {s['mean_duplicate_delta']}")
    print(f"  LLM usage           : {llm.usage.summary(args.model)}")
    print(f"  Saved -> {paths['refactoring']}\n  Re-run run_evaluation.py to add these to the report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
