"""
Ranking metrics for Phase 5.

Conventions: a *score* dict maps item id -> number where higher means "fix
first"; an *order* is a list of ids from highest to lowest priority.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Callable, Sequence

from scipy.stats import kendalltau, spearmanr


def _aligned(a: dict[str, float], b: dict[str, float]) -> tuple[list[float], list[float]]:
    ids = sorted(set(a) & set(b))
    return [a[i] for i in ids], [b[i] for i in ids]


def _finite(value: float) -> float | None:
    return None if value is None or math.isnan(value) else round(float(value), 4)


def spearman(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Spearman ρ between two score dicts (ties get average ranks)."""
    x, y = _aligned(a, b)
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return _finite(spearmanr(x, y).statistic)


def kendall(a: dict[str, float], b: dict[str, float]) -> float | None:
    """Kendall τ-b between two score dicts."""
    x, y = _aligned(a, b)
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return _finite(kendalltau(x, y).statistic)


def precision_at_k(order: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the top-k items that are relevant."""
    top = list(order)[:k]
    return round(sum(1 for i in top if i in relevant) / k, 4) if k else 0.0


def topk_overlap(order: Sequence[str], reference_order: Sequence[str], k: int) -> float:
    """|top-k(order) ∩ top-k(reference)| / k — the plan's Precision@K against experts."""
    return round(len(set(list(order)[:k]) & set(list(reference_order)[:k])) / k, 4) if k else 0.0


def ndcg_at_k(order: Sequence[str], gains: dict[str, float], k: int) -> float | None:
    """Normalised discounted cumulative gain at k (gain = graded relevance)."""
    def dcg(seq: Sequence[float]) -> float:
        return sum(g / math.log2(i + 2) for i, g in enumerate(seq))

    actual = dcg([gains.get(i, 0.0) for i in list(order)[:k]])
    ideal = dcg(sorted(gains.values(), reverse=True)[:k])
    return round(actual / ideal, 4) if ideal > 0 else None


def roc_auc(scores: dict[str, float], labels: dict[str, bool]) -> float | None:
    """Probability a random positive outranks a random negative (ties count ½)."""
    pos = [scores[i] for i in scores if labels.get(i)]
    neg = [scores[i] for i in scores if i in labels and not labels[i]]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def order_from_scores(scores: dict[str, float]) -> list[str]:
    """Deterministic order: score desc, then id."""
    return sorted(scores, key=lambda i: (-scores[i], i))


def bootstrap_ci(
    ids: Sequence[str],
    statistic: Callable[[list[str]], float | None],
    n_boot: int = 2000,
    seed: int = 7,
    alpha: float = 0.05,
) -> tuple[float | None, float | None]:
    """Percentile bootstrap CI of *statistic* over resampled item sets."""
    rng = random.Random(seed)
    ids = list(ids)
    values = []
    for _ in range(n_boot):
        sample = [rng.choice(ids) for _ in ids]
        v = statistic(sample)
        if v is not None and not math.isnan(v):
            values.append(v)
    if len(values) < n_boot * 0.5:
        return None, None
    values.sort()
    lo = values[int((alpha / 2) * len(values))]
    hi = values[int((1 - alpha / 2) * len(values)) - 1]
    return round(lo, 4), round(hi, 4)


def resampled_spearman(a: dict[str, float], b: dict[str, float], sample: list[str]) -> float | None:
    """Spearman ρ on a bootstrap sample (duplicated ids kept as separate observations)."""
    x = [a[i] for i in sample if i in a and i in b]
    y = [b[i] for i in sample if i in a and i in b]
    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return spearmanr(x, y).statistic


def kendalls_w(rankings: list[dict[str, float]]) -> float | None:
    """Kendall's coefficient of concordance W for m raters over shared items (0–1)."""
    if len(rankings) < 2:
        return None
    ids = sorted(set.intersection(*(set(r) for r in rankings)))
    n, m = len(ids), len(rankings)
    if n < 2:
        return None
    from scipy.stats import rankdata

    rank_sums = [0.0] * n
    tie_correction = 0
    for r in rankings:
        ranks = rankdata([-r[i] for i in ids])  # rank 1 = highest priority
        tie_correction += sum(t ** 3 - t for t in Counter(r[i] for i in ids).values())
        for j, rk in enumerate(ranks):
            rank_sums[j] += rk
    mean = m * (n + 1) / 2
    s = sum((rs - mean) ** 2 for rs in rank_sums)
    denominator = m ** 2 * (n ** 3 - n) - m * tie_correction
    return round(12 * s / denominator, 4) if denominator else None
