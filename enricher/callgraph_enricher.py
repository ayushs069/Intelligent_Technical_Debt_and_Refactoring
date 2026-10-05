"""
Call-graph Enricher — uses Python's stdlib `ast` module to count
how many times each function/method is called across the entire repository.

For each (file, function) pair it computes:
  - callers : number of call sites found in the repo that reference this name
"""

from __future__ import annotations

import ast
import os
from collections import defaultdict


class _CallCollector(ast.NodeVisitor):
    """Collect every function name that appears as a bare Call node."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        # Direct call:  foo()
        if isinstance(node.func, ast.Name):
            self.calls.append(node.func.id)
        # Method/attribute call:  obj.foo()
        elif isinstance(node.func, ast.Attribute):
            self.calls.append(node.func.attr)
        self.generic_visit(node)


class _FunctionCollector(ast.NodeVisitor):
    """Collect names of all function and method definitions in a file."""

    def __init__(self) -> None:
        self.functions: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.functions.append(node.name)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # handle async defs too


def _parse_file(path: str) -> ast.AST | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            source = fh.read()
        return ast.parse(source, filename=path)
    except SyntaxError:
        return None


def build_call_counts(repo_path: str) -> dict[str, int]:
    """
    Walk all .py files in *repo_path* and return a mapping:
        function_name -> total call count across the repository
    """
    call_counter: defaultdict[str, int] = defaultdict(int)

    for root, dirs, files in os.walk(repo_path):
        # Skip hidden dirs and venvs
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".")
            and d not in ("venv", "env", "site-packages", "node_modules", "data")
        ]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            tree = _parse_file(os.path.join(root, fname))
            if tree is None:
                continue
            collector = _CallCollector()
            collector.visit(tree)
            for name in collector.calls:
                call_counter[name] += 1

    return dict(call_counter)
