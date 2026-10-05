"""Integration test — runs the full pipeline on a temp repo."""

import json
import os

from analyzer.analyzer import run_all, save_results


def test_full_pipeline(sample_repo_str, tmp_path):
    """
    End-to-end: run all analyzers, save JSON, verify output.
    """
    output_file = str(tmp_path / "output" / "debt_dataset.json")

    # Run the full pipeline
    issues, repo_name = run_all(sample_repo_str)

    # Should find some issues in the deliberately messy code
    assert len(issues) >= 1, f"Expected issues but got {len(issues)}"

    # Save and verify file creation
    saved_path = save_results(issues, output_file)
    assert os.path.isfile(saved_path)

    # Verify JSON structure
    with open(saved_path, encoding="utf-8") as fh:
        loaded = json.load(fh)

    assert isinstance(loaded, list)
    assert len(loaded) == len(issues)

    # Check schema of first issue
    required_keys = {
        "repo", "file", "function", "line", "issue_type", "tool",
        "severity", "complexity", "maintainability_index",
        "loc", "message", "static_score",
    }
    for issue in loaded:
        missing = required_keys - issue.keys()
        assert not missing, f"Missing keys: {missing}"

    # Verify sorted by static_score descending
    scores = [i["static_score"] for i in loaded]
    assert scores == sorted(scores, reverse=True), "Issues should be sorted by static_score desc"


def test_multiple_tools_represented(sample_repo_str):
    """The sample repo should produce findings from multiple tools."""
    issues, _ = run_all(sample_repo_str)
    tools = {i["tool"] for i in issues}
    # At minimum, ruff and vulture should find something in the sample
    assert len(tools) >= 2, f"Expected findings from ≥2 tools, got {tools}"
