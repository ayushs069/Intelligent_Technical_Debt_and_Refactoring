#!/usr/bin/env python3
"""
run_prioritisation.py — Phase 4: LLM prioritisation of technical debt.

Usage:
    # Preview the prompt and estimated cost (no API calls):
    python run_prioritisation.py --dry-run

    # Full run: Analysis -> Priority -> Refactor agents with RAG context
    python run_prioritisation.py

    # Ablation: same LLM, static metrics + code only (no repo context, no RAG)
    python run_prioritisation.py --no-context --mode single

Requires ANTHROPIC_API_KEY in .env (except for --dry-run).  Responses are cached
in data/llm_cache/, so re-running is free.

Outputs (data/):
    <repo>_llm_rankings.json            (or _llm_rankings_nocontext.json)
    <repo>_static_rankings.json         baseline: static score and complexity
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

import config
from agents.prioritise import (build_contexts, build_rag_contexts, estimate_tokens,
                               prioritise, static_rankings)
from llm.client import _PRICING, make_client


def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(path: str, payload) -> None:
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 — LLM prioritisation.")
    parser.add_argument("--repo-name", default=config.DEFAULT_REPO_NAME)
    parser.add_argument("--mode", choices=("chain", "single"), default="chain",
                        help="chain = Analysis->Priority->Refactor agents (3 calls/item); "
                             "single = one combined call (plan Section 5 prompt).")
    parser.add_argument("--backend", choices=("native", "crewai"), default="native",
                        help="Agent runtime. crewai requires `pip install crewai`.")
    parser.add_argument("--no-context", action="store_true",
                        help="Ablation: hide repository context and RAG from the LLM.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default=config.LLM_EFFORT,
                        choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--top-k", type=int, default=config.RAG_TOP_K, help="RAG neighbours.")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N items (by id).")
    parser.add_argument("--workers", type=int, default=4, help="Parallel LLM requests.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the first prompt and a cost estimate; no API calls.")
    args = parser.parse_args()
    args.model = args.model or (os.environ.get("TECHDEBT_CREWAI_MODEL", config.LLM_MODEL)
                                if args.backend == "crewai" else config.LLM_MODEL)
    if args.backend == "crewai" and args.mode != "chain":
        parser.error("CrewAI implements the three-agent chain; use --mode chain")
    if args.limit < 0 or args.workers < 1 or args.top_k < 1:
        parser.error("limit must be nonnegative; workers and top-k must be positive")

    paths = config.dataset_paths(args.repo_name)
    if not os.path.isfile(paths["items"]):
        print(f"[error] {paths['items']} not found — run run_dataset.py first.")
        return 1
    items = sorted(_load(paths["items"]), key=lambda x: x["id"])
    meta = _load(paths["meta"]) if os.path.isfile(paths["meta"]) else {}
    if args.limit:
        items = items[: args.limit]
    with_context = not args.no_context
    if not items:
        parser.error("Dataset contains no candidate items")
    llm = None
    if not args.dry_run and args.backend == "native":
        llm = make_client(args.model, args.effort, config.LLM_CACHE_DIR)

    # Baseline rankings are cheap and always (re)written.
    _save(paths["static_rankings"], static_rankings(_load(paths["items"])))

    print(f"\n{'='*60}\n  Technical Debt Prioritisation — Phase 4\n"
          f"  Items   : {len(items)}  | mode={args.mode} backend={args.backend} "
          f"context={'on' if with_context else 'OFF (ablation)'}\n"
          f"  Model   : {args.model} (effort={args.effort})\n{'='*60}\n")

    rag_contexts = None
    if with_context:
        print(f"[rag] Indexing {len(items)} item(s) in ChromaDB and retrieving top-{args.top_k} …")
        from rag.retriever import DebtRetriever
        from rag.vector_store import DebtVectorStore

        store = DebtVectorStore(collection_name=f"{args.repo_name}_items")
        store.ingest_issues(_load(paths["items"]))
        rag_contexts = build_rag_contexts(items, DebtRetriever(store), args.top_k)

    since = meta.get("history_since", "")[:4]
    until = meta.get("as_of", "")[:4]
    window = f"{since}-{until}" if since and until else "5 years"
    contexts = build_contexts(items, with_context, rag_contexts, window)
    if any("<source unavailable>" in c for c in contexts.values()):
        raise SystemExit("[error] Snapshot source is missing. Restore the target snapshot before ranking.")

    tin, tout = estimate_tokens(contexts, args.mode)
    pin, pout = _PRICING.get(args.model, (0.0, 0.0))
    cost = f"~ ${(tin * pin + tout * pout) / 1e6:.2f}" if args.model in _PRICING else "cost unavailable; check provider billing"
    print(f"[estimate] ~{tin:,} input + ~{tout:,} output tokens; {cost}")

    if args.dry_run:
        first = items[0]
        print(f"\n--- Prompt for {first['id']} ---\n{contexts[first['id']]}\n--- end ---")
        return 0

    if args.backend == "crewai":
        from agents.crew_backend import prioritise_with_crewai

        results = prioritise_with_crewai(items, contexts, args.model)
        usage_line = "CrewAI run (token usage tracked by CrewAI)"
    else:
        def progress(done: int, total: int, item_id: str) -> None:
            print(f"  [{done:>3}/{total}] {item_id}")

        results = prioritise(items, llm, contexts, mode=args.mode,
                             workers=args.workers, progress=progress)
        usage_line = llm.usage.summary(args.model)

    for r in results:
        r.update(mode=args.mode, backend=args.backend, with_context=with_context,
                 model=args.model)
    out_path = paths["llm_rankings"] if with_context else paths["llm_rankings_nocontext"]
    if args.limit:
        out_path = out_path.replace(".json", "_pilot.json")
    errors = [r for r in results if "error" in r]
    if errors and os.path.isfile(out_path) and any("llm_rank" in r for r in _load(out_path)):
        out_path = out_path.replace(".json", "_failed_attempt.json")
        print("[warning] Preserving the previous ranking; failed attempt saved separately.")
    _save(out_path, results)
    _save(out_path.replace(".json", "_provenance.json"), {
        "generated": datetime.now(timezone.utc).isoformat(), "model": args.model,
        "mode": args.mode, "effort": args.effort, "with_context": with_context,
        "snapshot_sha": meta.get("snapshot_sha"), "item_count": len(items),
        "dataset_sha256": hashlib.sha256(Path(paths["items"]).read_bytes()).hexdigest(),
        "context_sha256": {i: hashlib.sha256(c.encode()).hexdigest() for i, c in contexts.items()},
        "retrieval_model": "all-MiniLM-L6-v2" if with_context else None,
        "top_k": args.top_k if with_context else 0, "usage": usage_line,
    })
    if rag_contexts:
        _save(out_path.replace(".json", "_retrieval.json"), rag_contexts)

    print(f"\n{'='*60}\n  RESULTS — {len(results) - len(errors)} ranked, {len(errors)} failed\n"
          f"  Usage: {usage_line}\n"
          f"  Priority levels: {dict(Counter(r.get('priority') for r in results if 'error' not in r))}")
    print("\n  Top 10 by LLM priority:")
    for r in results[:10]:
        if "error" in r:
            break
        print(f"   {r['llm_rank']:>3}. [{r['priority']} {r['priority_score']:>3}] {r['id']}")
        print(f"        {r['reason'][:140]}")
    for r in errors:
        print(f"  [failed] {r['id']}: {r['error']}")
    print(f"\n  Saved -> {out_path}\n  Saved -> {paths['static_rankings']}\n{'='*60}")
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
