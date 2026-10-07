"""
Function History — attributes git commits to the individual functions they touched.

For every non-merge commit in a time window, the zero-context diff against its
first parent is parsed and each changed line (new-side line numbers) is mapped
to the innermost function that contains it in the post-commit version of the
file (located with stdlib ``ast``).

The same routine serves two purposes:
  - repository context *before* a cutoff  (func_commits / func_fix_commits)
  - ground truth *after* a cutoff         (future fix commits per function)

Commits are classified as defect fixes with a keyword heuristic applied to the
commit message and, for commits that arrived through a merge, to the merge
commit's message (pull-request titles often carry the "fix" wording while the
individual commits say "update models.py").

Paths are canonicalised by stripping a leading ``src/`` so that history survives
the common "move package into src/" restructuring.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import defaultdict
from datetime import datetime

_DEFECT_RE = re.compile(
    r"\b(fix(e[sd])?|bug(s|fix)?|error|crash|regression|cve|vulnerab\w*|"
    r"broken|incorrect|wrong|hotfix|resolve[sd]?\s+#\d+|issue\s*#\d+)\b",
    re.IGNORECASE,
)
# Subjects that mention "fix" but do not repair behaviour.
_NON_DEFECT_RE = re.compile(
    r"\b(typos?|docs?|docstrings?|documentation|readme|changelog|comments?|"
    r"lint(ing)?|flake8|black|isort|pyright|mypy|ci|workflows?|typing|"
    r"type\s+hints?|annotations?|release|bump)\b",
    re.IGNORECASE,
)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def is_defect_fix(message: str) -> bool:
    """Return True if a commit message looks like a behavioural bug fix."""
    if not message:
        return False
    subject = message.strip().splitlines()[0]
    if _NON_DEFECT_RE.search(subject):
        return False
    return bool(_DEFECT_RE.search(message))


def canonical_path(path: str) -> str:
    """Repo-relative POSIX path with a leading ``src/`` removed."""
    p = path.replace("\\", "/").lstrip("./")
    return p[4:] if p.startswith("src/") else p


def _git(repo_path: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


# ── ast helpers ──────────────────────────────────────────────────────────────

def function_spans(source: str) -> list[tuple[str, int, int]]:
    """
    Return ``(qualname, start_line, end_line)`` for every function/method in
    *source*.  ``start_line`` includes decorators.  Nested functions get
    dotted qualnames (``Outer.method.inner``).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    spans: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                spans.append((qual, start, child.end_lineno or child.lineno))
                visit(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def innermost_function(spans: list[tuple[str, int, int]], line: int) -> str | None:
    """Qualname of the smallest span containing *line*, or None."""
    best: tuple[str, int, int] | None = None
    for qual, start, end in spans:
        if start <= line <= end and (best is None or (end - start) < (best[2] - best[1])):
            best = (qual, start, end)
    return best[0] if best else None


# ── diff parsing ─────────────────────────────────────────────────────────────

def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """
    Parse ``git show --unified=0`` output into {new_path: {new-side line numbers}}.
    Pure deletions are attributed to the line where the deletion happened.
    """
    changed: dict[str, set[int]] = defaultdict(set)
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
        elif line.startswith("@@") and current:
            m = _HUNK_RE.match(line)
            if not m:
                continue
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            if count == 0:
                changed[current].add(max(start, 1))
            else:
                changed[current].update(range(start, start + count))
    return dict(changed)


def _merge_messages(repo_path: str, since: str, until: str) -> dict[str, str]:
    """Map commit sha -> message of the merge commit that brought it into mainline."""
    out = _git(repo_path, "log", "--merges", "--first-parent",
               f"--since={since}", f"--until={until}", "--format=%H %P%x1f%B%x1e")
    inherited: dict[str, str] = {}
    for record in out.split("\x1e"):
        record = record.strip()
        if not record or "\x1f" not in record:
            continue
        header, message = record.split("\x1f", 1)
        parts = header.split()
        if len(parts) < 3:
            continue
        p1, p2 = parts[1], parts[2]
        try:
            shas = _git(repo_path, "rev-list", f"{p1}..{p2}").split()
        except RuntimeError:
            continue
        for sha in shas:
            inherited.setdefault(sha, message)
    return inherited


class _BlobReader:
    """Reads many ``<rev>:<path>`` blobs through one ``git cat-file --batch`` process."""

    def __init__(self, repo_path: str) -> None:
        self._proc = subprocess.Popen(["git", "-C", repo_path, "cat-file", "--batch"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)

    def read(self, rev: str, path: str) -> str | None:
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(f"{rev}:{path}\n".encode("utf-8"))
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode("utf-8", "replace").split()
        if len(header) != 3 or header[1] != "blob":
            return None  # "<rev>:<path> missing"
        data = self._proc.stdout.read(int(header[2]))
        self._proc.stdout.read(1)  # trailing newline
        return data.decode("utf-8", "replace")

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait(timeout=30)


def build_function_history(
    repo_path: str,
    since: datetime,
    until: datetime,
    pathspec: tuple[str, ...] = ("*.py",),
    exclude_prefixes: tuple[str, ...] = ("tests/", "test/", "docs/"),
    max_functions_per_commit: int = 25,
    progress=None,
) -> dict[str, dict]:
    """
    Return ``{"<canonical_file>::<qualname>": {...}}`` for commits in
    ``[since, until)`` with keys:

      commits       : number of commits that changed the function
      fix_commits   : subset classified as defect fixes
      fix_shas      : short shas of those fix commits (for auditability)

    Sweeping commits that touch more than *max_functions_per_commit* functions
    (re-formatting, type-annotation passes, mass renames) are skipped: they say
    nothing about the change-proneness of any individual function.

    All diffs come from one ``git log -p`` call and all file versions from one
    ``git cat-file --batch`` process, so cost grows with history size rather than
    with the number of git process launches.  *progress(done, total)* is called
    every 25 commits when given.
    """
    since_s, until_s = since.isoformat(), until.isoformat()
    log = _git(repo_path, "log", "--no-merges", f"--since={since_s}", f"--until={until_s}",
               "--format=%x1e%H%x1f%B%x1f", "-p", "--unified=0", "--no-color", "-M",
               "--", *pathspec)
    inherited = _merge_messages(repo_path, since_s, until_s)

    records = [r for r in log.split("\x1e") if r.count("\x1f") >= 2]
    history: dict[str, dict] = {}
    span_cache: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
    blobs = _BlobReader(repo_path)
    try:
        for done, record in enumerate(records, 1):
            sha, message, diff = record.split("\x1f", 2)
            sha = sha.strip()
            is_fix = is_defect_fix(message) or is_defect_fix(inherited.get(sha, ""))

            touched: set[str] = set()
            for path, lines in parse_changed_lines(diff).items():
                canon = canonical_path(path)
                if not canon.endswith(".py") or canon.startswith(exclude_prefixes):
                    continue
                key = (sha, path)
                if key not in span_cache:
                    source = blobs.read(sha, path)
                    span_cache[key] = function_spans(source) if source is not None else []
                for ln in lines:
                    qual = innermost_function(span_cache[key], ln)
                    if qual:
                        touched.add(f"{canon}::{qual}")

            if progress and (done % 25 == 0 or done == len(records)):
                progress(done, len(records))
            if len(touched) > max_functions_per_commit:
                continue
            for fid in touched:
                entry = history.setdefault(fid, {"commits": 0, "fix_commits": 0, "fix_shas": []})
                entry["commits"] += 1
                if is_fix:
                    entry["fix_commits"] += 1
                    entry["fix_shas"].append(sha[:8])
    finally:
        blobs.close()

    return history
