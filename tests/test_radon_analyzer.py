"""Tests for analyzer.radon_analyzer module."""

from analyzer import radon_analyzer


def test_run_returns_list(sample_repo_str):
    """Radon should return a list of issue dicts."""
    results = radon_analyzer.run(sample_repo_str)
    assert isinstance(results, list)


def test_complexity_issues_detected(sample_repo_str):
    """The intentionally complex functions should produce complexity issues."""
    results = radon_analyzer.run(sample_repo_str)
    complexity_issues = [r for r in results if r["issue_type"] == "high_complexity"]
    # very_complex_function and another_complex should both be flagged
    assert len(complexity_issues) >= 1, (
        f"Expected at least 1 complexity issue, got {len(complexity_issues)}"
    )


def test_issue_has_required_fields(sample_repo_str):
    """Each issue should have the expected raw fields."""
    results = radon_analyzer.run(sample_repo_str)
    required = {"file", "function", "line", "issue_type", "tool", "message"}
    for issue in results:
        missing = required - issue.keys()
        assert not missing, f"Missing fields: {missing}"
        assert issue["tool"] == "radon"


def test_maintainability_parsing():
    """Test _parse_maintainability with synthetic data."""
    import json
    raw = json.dumps({
        "bad_module.py": {"mi": 30.5, "rank": "C"},
        "good_module.py": {"mi": 85.0, "rank": "A"},
    })
    issues = radon_analyzer._parse_maintainability(raw)
    # Only bad_module should be flagged (MI < 65)
    assert len(issues) == 1
    assert issues[0]["file"] == "bad_module.py"
    assert issues[0]["maintainability_index"] == 30.5
