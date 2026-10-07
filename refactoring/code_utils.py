"""
Code utilities — locate, replace and measure a single function in a module.
"""

from __future__ import annotations

import ast
import textwrap

from dataset.function_index import _radon_complexity_by_line
from enricher.function_history import function_spans


class PatchError(ValueError):
    """The proposed code cannot be applied safely."""


def locate(source: str, qualname: str) -> tuple[int, int, int]:
    """Return (start_line incl. decorators, end_line, def_line) of *qualname*."""
    tree = ast.parse(source)

    def walk(node: ast.AST, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                if qual == qualname:
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    return start, child.end_lineno, child.lineno
                found = walk(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                found = walk(child, f"{prefix}{child.name}.")
            else:
                found = walk(child, prefix)
            if found:
                return found
        return None

    found = walk(tree, "")
    if not found:
        raise PatchError(f"function {qualname} not found")
    return found


def _strip_fences(code: str) -> str:
    lines = code.strip("\n").splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def replace_function(source: str, qualname: str, new_code: str) -> tuple[str, int, int]:
    """
    Replace *qualname* (with decorators) by *new_code*, re-indented to the
    original indentation.  Returns (new_source, block_start, block_end).
    Raises PatchError if the result does not parse or the function vanished.
    """
    start, end, _ = locate(source, qualname)
    lines = source.splitlines(keepends=True)
    original_first = lines[start - 1]
    indent = original_first[: len(original_first) - len(original_first.lstrip())]

    body = textwrap.dedent(_strip_fences(new_code).replace("\t", "    "))
    try:
        replacement = ast.parse(body)
        original_block = ast.parse(textwrap.dedent("".join(lines[start - 1:end])))
    except SyntaxError as exc:
        raise PatchError(f"replacement does not parse: {exc}") from exc
    original_node = original_block.body[0]
    functions = replacement.body
    if not functions or any(not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in functions):
        raise PatchError("Replacement may contain only the target function and private helper definitions")
    names = [n.name for n in functions]
    if len(set(names)) != len(names) or names.count(original_node.name) != 1:
        raise PatchError("Target must occur exactly once, with unique helper names")
    target_node = next(n for n in functions if n.name == original_node.name)
    if (type(target_node) is not type(original_node)
            or ast.dump(target_node.args) != ast.dump(original_node.args)
            or [ast.dump(d) for d in target_node.decorator_list] != [ast.dump(d) for d in original_node.decorator_list]):
        raise PatchError("Function signature, async type and decorators must be preserved")
    existing_names = {n.name for n in ast.walk(ast.parse(source))
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    if any(n.name != original_node.name and (not n.name.startswith('_') or n.name in existing_names)
           for n in functions):
        raise PatchError("New helpers must be private and must not collide with existing definitions")
    new_lines = [(indent + ln if ln.strip() else "") + "\n" for ln in body.splitlines()]
    if not new_lines:
        raise PatchError("empty replacement")
    patched = "".join(lines[: start - 1] + new_lines + lines[end:])
    try:
        ast.parse(patched)
    except SyntaxError as exc:
        raise PatchError(f"patched module does not parse: {exc}") from exc
    locate(patched, qualname)  # the target must still exist under the same name
    return patched, start, start + len(new_lines) - 1


def function_complexity(source: str, qualname: str) -> int:
    _, _, def_line = locate(source, qualname)
    return _radon_complexity_by_line(source).get(def_line, 1)


def block_complexity(source: str, block_start: int, block_end: int) -> int:
    """Sum of cyclomatic complexity of every function defined inside the block."""
    cc = _radon_complexity_by_line(source)
    tree = ast.parse(source)
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if block_start <= node.lineno <= block_end:
                total += cc.get(node.lineno, 1)
    return total


def function_names_in_block(source: str, block_start: int, block_end: int) -> list[str]:
    return [q for q, s, e in function_spans(source) if s >= block_start and e <= block_end]
