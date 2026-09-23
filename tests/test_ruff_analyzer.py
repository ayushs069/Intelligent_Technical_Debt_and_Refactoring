"""Tests for analyzer.ruff_analyzer module."""

from analyzer import ruff_analyzer


def test_run_returns_list(sample_repo_str):
    """Ruff should return a list of issue dicts."""
    results = ruff_analyzer.run(sample_repo_str)
    assert isinstance(results, list)


def test_lint_issues_detected(sample_repo_str):
    """The lint_issues.py file should trigger ruff findings."""
    results = ruff_analyzer.run(sample_repo_str)
    # Expect at least a few lint issues (unused imports, etc.)
    assert len(results) >= 1, (
        f"Expected at least 1 ruff issue, got {len(results)}"
    )


def test_issue_has_required_fields(sample_repo_str):
    """Each issue should carry the right fields."""
    results = ruff_analyzer.run(sample_repo_str)
    required = {"file", "function", "line", "issue_type", "tool", "message"}
    for issue in results:
        missing = required - issue.keys()
        assert not missing, f"Missing fields: {missing}"
        assert issue["tool"] == "ruff"
        assert issue["issue_type"] == "lint"


def test_severity_mapping():
    """Verify the severity mapping logic."""
    assert ruff_analyzer._severity_from_code("E901") == "HIGH"
    assert ruff_analyzer._severity_from_code("F401") == "HIGH"
    assert ruff_analyzer._severity_from_code("E501") == "MEDIUM"
    assert ruff_analyzer._severity_from_code("W291") == "MEDIUM"
    assert ruff_analyzer._severity_from_code("C0103") == "LOW"
