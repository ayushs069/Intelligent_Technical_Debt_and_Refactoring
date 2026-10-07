"""
Duplication — a lightweight, dependency-free clone detector (jscpd substitute).

Each file is reduced to its "significant" lines (stripped, non-blank,
non-comment).  Every window of ``window`` consecutive significant lines is
hashed; a window that occurs at two or more places in the tree marks all of
its source lines as duplicated.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict


def _significant_lines(source: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for lineno, raw in enumerate(source.splitlines(), 1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        out.append((lineno, " ".join(text.split())))
    return out


def find_duplicate_lines(sources: dict[str, str], window: int = 6) -> dict[str, set[int]]:
    """
    *sources* maps a file key to its source text.  Returns
    ``{file_key: {duplicated line numbers}}``.
    """
    occurrences: dict[str, list[tuple[str, list[int]]]] = defaultdict(list)
    for key, source in sources.items():
        sig = _significant_lines(source)
        for i in range(len(sig) - window + 1):
            chunk = sig[i : i + window]
            # Ignore windows made only of trivial lines (brackets, pass, return).
            if sum(len(text) for _, text in chunk) < 60:
                continue
            digest = hashlib.sha1("\n".join(t for _, t in chunk).encode()).hexdigest()
            occurrences[digest].append((key, [ln for ln, _ in chunk]))

    duplicated: dict[str, set[int]] = defaultdict(set)
    for places in occurrences.values():
        if len(places) < 2:
            continue
        for key, lines in places:
            duplicated[key].update(lines)
    return dict(duplicated)
