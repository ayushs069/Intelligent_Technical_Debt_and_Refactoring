"""Tests for Phase 3 — RAG Pipeline (embedder, vector_store, retriever)."""

from rag.embedder import IssueEmbedder, format_issue_document
from rag.retriever import DebtRetriever
from rag.vector_store import DebtVectorStore


def test_format_issue_document_includes_all_fields():
    """format_issue_document must render Phase 1 and Phase 2 fields into text."""
    issue = {
        "repo": "payment_service",
        "file": "payment.py",
        "function": "process_payment",
        "line": 85,
        "issue_type": "high_complexity",
        "tool": "radon",
        "severity": "HIGH",
        "message": "Cyclomatic complexity of 24 (rank D) in function 'process_payment'",
        "complexity": 24,
        "maintainability_index": 51.2,
        "loc": 180,
        "static_score": 8.2,
        "callers": 17,
        "recent_commits": 15,
        "defect_commits": 3,
        "commit_authors": 4,
        "last_modified_days": 12,
        "test_coverage": 42.0,
    }

    doc_text = format_issue_document(issue)

    # Verify Phase 1 static analysis metrics present in doc
    assert "payment.py" in doc_text
    assert "process_payment" in doc_text
    assert "Complexity=24" in doc_text
    assert "Maintainability Index=51.2" in doc_text
    assert "Composite Debt Score=8.20" in doc_text

    # Verify Phase 2 repository context fields present in doc
    assert "Callers=17" in doc_text
    assert "Recent Commits (90d)=15" in doc_text
    assert "Defect Commits=3" in doc_text
    assert "Test Coverage=42.0%" in doc_text


def test_vector_store_ingestion_and_retrieval(tmp_path):
    """Test ChromaDB ingestion and top-k retrieval with Phase 1 and Phase 2 fields intact."""
    sample_issues = [
        {
            "repo": "payment_service",
            "file": "payment.py",
            "function": "process_payment",
            "line": 85,
            "issue_type": "high_complexity",
            "tool": "radon",
            "severity": "HIGH",
            "message": "Cyclomatic complexity of 24 (rank D) in function 'process_payment'",
            "complexity": 24,
            "maintainability_index": 51.2,
            "loc": 180,
            "static_score": 8.2,
            "callers": 17,
            "recent_commits": 15,
            "defect_commits": 3,
            "commit_authors": 4,
            "last_modified_days": 12,
            "test_coverage": 42.0,
        },
        {
            "repo": "payment_service",
            "file": "utils.py",
            "function": "format_currency",
            "line": 12,
            "issue_type": "lint",
            "tool": "ruff",
            "severity": "LOW",
            "message": "Unused import 'sys'",
            "complexity": 1,
            "maintainability_index": 95.0,
            "loc": 10,
            "static_score": 0.2,
            "callers": 30,
            "recent_commits": 2,
            "defect_commits": 0,
            "commit_authors": 1,
            "last_modified_days": 45,
            "test_coverage": 98.0,
        },
    ]

    embedder = IssueEmbedder()
    store = DebtVectorStore(
        collection_name="test_rag_coll",
        persist_dir=str(tmp_path / "chroma_db"),
        embedder=embedder,
    )

    n_ingested = store.ingest_issues(sample_issues)
    assert n_ingested == 2

    retriever = DebtRetriever(store)
    query = "process_payment() with complexity=24, callers=17, defect_commits=3, coverage=42%"
    results = retriever.retrieve(query, top_k=2)

    assert len(results) == 2

    # Top result should be process_payment
    top = results[0]
    assert top["function"] == "process_payment"
    assert top["complexity"] == 24
    assert top["callers"] == 17
    assert top["defect_commits"] == 3
    assert top["test_coverage"] == 42.0
    assert top["static_score"] == 8.2


def test_format_prompt_context(tmp_path):
    """Verify prompt formatting outputs clean markdown context."""
    sample_issues = [
        {
            "repo": "demo",
            "file": "core.py",
            "function": "execute",
            "line": 20,
            "issue_type": "high_complexity",
            "tool": "radon",
            "severity": "HIGH",
            "message": "Too complex",
            "complexity": 15,
            "maintainability_index": 40.0,
            "loc": 100,
            "static_score": 7.4,
            "callers": 10,
            "recent_commits": 8,
            "defect_commits": 2,
            "commit_authors": 3,
            "last_modified_days": 5,
            "test_coverage": 50.0,
        }
    ]

    store = DebtVectorStore(
        collection_name="test_prompt_coll",
        persist_dir=str(tmp_path / "chroma_db2"),
    )
    store.ingest_issues(sample_issues)
    retriever = DebtRetriever(store)

    results = retriever.retrieve("execute high complexity", top_k=1)
    prompt_context = retriever.format_retrieved_context_for_prompt(results)

    assert "### Result 1" in prompt_context
    assert "**Repository**: `demo`" in prompt_context
    assert "**Function**: `execute`" in prompt_context
    assert "Complexity=15" in prompt_context
    assert "Callers=10" in prompt_context
    assert "Defect Commits=2" in prompt_context
    assert "Coverage=50.0%" in prompt_context
