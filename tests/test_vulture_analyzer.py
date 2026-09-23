"""Tests for analyzer.vulture_analyzer module."""

from analyzer import vulture_analyzer


def test_run_returns_list(sample_repo_str):
    """Vulture should return a list of issue dicts."""
    results = vulture_analyzer.run(sample_repo_str)
    assert isinstance(results, list)


def test_dead_code_detected(sample_repo_str):
    """The sample repo has intentional dead code — vulture should find some."""
    results = vulture_analyzer.run(sample_repo_str)
    dead = [r for r in results if r["issue_type"] == "dead_code"]
    assert len(dead) >= 1, (
        f"Expected at least 1 dead-code finding, got {len(dead)}"
    )


def test_issue_has_required_fields(sample_repo_str):
    """Each issue should carry the right fields."""
    results = vulture_analyzer.run(sample_repo_str)
    required = {"file", "function", "line", "issue_type", "tool", "message"}
    for issue in results:
        missing = required - issue.keys()
        assert not missing, f"Missing fields: {missing}"
        assert issue["tool"] == "vulture"
        assert issue["issue_type"] == "dead_code"


def test_line_regex_parsing():
    """Test the regex against a synthetic vulture output line."""
    import re
    line = "app/views.py:42: unused function 'old_handler' (90% confidence)"
    m = vulture_analyzer._LINE_RE.match(line)
    assert m is not None
    assert m.group("file") == "app/views.py"
    assert m.group("line") == "42"
    assert m.group("confidence") == "90"


def test_extract_name():
    """Test identifier extraction from vulture messages."""
    assert vulture_analyzer._extract_name("unused function 'foo'") == "foo"
    assert vulture_analyzer._extract_name("unused import 'os'") == "os"
    assert vulture_analyzer._extract_name("no quotes here") == ""
