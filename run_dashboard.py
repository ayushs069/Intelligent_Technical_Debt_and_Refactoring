#!/usr/bin/env python3
"""
run_dashboard.py — build the self-contained HTML research dashboard.

Reads every data file the pipeline has produced so far (dataset, rankings,
evaluation, refactoring, expert sample) and bakes it into one HTML file that
opens in any browser — no server needed.  Re-run after each phase.

Usage:
    python run_dashboard.py                 # -> reports/<repo>_dashboard.html
    python run_dashboard.py --repo-name requests

Outputs:
    reports/<repo>_dashboard.html            full page, open it directly
    reports/<repo>_dashboard_artifact.html   same page without the document
                                             skeleton (for publishing as an Artifact)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import config
from agents.prompts import read_function_source
from evaluation.evaluate import collect_methods
from evaluation.expert import load_raters

_NEIGHBOUR_RE = re.compile(
    r"### Result \d+ \(Score: (?P<score>[\d.]+), Distance: (?P<dist>[\d.]+)\)\s*\n"
    r".*?\*\*File\*\*: `(?P<file>[^`]+)`:(?P<line>\d+)\s*\n"
    r"- \*\*Function\*\*: `(?P<fn>[^`]*)`")

TEMPLATE = os.path.join(config.PROJECT_DIR, "dashboard", "template.html")

_ITEM_FIELDS = (
    "id", "file", "function", "line", "complexity", "complexity_rank", "loc", "params",
    "maintainability_index", "code_smells", "smell_types", "tool_messages", "style_issues",
    "duplicate_lines", "dead_code", "static_score", "static_rank", "callers",
    "test_references", "is_private", "func_commits", "func_fix_commits", "recent_commits",
    "defect_commits", "commit_authors", "last_modified_days", "function_coverage",
    "test_coverage", "docstring",
)


def _load(path: str, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_payload(repo: str) -> dict:
    paths = config.dataset_paths(repo)
    items = _load(paths["items"], [])
    if not items:
        raise SystemExit(f"[error] {paths['items']} not found — run run_dataset.py first.")

    slim = []
    for item in items:
        record = {k: item.get(k) for k in _ITEM_FIELDS}
        record["source"] = read_function_source(item)
        slim.append(record)

    refactoring = _load(paths["refactoring"])
    diffs = {}
    if refactoring:
        for r in refactoring.get("results", []):
            slug = r["branch"].split("/", 1)[-1]
            diff_path = os.path.join(config.PROJECT_DIR, "refactoring", "branches", slug, "patch.diff")
            if os.path.isfile(diff_path):
                with open(diff_path, encoding="utf-8") as fh:
                    diffs[r["id"]] = fh.read()[:20000]

    raters = load_raters(config.EXPERT_DIR)
    llm = _load(paths["llm_rankings"], []) or []
    llm_nc = _load(paths["llm_rankings_nocontext"], []) or []
    methods = collect_methods(items, llm, llm_nc)

    neighbours = {}
    retrieval = _load(os.path.join(config.DATA_DIR, f"{repo}_llm_rankings_retrieval.json"), {}) or {}
    for item_id, text in retrieval.items():
        neighbours[item_id] = [
            {"id": f"{m['file']}::{m['fn']}", "distance": float(m["dist"]), "score": float(m["score"])}
            for m in _NEIGHBOUR_RE.finditer(str(text))]

    def run_info(results: list, name: str) -> dict:
        prov = _load(os.path.join(config.DATA_DIR, f"{repo}_{name}_provenance.json"), {}) or {}
        errors = [r for r in results if "error" in r]
        return {"total": len(results), "ok": len(results) - len(errors), "errors": len(errors),
                "first_error": errors[0]["error"] if errors else "",
                "model": prov.get("model") or (results[0].get("model") if results else ""),
                "generated": prov.get("generated", ""),
                "dataset_sha256": prov.get("dataset_sha256", "")}

    baseline = _load(os.path.join(config.DATA_DIR, f"{repo}_test_baseline.json"), {}) or {}
    return {
        "repo": repo,
        "generated": datetime.now(tz=timezone.utc).isoformat(timespec="minutes"),
        "meta": _load(paths["meta"], {}),
        "items": slim,
        "groundTruth": _load(paths["ground_truth"], {}),
        "llm": _load(paths["llm_rankings"], []),
        "llmNoContext": _load(paths["llm_rankings_nocontext"], []),
        "evaluation": _load(paths["evaluation"], {}),
        "refactoring": refactoring or {},
        "diffs": diffs,
        "expertSample": _load(paths["expert_sample"], []),
        "raters": [{"rater": r["rater"], "count": len(r["scores"])} for r in raters],
        "methodScores": {name: m["scores"] for name, m in methods.items()},
        "neighbours": neighbours,
        "runs": {"llm": run_info(llm, "llm_rankings"),
                 "llm_nocontext": run_info(llm_nc, "llm_rankings_nocontext")},
        "readiness": _load(os.path.join(config.DATA_DIR, "readiness.json"), {}) or {},
        "retrievalDiagnostics": _load(
            os.path.join(config.DATA_DIR, f"{repo}_retrieval_diagnostics.json"), {}) or {},
        "testBaseline": {k: baseline.get(k) for k in
                         ("passed", "failed", "errors", "failed_ids", "duration_s")} if baseline else {},
    }


def render(payload: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as fh:
        template = fh.read()
    # "</" is escaped so source code containing "</script>" cannot end the data block.
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("/*__DASHBOARD_DATA__*/null", data)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the HTML research dashboard.")
    parser.add_argument("--repo-name", default=config.DEFAULT_REPO_NAME)
    args = parser.parse_args()

    fragment = render(build_payload(args.repo_name))
    os.makedirs(config.REPORTS_DIR, exist_ok=True)
    artifact_path = os.path.join(config.REPORTS_DIR, f"{args.repo_name}_dashboard_artifact.html")
    page_path = os.path.join(config.REPORTS_DIR, f"{args.repo_name}_dashboard.html")
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write(fragment)
    with open(page_path, "w", encoding="utf-8") as fh:
        fh.write('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                 '<meta name="viewport" content="width=device-width, initial-scale=1, '
                 'viewport-fit=cover">\n</head>\n<body>\n' + fragment + "\n</body>\n</html>\n")
    print(f"[dashboard] Wrote {page_path}")
    print(f"[dashboard] Wrote {artifact_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
