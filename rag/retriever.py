"""
RAG Retriever — High-level retrieval pipeline for technical debt analysis.

Retrieves top-K relevant issue records from ChromaDB and formats them for LLM prompt injection.
"""

from __future__ import annotations

from typing import Any

from rag.vector_store import DebtVectorStore


class DebtRetriever:
    """High-level retriever for Phase 3 RAG query pipeline."""

    def __init__(self, vector_store: DebtVectorStore) -> None:
        self.vector_store = vector_store

    def retrieve(
        self,
        query: str | dict[str, Any],
        top_k: int = 5,
        repo_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve top_k technical debt issues matching *query*.
        
        *query* can be a plain string description OR a dictionary of issue properties.
        """
        if isinstance(query, dict):
            # Format query dict into a query string
            func_name = query.get("function", "") or query.get("file", "issue")
            complexity = query.get("complexity", "")
            callers = query.get("callers", "")
            defects = query.get("defect_commits", "")
            coverage = query.get("test_coverage", "")
            query_str = (
                f"Technical debt issue in {func_name} with complexity={complexity}, "
                f"callers={callers}, defect_commits={defects}, coverage={coverage}%"
            )
        else:
            query_str = str(query)

        where_filter = None
        if repo_filter:
            where_filter = {"repo": repo_filter}

        raw_results = self.vector_store.search_similar(
            query_text=query_str,
            n_results=top_k,
            where_filter=where_filter,
        )

        results: list[dict[str, Any]] = []
        for item in raw_results:
            meta = dict(item["metadata"])
            results.append({
                "id": item["id"],
                "distance": item["distance"],
                "document": item["document"],
                "metadata": meta,
                # Expose metadata fields directly for ease of access
                "repo": meta.get("repo", ""),
                "file": meta.get("file", ""),
                "function": meta.get("function", ""),
                "line": meta.get("line", 0),
                "issue_type": meta.get("issue_type", ""),
                "tool": meta.get("tool", ""),
                "severity": meta.get("severity", ""),
                "complexity": meta.get("complexity", 0),
                "maintainability_index": meta.get("maintainability_index", 100.0),
                "static_score": meta.get("static_score", 0.0),
                "callers": meta.get("callers", 0),
                "recent_commits": meta.get("recent_commits", 0),
                "defect_commits": meta.get("defect_commits", 0),
                "test_coverage": meta.get("test_coverage", 0.0),
                "message": meta.get("message", ""),
            })

        return results

    def format_retrieved_context_for_prompt(self, results: list[dict[str, Any]]) -> str:
        """Format retrieved issues into a prompt-ready Markdown block for Phase 4 LLM agents."""
        if not results:
            return "No relevant technical debt issues retrieved."

        blocks: list[str] = []
        for idx, item in enumerate(results, 1):
            m = item
            block = (
                f"### Result {idx} (Score: {m['static_score']}, Distance: {m['distance']:.4f})\n"
                f"- **Repository**: `{m['repo']}` | **File**: `{m['file']}`:{m['line']}\n"
                f"- **Function**: `{m['function']}`\n"
                f"- **Issue Type**: `{m['issue_type']}` ({m['tool']} - Severity: {m['severity']})\n"
                f"- **Message**: {m['message']}\n"
                f"- **Static Metrics**: Complexity={m['complexity']}, Maintainability Index={m['maintainability_index']}\n"
                f"- **Repo Context**: Callers={m['callers']}, Recent Commits={m['recent_commits']}, "
                f"Defect Commits={m['defect_commits']}, Coverage={m['test_coverage']}%\n"
            )
            blocks.append(block)

        return "\n".join(blocks)
