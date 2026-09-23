"""Tests for analyzer.normalizer module."""

from analyzer.normalizer import normalize, _compute_static_score, _map_severity


class TestMapSeverity:
    """Test severity normalisation from various raw formats."""

    def test_radon_ranks(self):
        assert _map_severity("A") == "LOW"
        assert _map_severity("B") == "LOW"
        assert _map_severity("C") == "MEDIUM"
        assert _map_severity("D") == "MEDIUM"
        assert _map_severity("E") == "HIGH"
        assert _map_severity("F") == "HIGH"

    def test_pass_through(self):
        assert _map_severity("HIGH") == "HIGH"
        assert _map_severity("MEDIUM") == "MEDIUM"
        assert _map_severity("LOW") == "LOW"

    def test_unknown_defaults_low(self):
        assert _map_severity("unknown") == "LOW"
        assert _map_severity("") == "LOW"


class TestComputeStaticScore:
    """Test the composite score formula."""

    def test_zero_issue(self):
        issue = {
            "complexity": 0,
            "maintainability_index": 100.0,
            "severity": "LOW",
        }
        assert _compute_static_score(issue) == 0.0

    def test_max_complexity(self):
        issue = {
            "complexity": 30,  # min(30/5, 4) = 4
            "maintainability_index": 100.0,
            "severity": "LOW",
        }
        assert _compute_static_score(issue) == 4.0

    def test_low_maintainability(self):
        issue = {
            "complexity": 0,
            "maintainability_index": 0.0,  # (100-0)/25 = 4.0
            "severity": "LOW",
        }
        assert _compute_static_score(issue) == 4.0

    def test_high_severity_bonus(self):
        issue = {
            "complexity": 0,
            "maintainability_index": 100.0,
            "severity": "HIGH",
        }
        assert _compute_static_score(issue) == 2.0

    def test_worst_case(self):
        issue = {
            "complexity": 25,  # → 4.0
            "maintainability_index": 0.0,  # → 4.0
            "severity": "HIGH",  # → 2.0
        }
        assert _compute_static_score(issue) == 10.0


class TestNormalize:
    """Test the full normalize pipeline."""

    def test_basic_normalisation(self):
        raw = [{
            "file": "test.py",
            "function": "foo",
            "line": 10,
            "issue_type": "lint",
            "tool": "ruff",
            "raw_severity": "MEDIUM",
            "complexity": 0,
            "loc": 5,
            "message": "test message",
        }]
        result = normalize(raw)
        assert len(result) == 1
        issue = result[0]
        assert issue["file"] == "test.py"
        assert issue["severity"] == "MEDIUM"
        assert issue["static_score"] == 1.0  # severity MEDIUM = 1
        assert "raw_severity" not in issue

    def test_schema_fields(self):
        raw = [{
            "file": "a.py",
            "function": "f",
            "line": 1,
            "issue_type": "high_complexity",
            "tool": "radon",
            "raw_severity": "C",
            "complexity": 10,
            "loc": 50,
            "message": "high cc",
        }]
        result = normalize(raw)
        expected_keys = {
            "file", "function", "line", "issue_type", "tool",
            "severity", "complexity", "maintainability_index",
            "loc", "message", "static_score",
        }
        assert set(result[0].keys()) == expected_keys

    def test_empty_input(self):
        assert normalize([]) == []
