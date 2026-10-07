"""
Prioritise — Phase 4: LLM ranking of every debt item.

For each item:
  1. retrieve the top-K most similar *other* items from ChromaDB (Phase 3 RAG),
  2. render the item context (metrics + repository context + RAG + source code),
  3. run the agent chain (Analysis → Priority → Refactor) or the single-call
     variant, and collect a priority score with reasons.

Items are ranked by ``priority_score`` (desc), then priority level, then id —
a neutral tie-break so the LLM ranking never borrows the static ranking.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from threading import Event

from agents.agents import PRIORITY_LEVELS, run_chain, run_single
from agents.prompts import read_function_source, render_item
from llm.client import LLMClient, LLMError, LLMUnavailableError

_LEVEL_ORDER = {level: i for i, level in enumerate(PRIORITY_LEVELS)}


def static_rankings(items: list[dict]) -> list[dict]:
    """Baseline rankings: composite static score and raw complexity."""
    by_complexity = sorted(items, key=lambda x: (-x["complexity"], -x["loc"], x["id"]))
    cx_rank = {item["id"]: rank for rank, item in enumerate(by_complexity, 1)}
    ranked = sorted(items, key=lambda x: x["static_rank"])
    return [
        {
            "id": item["id"],
            "file": item["file"],
            "function": item["function"],
            "static_score": item["static_score"],
            "complexity": item["complexity"],
            "static_rank": item["static_rank"],
            "complexity_rank": cx_rank[item["id"]],
        }
        for item in ranked
    ]


def build_rag_contexts(items: list[dict], retriever, top_k: int) -> dict[str, str]:
    """Retrieve top-K neighbours for every item, excluding the item itself."""
    from rag.embedder import format_issue_document

    contexts: dict[str, str] = {}
    for item in items:
        results = retriever.retrieve(format_issue_document(item), top_k=top_k + 1)
        neighbours = [r for r in results if r["metadata"].get("id") != item["id"]][:top_k]
        contexts[item["id"]] = retriever.format_retrieved_context_for_prompt(neighbours)
    return contexts


def build_contexts(items: list[dict], with_context: bool,
                   rag_contexts: dict[str, str] | None,
                   history_window: str) -> dict[str, str]:
    contexts = {}
    for item in items:
        contexts[item["id"]] = render_item(
            item,
            read_function_source(item),
            with_context=with_context,
            rag_context=(rag_contexts or {}).get(item["id"], ""),
            history_window=history_window,
        )
    return contexts


def rank_results(results: list[dict]) -> list[dict]:
    ok = [r for r in results if "error" not in r]
    ok.sort(key=lambda r: (-r["priority_score"], _LEVEL_ORDER.get(r["priority"], 9), r["id"]))
    for rank, r in enumerate(ok, 1):
        r["llm_rank"] = rank
    return ok + [r for r in results if "error" in r]


def prioritise(
    items: list[dict],
    llm: LLMClient,
    contexts: dict[str, str],
    mode: str = "chain",
    workers: int = 4,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """Run the LLM over every item and return ranked results."""
    runner = run_chain if mode == "chain" else run_single
    unavailable = Event()

    def work(item: dict) -> dict[str, Any]:
        base = {"id": item["id"], "file": item["file"], "function": item["function"],
                "line": item["line"]}
        if unavailable.is_set():
            return {**base, "error": "Skipped: provider authentication or quota unavailable."}
        try:
            return {**base, **runner(llm, contexts[item["id"]])}
        except LLMUnavailableError as exc:
            unavailable.set()
            return {**base, "error": str(exc)}
        except LLMError as exc:
            return {**base, "error": str(exc)}

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, item): item["id"] for item in items}
        for done, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if progress:
                progress(done, len(items), futures[future])
    return rank_results(results)


def estimate_tokens(contexts: dict[str, str], mode: str) -> tuple[int, int]:
    """Rough (input, output) token estimate (~4 chars/token) for a full run."""
    calls_per_item = 3 if mode == "chain" else 1
    system_overhead = 350
    input_tokens = 0
    for text in contexts.values():
        per_call = len(text) // 4 + system_overhead
        # later agents in the chain also read earlier agents' JSON (~400 tokens each)
        input_tokens += per_call * calls_per_item + (1200 if mode == "chain" else 0)
    output_tokens = len(contexts) * calls_per_item * 450
    return input_tokens, output_tokens
