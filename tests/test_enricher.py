"""Integration tests for enricher.enricher (Phase 2 orchestrator)."""

import json
import os
from enricher.enricher import enrich


def _make_phase1_dataset(path, items=None):
    """Write a minimal Phase 1 JSON dataset to *path*."""
    if items is None:
        items = [
            {
                "repo": "test_repo",
                "file": "src/foo.py",
                "function": "bar",
                "line": 10,
                "issue_type": "high_complexity",
                "tool": "radon",
                "severity": "MEDIUM",
                "complexity": 12,
                "maintainability_index": 65.0,
                "loc": 40,
                "message": "High complexity",
                "static_score": 3.4,
            }
        ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh)


def test_enrich_preserves_phase1_fields(tmp_path):
    """Enrichment must keep all Phase 1 fields intact."""
    input_file = tmp_path / "phase1.json"
    output_file = tmp_path / "enriched.json"
    _make_phase1_dataset(str(input_file))

    enrich(
        phase1_path=str(input_file),
        repo_path=str(tmp_path),
        output_path=str(output_file),
    )

    with open(str(output_file), encoding="utf-8") as fh:
        result = json.load(fh)

    assert len(result) == 1
    issue = result[0]

    # All Phase 1 keys must still be present
    phase1_keys = {
        "repo", "file", "function", "line", "issue_type", "tool",
        "severity", "complexity", "maintainability_index",
        "loc", "message", "static_score",
    }
    for key in phase1_keys:
        assert key in issue, f"Phase 1 key '{key}' missing from enriched output"


def test_enrich_adds_phase2_fields(tmp_path):
    """Enrichment must add all Phase 2 context fields."""
    input_file = tmp_path / "phase1.json"
    output_file = tmp_path / "enriched.json"
    _make_phase1_dataset(str(input_file))

    enrich(
        phase1_path=str(input_file),
        repo_path=str(tmp_path),
        output_path=str(output_file),
    )

    with open(str(output_file), encoding="utf-8") as fh:
        result = json.load(fh)

    issue = result[0]
    phase2_keys = {
        "recent_commits", "defect_commits",
        "commit_authors", "last_modified_days",
        "callers", "test_coverage",
    }
    for key in phase2_keys:
        assert key in issue, f"Phase 2 key '{key}' missing from enriched output"


def test_enrich_field_types(tmp_path):
    """Phase 2 fields should have the correct types."""
    input_file = tmp_path / "phase1.json"
    output_file = tmp_path / "enriched.json"
    _make_phase1_dataset(str(input_file))

    enrich(
        phase1_path=str(input_file),
        repo_path=str(tmp_path),
        output_path=str(output_file),
    )

    with open(str(output_file), encoding="utf-8") as fh:
        result = json.load(fh)

    issue = result[0]
    assert isinstance(issue["recent_commits"], int)
    assert isinstance(issue["defect_commits"], int)
    assert isinstance(issue["commit_authors"], int)
    assert isinstance(issue["last_modified_days"], int)
    assert isinstance(issue["callers"], int)
    assert isinstance(issue["test_coverage"], float)


def test_enrich_output_file_created(tmp_path):
    """Output file must be created even if enrichers return empty data."""
    input_file = tmp_path / "phase1.json"
    output_file = tmp_path / "out" / "enriched.json"
    _make_phase1_dataset(str(input_file))

    enrich(
        phase1_path=str(input_file),
        repo_path=str(tmp_path),
        output_path=str(output_file),
    )

    assert os.path.isfile(str(output_file))
