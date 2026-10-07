"""
Evaluate — Phase 5: compare prioritisation methods against ground truth.

Methods (higher score = fix first):
  static_score        Phase 1 composite static score (primary baseline)
  complexity          raw cyclomatic complexity (the plan's baseline)
  context_heuristic   non-LLM equal-weight mix of static + context features
                      (tests whether an LLM is needed at all)
  llm                 LLM + repository context + RAG (the proposed method)
  llm_nocontext       same LLM, static metrics + code only (ablation)

Ground truths:
  defects   bug-fix commits touching the function *after* the cutoff
  changes   all non-sweeping commits touching it after the cutoff
  experts   mean 1–10 urgency score from human raters (sampled items)
"""

from __future__ import annotations

import math
from typing import Any

from evaluation.metrics import (bootstrap_ci, kendall, ndcg_at_k, order_from_scores,
                                precision_at_k, resampled_spearman, roc_auc, spearman,
                                topk_overlap)

METHOD_LABELS = {
    "static_score": "Static analysis (composite score)",
    "complexity": "Static analysis (complexity only)",
    "context_heuristic": "Context heuristic (no LLM)",
    "llm": "LLM + RAG context (proposed)",
    "llm_nocontext": "LLM without context (ablation)",
}

_HEURISTIC_FEATURES = (
    ("static_score", lambda x: x["static_score"]),
    ("callers", lambda x: math.log1p(x["callers"])),
    ("func_fix_commits", lambda x: math.log1p(x["func_fix_commits"])),
    ("func_commits", lambda x: math.log1p(x["func_commits"])),
    ("file_defects", lambda x: math.log1p(x["defect_commits"])),
    ("coverage_gap", lambda x: 100.0 - x["function_coverage"]),
)


def context_heuristic_scores(items: list[dict]) -> dict[str, float]:
    """Equal-weight mean of min–max normalised features."""
    columns = {name: [f(x) for x in items] for name, f in _HEURISTIC_FEATURES}
    scores = {x["id"]: 0.0 for x in items}
    for values in columns.values():
        lo, hi = min(values), max(values)
        span = (hi - lo) or 1.0
        for x, v in zip(items, values):
            scores[x["id"]] += (v - lo) / span / len(columns)
    return {k: round(v, 5) for k, v in scores.items()}


def collect_methods(items: list[dict], llm: list[dict] | None,
                    llm_nocontext: list[dict] | None) -> dict[str, dict[str, Any]]:
    """Return {method: {"scores": {id: score}, "order": [ids]}}."""
    methods: dict[str, dict[str, Any]] = {}
    static = {x["id"]: x["static_score"] for x in items}
    methods["static_score"] = {
        "scores": static,
        "order": [x["id"] for x in sorted(items, key=lambda x: x["static_rank"])],
    }
    cx = {x["id"]: x["complexity"] for x in items}
    methods["complexity"] = {"scores": cx, "order": order_from_scores(cx)}
    ch = context_heuristic_scores(items)
    methods["context_heuristic"] = {"scores": ch, "order": order_from_scores(ch)}
    for name, results in (("llm", llm), ("llm_nocontext", llm_nocontext)):
        ok = [r for r in (results or []) if "error" not in r and "llm_rank" in r]
        if ok:
            methods[name] = {
                "scores": {r["id"]: r["priority_score"] for r in ok},
                "order": [r["id"] for r in sorted(ok, key=lambda r: r["llm_rank"])],
            }
    return methods


def _restrict(method: dict, ids: set[str]) -> dict:
    return {"scores": {k: v for k, v in method["scores"].items() if k in ids},
            "order": [i for i in method["order"] if i in ids]}


def evaluate_against_defects(methods: dict, ground_truth: dict, ks=(5, 10, 20)) -> dict:
    ids = set.intersection(*(set(m["scores"]) for m in methods.values())) & set(ground_truth)
    fixes = {i: float(ground_truth[i]["future_fix_commits"]) for i in ids}
    changes = {i: float(ground_truth[i]["future_commits"]) for i in ids}
    labels = {i: ground_truth[i]["future_defect"] for i in ids}
    relevant = {i for i in ids if labels[i]}
    out: dict[str, Any] = {"n_items": len(ids), "n_positive": len(relevant), "methods": {}}
    for name, method in methods.items():
        m = _restrict(method, ids)
        row = {
            "spearman_vs_fix_commits": spearman(m["scores"], fixes),
            "spearman_vs_change_commits": spearman(m["scores"], changes),
            "auc_future_defect": roc_auc(m["scores"], labels),
            "ndcg@10_fix_commits": ndcg_at_k(m["order"], fixes, 10),
            "ndcg@10_change_commits": ndcg_at_k(m["order"], changes, 10),
        }
        for k in ks:
            if k <= len(ids):
                row[f"precision@{k}"] = precision_at_k(m["order"], relevant, k)
        lo, hi = bootstrap_ci(sorted(ids), lambda s, sc=m["scores"]: resampled_spearman(sc, changes, s))
        row["spearman_vs_change_commits_ci95"] = [lo, hi]
        out["methods"][name] = row
    return out


def evaluate_against_experts(methods: dict, expert_scores: dict[str, float],
                             ks=(5, 10)) -> dict:
    ids = set(expert_scores).intersection(*(set(m["scores"]) for m in methods.values()))
    if len(ids) < 3:
        return {"n_items": len(ids), "methods": {}, "note": "not enough expert-rated items"}
    expert = {i: expert_scores[i] for i in ids}
    expert_order = order_from_scores(expert)
    out: dict[str, Any] = {"n_items": len(ids), "methods": {}}
    for name, method in methods.items():
        m = _restrict(method, ids)
        row = {
            "spearman": spearman(m["scores"], expert),
            "kendall_tau": kendall(m["scores"], expert),
            "ndcg@10": ndcg_at_k(m["order"], expert, 10),
        }
        for k in ks:
            if k < len(ids):
                row[f"precision@{k}"] = topk_overlap(m["order"], expert_order, k)
        lo, hi = bootstrap_ci(sorted(ids), lambda s, sc=m["scores"]: resampled_spearman(sc, expert, s))
        row["spearman_ci95"] = [lo, hi]
        out["methods"][name] = row
    return out


def paired_difference(method_a: dict, method_b: dict, truth: dict[str, float]) -> dict:
    """Bootstrap CI of ρ(a, truth) − ρ(b, truth) on shared items."""
    ids = sorted(set(method_a["scores"]) & set(method_b["scores"]) & set(truth))

    def diff(sample: list[str]) -> float | None:
        ra = resampled_spearman(method_a["scores"], truth, sample)
        rb = resampled_spearman(method_b["scores"], truth, sample)
        return None if ra is None or rb is None else ra - rb

    shared_truth = {i: truth[i] for i in ids}
    point_a = spearman(method_a["scores"], shared_truth)
    point_b = spearman(method_b["scores"], shared_truth)
    lo, hi = bootstrap_ci(ids, diff)
    point = None if point_a is None or point_b is None else round(point_a - point_b, 4)
    return {"delta_spearman": point, "ci95": [lo, hi],
            "significant": lo is not None and (lo > 0 or hi < 0)}
