"""Tests for enricher.git_enricher module."""

import os
from unittest.mock import MagicMock, patch
from enricher.git_enricher import build_file_context


def test_non_git_repo_returns_empty(tmp_path):
    """A plain directory (not a git repo) should return an empty dict."""
    result = build_file_context(str(tmp_path))
    assert isinstance(result, dict)
    assert result == {}


def test_default_structure(tmp_path):
    """build_file_context always returns a dict."""
    result = build_file_context(str(tmp_path))
    assert isinstance(result, dict)


def test_values_are_non_negative():
    """All numeric values in results should be >= 0."""
    result = build_file_context(".")
    for file_path, ctx in result.items():
        assert ctx["recent_commits"] >= 0
        assert ctx["defect_commits"] >= 0
        assert ctx["commit_authors"] >= 0
        # last_modified_days is -1 only if no commit found
        assert ctx["last_modified_days"] >= -1


def test_defect_commits_lte_recent():
    """defect_commits should never exceed recent_commits."""
    result = build_file_context(".")
    for _, ctx in result.items():
        assert ctx["defect_commits"] <= ctx["recent_commits"]
