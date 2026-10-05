"""Tests for enricher.callgraph_enricher module."""

import textwrap
from enricher.callgraph_enricher import build_call_counts


def test_returns_dict(tmp_path):
    """build_call_counts always returns a dict."""
    result = build_call_counts(str(tmp_path))
    assert isinstance(result, dict)


def test_counts_simple_calls(tmp_path):
    """Should count direct function calls."""
    f = tmp_path / "sample.py"
    f.write_text(textwrap.dedent("""
        def helper():
            pass

        def main():
            helper()
            helper()
            other()
    """), encoding="utf-8")

    counts = build_call_counts(str(tmp_path))
    assert counts.get("helper", 0) == 2
    assert counts.get("other", 0) == 1


def test_counts_method_calls(tmp_path):
    """Should count method calls via attribute access."""
    f = tmp_path / "obj.py"
    f.write_text(textwrap.dedent("""
        import os
        os.path.join("a", "b")
        os.path.join("c", "d")
    """), encoding="utf-8")

    counts = build_call_counts(str(tmp_path))
    assert counts.get("join", 0) == 2


def test_empty_directory(tmp_path):
    """An empty directory yields an empty dict."""
    assert build_call_counts(str(tmp_path)) == {}


def test_syntax_error_file_skipped(tmp_path):
    """Files with syntax errors should be silently skipped."""
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    result = build_call_counts(str(tmp_path))
    assert isinstance(result, dict)
