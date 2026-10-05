"""Tests for enricher.coverage_enricher module."""

import json
import os
from enricher.coverage_enricher import build_coverage_map


def test_returns_dict_no_coverage(tmp_path):
    """Without a coverage file, returns empty dict (no error)."""
    result = build_coverage_map(str(tmp_path))
    assert isinstance(result, dict)


def test_reads_coverage_json(tmp_path):
    """Should parse a coverage.json file correctly."""
    cov_data = {
        "files": {
            "src/payment.py": {"summary": {"percent_covered": 72.5}},
            "src/utils.py":   {"summary": {"percent_covered": 100.0}},
        }
    }
    cov_file = tmp_path / "coverage.json"
    cov_file.write_text(json.dumps(cov_data), encoding="utf-8")

    result = build_coverage_map(str(tmp_path))
    assert isinstance(result, dict)
    assert len(result) == 2
    # Values are floats in 0-100 range
    for v in result.values():
        assert 0.0 <= v <= 100.0


def test_missing_summary_defaults_zero(tmp_path):
    """Files without a 'summary' section should get 0.0 coverage."""
    cov_data = {"files": {"src/missing.py": {}}}
    (tmp_path / "coverage.json").write_text(json.dumps(cov_data), encoding="utf-8")
    result = build_coverage_map(str(tmp_path))
    assert list(result.values())[0] == 0.0


def test_corrupted_json_returns_empty(tmp_path):
    """Corrupted coverage.json should return empty dict."""
    (tmp_path / "coverage.json").write_text("NOT_JSON", encoding="utf-8")
    result = build_coverage_map(str(tmp_path))
    assert result == {}
