"""
Prompt builders — render a debt item into the context block the agents read.

Two variants are produced so the evaluation can isolate the value of context:

  * ``with_context=True``  static metrics + repository context + RAG neighbours
  * ``with_context=False`` static metrics + source code only (ablation)

Both variants include the function's source code, so any difference between
them is attributable to repository context, not to seeing the code.
Ground-truth fields (post-cutoff history) never appear here.
"""

from __future__ import annotations

from typing import Any
from pathlib import Path

MAX_SOURCE_LINES = 250


def read_function_source(item: dict[str, Any]) -> str:
    """Return the function's source text from the snapshot (decorators included)."""
    path = item.get("abs_file", "")
    if not Path(path).is_file() and item.get("rel_file") and item.get("repo"):
        import config

        root = Path(config.PROJECT_DIR) / "targets" / f"{item['repo']}_snapshot"
        candidate = (root / item["rel_file"]).resolve()
        if candidate.is_relative_to(root.resolve()):
            path = str(candidate)
        if not Path(path).is_file():
            root = Path(config.DATA_DIR) / "snapshots" / str(item["repo"])
            candidate = (root / item["rel_file"]).resolve()
            if candidate.is_relative_to(root.resolve()):
                path = str(candidate)
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return "<source unavailable>"
    body = lines[item["start_line"] - 1 : item["end_line"]]
    if len(body) > MAX_SOURCE_LINES:
        omitted = len(body) - MAX_SOURCE_LINES
        body = body[:MAX_SOURCE_LINES] + [f"# … {omitted} more line(s) not shown"]
    return "\n".join(body)


def _static_block(item: dict[str, Any]) -> str:
    smells = ", ".join(item.get("smell_types", [])) or "none"
    messages = "\n".join(f"    - {m}" for m in item.get("tool_messages", [])) or "    - none"
    return (
        f"Static analysis:\n"
        f"  - Cyclomatic complexity: {item['complexity']} (Radon rank {item['complexity_rank']})\n"
        f"  - LOC: {item['loc']} | Parameters: {item['params']}\n"
        f"  - File maintainability index: {item['maintainability_index']}\n"
        f"  - Code smells (lint/refactor findings): {item['code_smells']} [{smells}]\n"
        f"  - Style-only findings: {item.get('style_issues', 0)}\n"
        f"  - Duplicated lines: {item['duplicate_lines']}\n"
        f"  - Flagged as dead code: {'yes' if item['dead_code'] else 'no'}\n"
        f"  - Composite static debt score: {item['static_score']} / 10\n"
        f"  Tool findings:\n{messages}"
    )


def _context_block(item: dict[str, Any], history_window: str) -> str:
    doc = item.get("docstring") or "(no docstring)"
    return (
        f"Repository context (all history is from before the analysis snapshot):\n"
        f"  - Call sites in the library: {item['callers']} | References from tests: "
        f"{item['test_references']}\n"
        f"  - Visibility: {'private helper' if item.get('is_private') else 'public / API surface'}\n"
        f"  - Function-level history ({history_window}): changed in {item['func_commits']} "
        f"commit(s), {item['func_fix_commits']} of them bug fixes\n"
        f"  - File-level history (2 years): {item['recent_commits']} commit(s), "
        f"{item['defect_commits']} defect-linked, {item['commit_authors']} author(s), "
        f"last change {item['last_modified_days']} day(s) before snapshot\n"
        + (f"  - Test coverage: function {item['function_coverage']}%, file {item['test_coverage']}%\n"
           if item.get("coverage_known", True) else
           "  - Test coverage: not measured (the repository's tests were not executed)\n") +
        f"  - Documentation: \"{doc}\""
    )


def render_item(
    item: dict[str, Any],
    source: str,
    with_context: bool = True,
    rag_context: str = "",
    history_window: str = "5 years",
) -> str:
    """Render one item as the shared context block for every agent."""
    parts = [
        f"Issue: {item['function']}() in {item['file']} (line {item['line']})",
        _static_block(item),
    ]
    if with_context:
        parts.append(_context_block(item, history_window))
        if rag_context:
            parts.append(
                "Similar debt items elsewhere in this repository (retrieved via RAG, "
                "for calibration — they are NOT the item under review):\n" + rag_context)
    parts.append(f"Source code:\n```python\n{source}\n```")
    return "\n\n".join(parts)
