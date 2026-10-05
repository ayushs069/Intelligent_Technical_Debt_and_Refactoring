"""
RAG package — Vector embeddings, ChromaDB storage, and semantic retrieval for technical debt.
"""

from rag.embedder import IssueEmbedder, format_issue_document
from rag.retriever import DebtRetriever
from rag.vector_store import DebtVectorStore

__all__ = [
    "IssueEmbedder",
    "format_issue_document",
    "DebtVectorStore",
    "DebtRetriever",
]
