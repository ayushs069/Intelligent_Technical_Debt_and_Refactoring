"""
Function Index — per-function static facts for every function in a source tree.

For each function/method it records the span (incl. decorators), qualname,
cyclomatic complexity (Radon), LOC, parameter count, docstring, and the
file-level maintainability index (Radon MI).
"""

from __future__ import annotations

import ast
import os

from radon.complexity import cc_rank, cc_visit
from radon.metrics import mi_visit

from enricher.function_history import canonical_path


def iter_python_files(root: str, exclude_dirs: tuple[str, ...] = ()) -> list[str]:
    """Sorted list of .py files under *root* (skipping hidden/venv/excluded dirs)."""
    skip = {".git", ".venv", "venv", "__pycache__", "build", "dist", *exclude_dirs}
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip and not d.startswith("."))
        files.extend(os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith(".py"))
    return files


def _radon_complexity_by_line(source: str) -> dict[int, int]:
    """Map def line -> cyclomatic complexity for every function, method and closure."""
    result: dict[int, int] = {}

    def collect(blocks) -> None:
        for block in blocks:
            if hasattr(block, "methods"):  # radon Class
                collect(block.methods)
                continue
            result[block.lineno] = block.complexity
            collect(getattr(block, "closures", []))

    try:
        collect(cc_visit(source))
    except (SyntaxError, ValueError):
        pass
    return result


def index_functions(repo_root: str, source_dir: str) -> list[dict]:
    """
    Index every function under *source_dir*.  ``file`` is the canonical
    repo-relative path (see ``canonical_path``); ``abs_file`` is the real path.
    """
    records: list[dict] = []
    for path in iter_python_files(source_dir):
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = os.path.relpath(path, repo_root).replace("\\", "/")
        canon = canonical_path(rel)
        complexity = _radon_complexity_by_line(source)
        try:
            mi = round(float(mi_visit(source, multi=True)), 2)
        except (SyntaxError, ValueError):
            mi = 100.0

        def visit(node: ast.AST, prefix: str, class_name: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qual = f"{prefix}{child.name}"
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    end = child.end_lineno or child.lineno
                    cc = complexity.get(child.lineno, 1)
                    args = child.args
                    n_params = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
                    if class_name and args.args and args.args[0].arg in ("self", "cls"):
                        n_params -= 1
                    doc = ast.get_docstring(child) or ""
                    records.append({
                        "id": f"{canon}::{qual}",
                        "file": canon,
                        "rel_file": rel,
                        "abs_file": os.path.abspath(path),
                        "function": qual,
                        "name": child.name,
                        "class_name": class_name,
                        "line": child.lineno,
                        "start_line": start,
                        "end_line": end,
                        "loc": end - start + 1,
                        "params": n_params,
                        "complexity": cc,
                        "complexity_rank": cc_rank(cc),
                        "maintainability_index": mi,
                        "docstring": " ".join(doc.split())[:300],
                        "is_private": child.name.startswith("_") and not child.name.startswith("__"),
                    })
                    visit(child, qual + ".", class_name)
                elif isinstance(child, ast.ClassDef):
                    visit(child, f"{prefix}{child.name}.", child.name)
                else:
                    visit(child, prefix, class_name)

        visit(tree, "", "")
    return records


def function_coverage(cov_file_entry: dict, start: int, end: int) -> float | None:
    """Line coverage (0–100) of the statements in [start, end], or None if unknown."""
    executed = set(cov_file_entry.get("executed_lines", []))
    missing = set(cov_file_entry.get("missing_lines", []))
    statements = {ln for ln in executed | missing if start <= ln <= end}
    if not statements:
        return None
    return round(100.0 * len(statements & executed) / len(statements), 1)
