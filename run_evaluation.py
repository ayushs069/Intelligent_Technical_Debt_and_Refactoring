#!/usr/bin/env python3
"""
run_evaluation.py — Phase 5: measure prioritisation quality.

Usage:
    # 1. Draw the stratified item sample that human raters will score
    python run_evaluation.py --make-expert-sample

    # 2. Raters score the sample in the dashboard (streamlit run dashboard/app.py)
    #    -> evaluation/expert_rankings/<rater>.json

    # 3. Compute all metrics and write the report
    python run_evaluation.py

Outputs:
    data/<repo>_evaluation_results.json
    reports/<repo>_evaluation_report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import config
from evaluation.evaluate import (METHOD_LABELS, collect_methods, evaluate_against_defects,
                                 evaluate_against_experts, paired_difference)
from evaluation.expert import agreement, consensus, load_raters, make_expert_sample


def _load(path: str, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def _table(section: dict, columns: list[str]) -> str:
    lines = ["| Method | " + " | ".join(columns) + " |",
             "|---|" + "---|" * len(columns)]
    for name, row in section["methods"].items():
        lines.append(f"| {METHOD_LABELS.get(name, name)} | "
                     + " | ".join(_fmt(row.get(c)) for c in columns) + " |")
    return "\n".join(lines)


def _research_statement(results: dict, refactoring: dict | None) -> str:
    if "llm" not in results.get("methods", []):
        return ("> Static baselines have been measured, but there is no completed LLM experiment. "
                "The research question remains unanswered; no claim of LLM improvement is supported.")
    experts = results.get("experts", {}).get("methods", {})
    basis = "expert developer rankings"
    source = experts
    if "llm" not in source or source["llm"].get("spearman") is None:
        source = results["defects"]["methods"]
        basis = "post-cutoff change history"
        rho_key, p_key = "spearman_vs_change_commits", "precision@10"
    else:
        rho_key, p_key = "spearman", "precision@10"
    llm, static = source.get("llm", {}), source.get("static_score", {})
    statement = (
        f"> LLM-based prioritisation of technical debt, augmented with repository context "
        f"retrieved via RAG, achieves a Spearman correlation of {_fmt(llm.get(rho_key))} with "
        f"{basis} — compared to {_fmt(static.get(rho_key))} for static analysis alone — and a "
        f"Precision@10 of {_fmt(llm.get(p_key))} vs {_fmt(static.get(p_key))}."
    )
    if refactoring and refactoring.get("summary"):
        s = refactoring["summary"]
        statement += (
            f" LLM-generated refactoring patches pass CI validation at a rate of "
            f"{s['success_rate'] * 100:.0f}%, with an average cyclomatic complexity reduction "
            f"of {_fmt(s['mean_complexity_reduction_target'])} points per refactored function.")
    return statement


def write_report(path: str, repo: str, meta: dict, results: dict,
                 refactoring: dict | None) -> None:
    d = results["defects"]
    parts = [
        f"# Evaluation report — {repo}",
        f"_Generated {results['generated']}_",
        "## Setup",
        f"- Snapshot: `{meta.get('snapshot_sha', '?')[:8]}` (last commit before "
        f"{meta.get('as_of', '?')[:10]}); context history from {meta.get('history_since', '?')[:10]}.",
        f"- Ground-truth window: {meta.get('as_of', '?')[:10]} → {meta.get('horizon', '?')[:10]}.",
        f"- Candidate debt items: {d['n_items']} functions; {d['n_positive']} received a "
        f"bug-fix commit after the cutoff.",
        f"- Methods compared: {', '.join(METHOD_LABELS.get(m, m) for m in results['methods'])}.",
        "## 1. Against expert rankings",
    ]
    if results.get("excluded_experiments"):
        parts.insert(-1, "### Incomplete experiments\n\n" + "\n".join(
            f"- {note}" for note in results["excluded_experiments"]))
    e = results.get("experts")
    if e and e.get("methods"):
        ag = results.get("expert_agreement", {})
        parts.append(f"{e['n_items']} items rated by {len(ag.get('raters', []))} rater(s) "
                     f"({', '.join(ag.get('raters', []))}); Kendall's W = "
                     f"{_fmt(ag.get('kendalls_w'))}, mean pairwise ρ = "
                     f"{_fmt(ag.get('mean_pairwise_spearman'))}.")
        parts.append(_table(e, ["spearman", "spearman_ci95", "kendall_tau", "precision@5",
                                "precision@10", "ndcg@10"]))
    else:
        parts.append("_No expert ratings yet — collect them in the dashboard's Expert ranking tab._")
    parts += [
        "## 2. Against post-cutoff defect history",
        "Precision@K = share of a method's top-K items that received a bug-fix commit after the "
        "cutoff. AUC = probability that a defect-fixed item outranks a non-fixed one.",
        _table(d, ["precision@5", "precision@10", "precision@20", "auc_future_defect",
                   "spearman_vs_fix_commits", "ndcg@10_fix_commits"]),
        "## 3. Against post-cutoff change-proneness",
        _table(d, ["spearman_vs_change_commits", "spearman_vs_change_commits_ci95",
                   "ndcg@10_change_commits"]),
        "## 4. Paired comparisons (bootstrap 95% CI of Δρ)",
    ]
    if results.get("paired"):
        parts.append("| Comparison | Ground truth | Δρ | 95% CI | Significant |\n|---|---|---|---|---|")
        for row in results["paired"]:
            parts.append(f"| {row['comparison']} | {row['truth']} | {_fmt(row['delta_spearman'])} "
                         f"| {_fmt(row['ci95'])} | {'yes' if row['significant'] else 'no'} |")
    else:
        parts.append("_Run Phase 4 (run_prioritisation.py) to add the LLM methods._")
    if refactoring and refactoring.get("summary"):
        s = refactoring["summary"]
        parts += [
            "## 5. Refactoring + CI (Phase 6)",
            f"- Attempted: {s['attempted']}; succeeded (no new test failures and lower "
            f"complexity): {s['succeeded']} → success rate {s['success_rate'] * 100:.0f}%",
            f"- Test pass rate (no regressions): {s['test_pass_rate'] * 100:.0f}%",
            f"- Mean complexity reduction (target function): {_fmt(s['mean_complexity_reduction_target'])}; "
            f"(whole replaced block incl. helpers): {_fmt(s['mean_complexity_reduction_block'])}",
            f"- Mean Ruff findings Δ per file: {_fmt(s['mean_ruff_delta'])}; "
            f"duplicated-lines Δ: {_fmt(s['mean_duplicate_delta'])}",
        ]
    parts += [
        "## Research contribution statement",
        _research_statement(results, refactoring),
        "## Threats to validity",
        "- Defect labels come from commit-message keywords mapped to changed functions; "
        "some fixes are missed or mislabelled.",
        "- Few post-cutoff fixes in a mature library make defect-based metrics noisy — read the "
        "confidence intervals, not just point estimates.",
        "- Independent expert ratings are pending unless explicitly reported above; historical fixes are a proxy for debt urgency.",
        "- One repository and one snapshot limit generalisation. The model may have seen public repository code during training.",
        "- The context heuristic uses arbitrary equal weights; it is a sanity baseline, not a "
        "tuned model.",
        "- LLM output can vary between runs; responses are cached so the reported run is "
        "reproducible from data/llm_cache.",
        "- Matched comparisons require the same model, agent mode, item set and prompt rubric. Report incomplete runs explicitly.",
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(parts) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5 — evaluation.")
    parser.add_argument("--repo-name", default=config.DEFAULT_REPO_NAME)
    parser.add_argument("--make-expert-sample", action="store_true",
                        help="Draw the stratified sample for human raters and exit.")
    parser.add_argument("--sample-size", type=int, default=config.EXPERT_SAMPLE_SIZE)
    parser.add_argument("--force", action="store_true", help="Overwrite an existing sample.")
    args = parser.parse_args()

    paths = config.dataset_paths(args.repo_name)
    items = _load(paths["items"])
    if not items:
        print(f"[error] {paths['items']} not found — run run_dataset.py first.")
        return 1

    if args.make_expert_sample:
        if os.path.isfile(paths["expert_sample"]) and not args.force:
            print(f"[eval] Sample already exists: {paths['expert_sample']} (use --force to redraw "
                  "— this invalidates existing ratings).")
            return 0
        sample = make_expert_sample(items, args.sample_size)
        with open(paths["expert_sample"], "w", encoding="utf-8") as fh:
            json.dump(sample, fh, indent=2)
        print(f"[eval] Wrote {len(sample)} item ids -> {paths['expert_sample']}")
        return 0

    ground_truth = _load(paths["ground_truth"], {})
    meta = _load(paths["meta"], {})
    ids = {x["id"] for x in items}
    exclusions = []
    def complete_ranking(key):
        rows = _load(paths[key], [])
        good = [r for r in rows if "llm_rank" in r and "error" not in r]
        provenance = _load(paths[key].replace(".json", "_provenance.json"), {})
        digest = hashlib.sha256(Path(paths["items"]).read_bytes()).hexdigest()
        if len(good) != len(items) or {r['id'] for r in good} != ids:
            exclusions.append(f"{key}: {len(good)}/{len(items)} successful items; excluded from full-dataset comparison")
            return None
        if provenance and provenance.get('dataset_sha256') != digest:
            exclusions.append(f"{key}: dataset fingerprint changed; rerun this experiment")
            return None
        return good

    with_context = complete_ranking("llm_rankings")
    without_context = complete_ranking("llm_rankings_nocontext")
    if with_context and without_context:
        def modes(rows):
            return {(r.get('model'), r.get('mode'), r.get('backend')) for r in rows}

        if modes(with_context) != modes(without_context) or len(modes(with_context)) != 1:
            exclusions.append("No-context run excluded: model, agent mode or backend differs from the context run")
            without_context = None
    methods = collect_methods(items, with_context, without_context)

    results: dict = {
        "generated": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "methods": list(methods),
        "excluded_experiments": exclusions,
        "defects": evaluate_against_defects(methods, ground_truth, config.PRECISION_KS),
    }

    raters = load_raters(config.EXPERT_DIR)
    expert_scores = consensus(raters) if raters else {}
    if expert_scores:
        results["experts"] = evaluate_against_experts(methods, expert_scores)
        results["expert_agreement"] = agreement(raters)

    changes = {i: float(g["future_commits"]) for i, g in ground_truth.items()}
    paired = []
    for a, b in (("llm", "static_score"), ("llm", "complexity"), ("llm", "llm_nocontext"),
                 ("llm", "context_heuristic")):
        if a in methods and b in methods:
            for truth_name, truth in (("experts", expert_scores), ("changes", changes)):
                if len(truth) >= 3:
                    paired.append({"comparison": f"{a} vs {b}", "truth": truth_name,
                                   **paired_difference(methods[a], methods[b], truth)})
    results["paired"] = paired
    for note in exclusions:
        print(f"[eval] {note}")

    with open(paths["evaluation"], "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    report = os.path.join(config.REPORTS_DIR, f"{args.repo_name}_evaluation_report.md")
    write_report(report, args.repo_name, meta, results, _load(paths["refactoring"]))

    print(f"\n{'='*60}\n  EVALUATION — {args.repo_name}\n{'='*60}")
    print(f"  Items: {results['defects']['n_items']} | with future defect: "
          f"{results['defects']['n_positive']} | expert-rated: {len(expert_scores)}")
    print(f"\n  {'Method':34s} {'P@10(def)':>9} {'AUC':>6} {'rho(chg)':>8} {'rho(exp)':>8}")
    for name in methods:
        d = results["defects"]["methods"][name]
        ex = results.get("experts", {}).get("methods", {}).get(name, {})
        print(f"  {METHOD_LABELS.get(name, name):34s} {_fmt(d.get('precision@10')):>9} "
              f"{_fmt(d.get('auc_future_defect')):>6} {_fmt(d.get('spearman_vs_change_commits')):>8} "
              f"{_fmt(ex.get('spearman')):>8}")
    if "llm" not in methods:
        print("\n  (LLM methods missing — run run_prioritisation.py first.)")
    if not expert_scores:
        print("  (No expert ratings yet — run --make-expert-sample, then rate in the dashboard.)")
    print(f"\n  Saved -> {paths['evaluation']}\n  Report -> {report}\n{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
