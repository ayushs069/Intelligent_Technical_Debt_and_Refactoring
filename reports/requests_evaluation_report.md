# Evaluation report — requests

_Generated 2026-10-07T17:20:43+00:00_

## Setup

- Snapshot: `72eccc8d` (last commit before 2024-01-01); context history from 2019-01-01.

- Ground-truth window: 2024-01-01 → 2026-10-06.

- Candidate debt items: 84 functions; 7 received a bug-fix commit after the cutoff.

- Methods compared: Static analysis (composite score), Static analysis (complexity only), Context heuristic (no LLM).

### Incomplete experiments

- llm_rankings: 0/84 successful items; excluded from full-dataset comparison
- llm_rankings_nocontext: 0/84 successful items; excluded from full-dataset comparison

## 1. Against expert rankings

_No expert ratings yet — collect them in the dashboard's Expert ranking tab._

## 2. Against post-cutoff defect history

Precision@K = share of a method's top-K items that received a bug-fix commit after the cutoff. AUC = probability that a defect-fixed item outranks a non-fixed one.

| Method | precision@5 | precision@10 | precision@20 | auc_future_defect | spearman_vs_fix_commits | ndcg@10_fix_commits |
|---|---|---|---|---|---|---|
| Static analysis (composite score) | 0.400 | 0.400 | 0.300 | 0.852 | 0.333 | 0.472 |
| Static analysis (complexity only) | 0.600 | 0.400 | 0.250 | 0.843 | 0.329 | 0.528 |
| Context heuristic (no LLM) | 0.600 | 0.300 | 0.300 | 0.896 | 0.380 | 0.546 |

## 3. Against post-cutoff change-proneness

| Method | spearman_vs_change_commits | spearman_vs_change_commits_ci95 | ndcg@10_change_commits |
|---|---|---|---|
| Static analysis (composite score) | 0.423 | [0.216, 0.606] | 0.673 |
| Static analysis (complexity only) | 0.520 | [0.341, 0.662] | 0.744 |
| Context heuristic (no LLM) | 0.412 | [0.223, 0.575] | 0.512 |

## 4. Paired comparisons (bootstrap 95% CI of Δρ)

_Run Phase 4 (run_prioritisation.py) to add the LLM methods._

## Research contribution statement

> Static baselines have been measured, but there is no completed LLM experiment. The research question remains unanswered; no claim of LLM improvement is supported.

## Threats to validity

- Defect labels come from commit-message keywords mapped to changed functions; some fixes are missed or mislabelled.

- Few post-cutoff fixes in a mature library make defect-based metrics noisy — read the confidence intervals, not just point estimates.

- Independent expert ratings are pending unless explicitly reported above; historical fixes are a proxy for debt urgency.

- One repository and one snapshot limit generalisation. The model may have seen public repository code during training.

- The context heuristic uses arbitrary equal weights; it is a sanity baseline, not a tuned model.

- LLM output can vary between runs; responses are cached so the reported run is reproducible from data/llm_cache.

- Matched comparisons require the same model, agent mode, item set and prompt rubric. Report incomplete runs explicitly.
