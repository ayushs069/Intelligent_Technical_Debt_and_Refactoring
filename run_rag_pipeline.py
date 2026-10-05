#!/usr/bin/env python3
"""
run_rag_pipeline.py — CLI entry point for Phase 3: RAG Pipeline.

Usage:
    # Ingest enriched dataset and query top 5 similar issues:
    python run_rag_pipeline.py --input data/Intelligent_Technical_Debt_and_Refactoring_enriched_dataset.json

    # Custom query:
    python run_rag_pipeline.py --input data/requests_enriched_dataset.json \
        --query "process_payment() with complexity=24, callers=17, defect_commits=3, coverage=42%"
"""

import argparse
import json
import os
import sys

from rag.embedder import IssueEmbedder
from rag.retriever import DebtRetriever
from rag.vector_store import DebtVectorStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Phase 3 — Vector Embeddings and RAG Retrieval for Technical Debt Issues.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the Phase 2 enriched JSON dataset file.",
    )
    parser.add_argument(
        "--query",
        default="process_payment() with complexity=24, callers=17, defect_commits=3, coverage=42%",
        help="Query text or description to retrieve relevant issues.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of top results to retrieve (default: 5).",
    )
    parser.add_argument(
        "--persist-dir",
        default="",
        help="Directory to persist ChromaDB collection (default: in-memory / temporary).",
    )
    args = parser.parse_args()

    input_path = os.path.normpath(os.path.abspath(args.input))
    if not os.path.isfile(input_path):
        print(f"[error] Enriched dataset not found: {input_path}")
        print("        Run Phase 2 first: python run_enrichment.py --repo <repo_path>")
        return 1

    print(f"\n{'='*60}")
    print(f"  Technical Debt RAG Pipeline — Phase 3")
    print(f"  Input Dataset : {input_path}")
    print(f"  Query         : {args.query}")
    print(f"{'='*60}\n")

    # Load dataset
    with open(input_path, encoding="utf-8") as fh:
        issues: list[dict] = json.load(fh)
    print(f"[rag] Loaded {len(issues)} enriched issue(s) from dataset.")

    # Initialize Embedder & Vector Store
    print("[rag] Initializing sentence-transformer model (all-MiniLM-L6-v2) & ChromaDB...")
    embedder = IssueEmbedder()
    persist_dir = args.persist_dir if args.persist_dir else None
    vector_store = DebtVectorStore(
        collection_name="techdebt_rag",
        persist_dir=persist_dir,
        embedder=embedder,
    )

    # Ingest
    print(f"[rag] Ingesting {len(issues)} issues into ChromaDB vector collection...")
    n_ingested = vector_store.ingest_issues(issues)
    print(f"[rag] Successfully ingested {n_ingested} issues.\n")

    # Retrieve
    retriever = DebtRetriever(vector_store)
    print(f"[rag] Searching for Top {args.top_k} relevant issues...")
    results = retriever.retrieve(args.query, top_k=args.top_k)

    print(f"\n{'='*60}")
    print(f"  RETRIEVAL RESULTS — Top {len(results)} Issues")
    print(f"{'='*60}\n")

    for idx, item in enumerate(results, 1):
        print(f"[{idx}] {item['repo']} | {item['file']}:{item['line']} -> {item['function']}()")
        print(f"    Tool/Type : [{item['tool']}] {item['issue_type']} (Severity: {item['severity']})")
        print(f"    Message   : {item['message']}")
        print(f"    Phase 1   : Complexity={item['complexity']}, MI={item['maintainability_index']}, Score={item['static_score']}")
        print(f"    Phase 2   : Callers={item['callers']}, Recent Commits={item['recent_commits']}, Defects={item['defect_commits']}, Coverage={item['test_coverage']}%")
        print(f"    Distance  : {item['distance']:.4f}")
        print("-" * 60)

    print("\n[rag] Prompt-formatted Context for Phase 4 LLM:")
    print(retriever.format_retrieved_context_for_prompt(results[:2]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
