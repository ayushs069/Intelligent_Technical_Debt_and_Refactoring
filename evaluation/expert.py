"""
Expert rankings — sampling items for human raters and aggregating their scores.

Raters score each sampled item 1–10 ("how urgently should this be refactored?")
using the dashboard's *Expert ranking* tab (or by editing a JSON file).  Each
rater's file lives in ``evaluation/expert_rankings/<rater>.json``:

    {"rater": "alice", "scores": {"<item id>": 7, ...}, "notes": {...}}

The consensus is the mean score per item; agreement is reported as Kendall's W
and mean pairwise Spearman ρ.  Raters should not see the LLM or static ranks.
"""

from __future__ import annotations

import glob
import itertools
import json
import os
import random
import re

from evaluation.metrics import kendalls_w, spearman


def make_expert_sample(items: list[dict], size: int, seed: int = 42) -> list[str]:
    """
    Stratified random sample: items are split into tertiles by static score and
    an equal share is drawn from each, so the sample is not dominated by the
    static ranking's favourites.
    """
    rng = random.Random(seed)
    ordered = sorted(items, key=lambda x: (-x["static_score"], x["id"]))
    size = min(size, len(ordered))
    third = len(ordered) / 3
    strata = [ordered[round(i * third): round((i + 1) * third)] for i in range(3)]
    picks: list[str] = []
    for i, stratum in enumerate(strata):
        want = size // 3 + (1 if i < size % 3 else 0)
        picks += [x["id"] for x in rng.sample(stratum, min(want, len(stratum)))]
    rng.shuffle(picks)
    return picks


def _safe_name(rater: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", rater.strip()) or "rater"


def save_rater(directory: str, rater: str, scores: dict[str, int],
               notes: dict[str, str] | None = None) -> str:
    validate_rater({"rater": rater, "scores": scores, "notes": notes or {}})
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{_safe_name(rater)}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"rater": rater, "scores": scores, "notes": notes or {}}, fh, indent=2)
    return path


def validate_rater(data: dict, allowed_ids: set[str] | None = None) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("rater"), str) or not data["rater"].strip():
        raise ValueError("A nonempty human reviewer name is required")
    scores = data.get("scores")
    if not isinstance(scores, dict) or not scores:
        raise ValueError("Provide at least one independently reviewed score")
    for item_id, score in scores.items():
        if not isinstance(item_id, str) or type(score) is not int or not 1 <= score <= 10:
            raise ValueError("Scores must be integers from 1 to 10 keyed by item ID")
        if allowed_ids is not None and item_id not in allowed_ids:
            raise ValueError(f"Unknown sample item: {item_id}")
    notes = data.get("notes", {})
    if not isinstance(notes, dict) or any(not isinstance(v, str) for v in notes.values()):
        raise ValueError("Notes must be a mapping of item IDs to text")
    return data


def load_raters(directory: str) -> list[dict]:
    raters = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("scores"):
            validate_rater(data)
            raters.append(data)
    return raters


def consensus(raters: list[dict], min_raters: int = 1) -> dict[str, float]:
    """Mean score per item over raters who scored it."""
    pooled: dict[str, list[float]] = {}
    for r in raters:
        for item_id, score in r["scores"].items():
            pooled.setdefault(item_id, []).append(float(score))
    return {i: round(sum(v) / len(v), 3) for i, v in pooled.items() if len(v) >= min_raters}


def agreement(raters: list[dict]) -> dict:
    """Inter-rater agreement statistics."""
    score_sets = [{k: float(v) for k, v in r["scores"].items()} for r in raters]
    pairs = [spearman(a, b) for a, b in itertools.combinations(score_sets, 2)]
    pairs = [p for p in pairs if p is not None]
    return {
        "raters": [r["rater"] for r in raters],
        "kendalls_w": kendalls_w(score_sets),
        "mean_pairwise_spearman": round(sum(pairs) / len(pairs), 4) if pairs else None,
    }
