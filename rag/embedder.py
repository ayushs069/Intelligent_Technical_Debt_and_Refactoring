"""
Embedder — Formats enriched technical-debt issue records into descriptive text chunks
and generates vector embeddings using SentenceTransformers (all-MiniLM-L6-v2).
"""

from __future__ import annotations

from typing import Any


def format_issue_document(issue: dict[str, Any]) -> str:
    """
    Format an enriched issue dictionary into a structured, human-readable text document
    suitable for vector embedding and retrieval.
    """
    repo = issue.get("repo", "unknown_repo")
    file_path = issue.get("file", "unknown_file")
    func_name = issue.get("function", "") or "<module_level>"
    line = issue.get("line", 0)
    issue_type = issue.get("issue_type", "unknown")
    tool = issue.get("tool", "unknown")
    severity = issue.get("severity", "LOW")
    message = issue.get("message", "")

    complexity = issue.get("complexity", 0)
    mi = issue.get("maintainability_index", 100.0)
    loc = issue.get("loc", 0)
    static_score = issue.get("static_score", 0.0)

    callers = issue.get("callers", 0)
    recent_commits = issue.get("recent_commits", 0)
    defect_commits = issue.get("defect_commits", 0)
    commit_authors = issue.get("commit_authors", 0)
    last_modified_days = issue.get("last_modified_days", -1)
    test_coverage = issue.get("test_coverage", 0.0)

    doc_text = (
        f"Repository: {repo}\n"
        f"File: {file_path}\n"
        f"Function: {func_name} (line {line})\n"
        f"Issue Type: {issue_type} | Tool: {tool} | Severity: {severity}\n"
        f"Message: {message}\n"
        f"Static Analysis Metrics: Complexity={complexity}, Maintainability Index={mi:.1f}, "
        f"LOC={loc}, Composite Debt Score={static_score:.2f}\n"
        f"Repository Context: Callers={callers}, Recent Commits (90d)={recent_commits}, "
        f"Defect Commits={defect_commits}, Distinct Authors={commit_authors}, "
        f"Days Since Last Commit={last_modified_days}, Test Coverage={test_coverage:.1f}%"
    )
    return doc_text


class IssueEmbedder:
    """Generates embeddings using sentence-transformers (all-MiniLM-L6-v2)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode_text(self, text: str) -> list[float]:
        """Generate embedding vector for a single text chunk."""
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of text chunks."""
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return [emb.tolist() for emb in embeddings]
