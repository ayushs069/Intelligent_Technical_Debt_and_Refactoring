"""Tests for analyzer.pylint_analyzer module."""

from analyzer import pylint_analyzer


def test_run_returns_list(sample_repo_str):
    """Pylint should return a list of issue dicts."""
    results = pylint_analyzer.run(sample_repo_str)
    assert isinstance(results, list)


def test_issues_detected(sample_repo_str):
    """The sample repo should trigger pylint findings."""
    results = pylint_analyzer.run(sample_repo_str)
    assert len(results) >= 1, (
        f"Expected at least 1 pylint issue, got {len(results)}"
    )


def test_issue_has_required_fields(sample_repo_str):
    """Each issue should carry the right fields."""
    results = pylint_analyzer.run(sample_repo_str)
    required = {"file", "function", "line", "issue_type", "tool", "message"}
    for issue in results:
        missing = required - issue.keys()
        assert not missing, f"Missing fields: {missing}"
        assert issue["tool"] == "pylint"


def test_severity_and_type_mapping():
    """Verify internal mapping helpers."""
    assert pylint_analyzer._PYLINT_TYPE_TO_SEVERITY["fatal"] == "HIGH"
    assert pylint_analyzer._PYLINT_TYPE_TO_SEVERITY["error"] == "HIGH"
    assert pylint_analyzer._PYLINT_TYPE_TO_SEVERITY["warning"] == "MEDIUM"
    assert pylint_analyzer._PYLINT_TYPE_TO_SEVERITY["convention"] == "LOW"
    assert pylint_analyzer._map_issue_type("error") == "lint"
    assert pylint_analyzer._map_issue_type("refactor") == "convention"
