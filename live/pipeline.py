"""
Live pipeline — analyse any public Python repository from its URL, stage by stage.

Stages (each emits a ``stage`` event with a compact result payload so the UI can
show results as soon as they exist):

  clone     shallow clone of the recent history window
  static    Phase 1: Radon, Ruff, Pylint, Vulture (read-only tools)
  index     function index, issue attribution, duplication, call graph
  context   Phase 2: file and function-level git history
  items     debt candidates, static score, context heuristic
  rag       Phase 3: ChromaDB + MiniLM neighbours for every candidate
  llm       Phase 4: LLM review of a shortlist (optional, needs a funded key)
  report    final fix-first list

Safety: the target repository's code is never executed — no tests, no imports,
no setup scripts. Coverage is therefore reported as "not measured".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from analyzer import pylint_analyzer, radon_analyzer, ruff_analyzer, vulture_analyzer
from analyzer.normalizer import _compute_static_score, _map_severity, normalize
from dataset.builder import attach_issues, is_candidate
from dataset.duplication import find_duplicate_lines
from dataset.function_index import index_functions, iter_python_files
from enricher.callgraph_enricher import build_call_counts
from enricher.function_history import build_function_history, canonical_path
from enricher.function_history import is_defect_fix

URL_RE = re.compile(
    r"^https://(github\.com|gitlab\.com|bitbucket\.org)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_SKIP_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "benchmarks",
              "bench", "scripts", "tools", "build", "dist", "site-packages", ".github"}

MAX_SOURCE_FILES = 800
PYLINT_MAX_FILES = 250
RAG_MAX_ITEMS = 80
SOURCE_PREVIEW_LINES = 140

STAGES = [
    ("clone", "Clone repository"),
    ("static", "Static analysis"),
    ("index", "Function index"),
    ("context", "Repository context"),
    ("items", "Debt candidates"),
    ("rag", "RAG retrieval"),
    ("llm", "LLM agents"),
    ("report", "Fix-first report"),
]


class PipelineError(RuntimeError):
    """A stage failed in a way the user can act on."""


def parse_repo_url(url: str) -> tuple[str, str, str]:
    """Validate a hosted-git HTTPS URL. Returns (clean_url, owner, name)."""
    url = (url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):  # owner/repo shorthand
        url = f"https://github.com/{url}"
    m = URL_RE.match(url)
    if not m:
        raise PipelineError("Enter a public repository URL such as https://github.com/psf/requests")
    host, owner, name = m.groups()
    if name.endswith(".git"):
        name = name[:-4]
    return f"https://{host}/{owner}/{name}.git", owner, name


def detect_source_dir(root: str) -> str:
    """Pick the main Python package: the importable dir with the most Python lines."""
    def py_lines(path: str) -> int:
        total = 0
        for f in iter_python_files(path, exclude_dirs=tuple(_SKIP_DIRS)):
            try:
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    total += sum(1 for _ in fh)
            except OSError:
                pass
        return total

    candidates: list[tuple[int, str]] = []
    for base in (root, os.path.join(root, "src"), os.path.join(root, "lib")):
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            path = os.path.join(base, entry)
            if (entry in _SKIP_DIRS or entry.startswith((".", "_")) or not os.path.isdir(path)
                    or not os.path.isfile(os.path.join(path, "__init__.py"))):
                continue
            candidates.append((py_lines(path), path))
    if candidates:
        return max(candidates)[1]
    return root


def _remove_tree(path: str) -> None:
    """rmtree that also clears read-only files (git pack files on Windows)."""
    import stat

    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=on_error)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=on_error)


def _git(repo: str, *args: str, timeout: int = 120) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if proc.returncode != 0:
        raise PipelineError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "git failed")
    return proc.stdout


class LivePipeline:
    """Runs every stage for one repository and reports through ``emit``."""

    def __init__(self, url: str, workdir: str, emit: Callable[[dict], None], *,
                 history_years: int = 3, llm_factory: Callable[[], Any] | None = None,
                 llm_label: str = "", shortlist: int = 10, clone_url: str | None = None) -> None:
        self.clean_url, self.owner, self.name = parse_repo_url(url) if clone_url is None else (clone_url, "local", os.path.basename(clone_url.rstrip("/\\")))
        self.workdir = workdir
        self.emit = emit
        self.history_years = max(1, min(int(history_years), 10))
        self.llm_factory = llm_factory
        self.llm_label = llm_label
        self.shortlist = max(3, min(int(shortlist), 25))
        self.now = datetime.now(tz=timezone.utc)
        self.repo_dir = os.path.join(workdir, f"{self.owner}__{self.name}")
        self.state: dict[str, Any] = {}

    # ── plumbing ─────────────────────────────────────────────────────────
    def _log(self, stage: str, text: str) -> None:
        self.emit({"type": "log", "stage": stage, "text": text})

    def _stage(self, key: str, fn: Callable[[], dict]) -> dict:
        self.emit({"type": "stage", "stage": key, "status": "running"})
        started = time.time()
        try:
            data = fn() or {}
        except PipelineError as exc:
            self.emit({"type": "stage", "stage": key, "status": "error", "message": str(exc),
                       "elapsed": round(time.time() - started, 1)})
            raise
        status = data.pop("_status", "done")
        message = data.pop("_message", "")
        self.emit({"type": "stage", "stage": key, "status": status, "message": message,
                   "elapsed": round(time.time() - started, 1), "data": data})
        return data

    def run(self) -> dict:
        for key, _ in STAGES:
            self._stage(key, getattr(self, f"stage_{key}"))
        return self.state.get("summary", {})

    def _clean(self, text: str) -> str:
        """Strip this machine's absolute clone path from tool messages."""
        for prefix in {self.repo_dir, os.path.normpath(self.repo_dir),
                       self.repo_dir.replace("\\", "/")}:
            text = text.replace(prefix + os.sep, "").replace(prefix + "/", "").replace(prefix, "")
        return text

    # ── stages ───────────────────────────────────────────────────────────
    def stage_clone(self) -> dict:
        os.makedirs(self.workdir, exist_ok=True)
        if os.path.isdir(self.repo_dir):
            _remove_tree(self.repo_dir)
            if os.path.exists(self.repo_dir):
                raise PipelineError(f"Could not remove the previous clone at {self.repo_dir}.")
        since = (self.now - timedelta(days=365 * self.history_years + 30)).date().isoformat()
        self._log("clone", f"git clone --shallow-since={since} {self.clean_url}")
        cmd = ["git", "clone", "--quiet", "--no-tags", "--single-branch",
               f"--shallow-since={since}", self.clean_url, self.repo_dir]
        if self.owner == "local":  # tests: local paths do not support shallow clones
            cmd = ["git", "clone", "--quiet", "--no-tags", self.clean_url, self.repo_dir]
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, check=False)
        if proc.returncode != 0 and "shallow" in (proc.stderr or "").lower():
            self._log("clone", "No commits in the window; falling back to the latest 300 commits")
            proc = subprocess.run(["git", "clone", "--quiet", "--no-tags", "--single-branch",
                                   "--depth", "300", self.clean_url, self.repo_dir],
                                  capture_output=True, text=True, timeout=600, env=env, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip().splitlines()
            if detail:
                self._log("clone", detail[-1][:200])
            raise PipelineError("Could not clone the repository. Check that the URL is public and correct.")

        head = _git(self.repo_dir, "log", "-1", "--format=%H%x1f%cI%x1f%s").strip().split("\x1f")
        commits = int(_git(self.repo_dir, "rev-list", "--count", "HEAD").strip() or 0)
        authors = len(set(_git(self.repo_dir, "log", "--format=%ae").split()))
        branch = _git(self.repo_dir, "rev-parse", "--abbrev-ref", "HEAD").strip()
        source = detect_source_dir(self.repo_dir)
        files = iter_python_files(source, exclude_dirs=tuple(_SKIP_DIRS))
        if not files:
            raise PipelineError("No Python source files found in this repository.")
        if len(files) > MAX_SOURCE_FILES:
            raise PipelineError(f"{len(files)} Python files is too large for a live run "
                                f"(limit {MAX_SOURCE_FILES}). Use run_dataset.py for big repositories.")
        loc = 0
        for f in files:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                loc += sum(1 for _ in fh)
        tests = next((os.path.join(self.repo_dir, d) for d in ("tests", "test", "testing")
                      if os.path.isdir(os.path.join(self.repo_dir, d))), "")
        self.state.update(source=source, tests=tests, files=files, head=head[0])
        rel_source = os.path.relpath(source, self.repo_dir).replace("\\", "/")
        self._log("clone", f"{commits} commits in window · source package: {rel_source}")
        return {"repo": f"{self.owner}/{self.name}", "url": self.clean_url.removesuffix(".git"),
                "branch": branch, "head": head[0][:10], "head_date": head[1][:10],
                "head_subject": head[2][:120], "commits": commits, "authors": authors,
                "python_files": len(files), "python_loc": loc, "source_dir": rel_source,
                "tests_dir": os.path.relpath(tests, self.repo_dir).replace("\\", "/") if tests else "",
                "history_years": self.history_years}

    def stage_static(self) -> dict:
        source, files = self.state["source"], self.state["files"]
        raw: list[dict] = []
        by_tool: dict[str, int] = {}
        tools = [("radon", radon_analyzer), ("ruff", ruff_analyzer),
                 ("pylint", pylint_analyzer), ("vulture", vulture_analyzer)]
        skipped = []
        for name, module in tools:
            if name == "pylint" and len(files) > PYLINT_MAX_FILES:
                skipped.append(f"pylint skipped ({len(files)} files > {PYLINT_MAX_FILES})")
                self._log("static", skipped[-1])
                continue
            t0 = time.time()
            found = module.run(source)
            raw.extend(found)
            by_tool[name] = len(found)
            self._log("static", f"{name}: {len(found)} finding(s) in {time.time() - t0:.1f}s")
        issues = normalize(raw, repo_name=self.name)
        self.state["issues"] = issues
        types = Counter(i["issue_type"] for i in issues)
        sev = Counter(i["severity"] for i in issues)
        worst = sorted(issues, key=lambda i: -i["static_score"])[:6]
        return {"total": len(issues), "by_tool": by_tool, "by_type": dict(types),
                "by_severity": dict(sev), "notes": skipped,
                "worst": [{"file": canonical_path(os.path.relpath(i["file"], self.repo_dir))
                           if os.path.isabs(i["file"]) else canonical_path(i["file"]),
                           "line": i["line"], "tool": i["tool"], "message": self._clean(i["message"])[:140],
                           "score": i["static_score"]} for i in worst]}

    def stage_index(self) -> dict:
        source = self.state["source"]
        functions = index_functions(self.repo_dir, source)
        attach_issues(functions, self.state["issues"], self.repo_dir)
        sources = {}
        for path in self.state["files"]:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                sources[canonical_path(os.path.relpath(path, self.repo_dir))] = fh.read()
        dup = find_duplicate_lines(sources)
        callers = build_call_counts(source)
        tests = self.state["tests"]
        test_refs = build_call_counts(tests) if tests else {}
        for fn in functions:
            fn["duplicate_lines"] = sum(1 for ln in dup.get(fn["file"], ())
                                        if fn["start_line"] <= ln <= fn["end_line"])
            fn["callers"] = callers.get(fn["name"], 0)
            fn["test_references"] = test_refs.get(fn["name"], 0)
        self.state["functions"] = functions
        ranks = Counter(fn["complexity_rank"] for fn in functions)
        top = sorted(functions, key=lambda f: -f["complexity"])[:8]
        self._log("index", f"{len(functions)} functions indexed; {sum(len(v) for v in dup.values())} duplicated lines")
        return {"functions": len(functions),
                "complexity_ranks": {r: ranks.get(r, 0) for r in "ABCDEF"},
                "duplicated_lines": sum(len(v) for v in dup.values()),
                "dead_code": sum(1 for f in functions if f["dead_code"]),
                "with_smells": sum(1 for f in functions if f["code_smells"]),
                "most_complex": [{"id": f["id"], "complexity": f["complexity"], "loc": f["loc"]} for f in top]}

    def stage_context(self) -> dict:
        lookback = 365 * self.history_years
        self._log("context", f"Mining file history ({self.history_years} years) …")
        raw_ctx = shallow_file_context(self.repo_dir, self.now, lookback)
        file_ctx: dict[str, dict] = {}
        for path, ctx in raw_ctx.items():
            canon = canonical_path(path)
            merged = file_ctx.setdefault(canon, {"recent_commits": 0, "defect_commits": 0,
                                                 "commit_authors": 0, "last_modified_days": -1})
            merged["recent_commits"] += ctx["recent_commits"]
            merged["defect_commits"] += ctx["defect_commits"]
            merged["commit_authors"] = max(merged["commit_authors"], ctx["commit_authors"])
            lm = ctx["last_modified_days"]
            if merged["last_modified_days"] < 0 or 0 <= lm < merged["last_modified_days"]:
                merged["last_modified_days"] = lm
        self._log("context", "Mapping commits to functions …")
        history = build_function_history(
            self.repo_dir, self.now - timedelta(days=lookback), self.now,
            progress=lambda done, total: self._log("context", f"  {done}/{total} commits mapped"))
        for fn in self.state["functions"]:
            ctx = file_ctx.get(fn["file"], {})
            fn["recent_commits"] = ctx.get("recent_commits", 0)
            fn["defect_commits"] = ctx.get("defect_commits", 0)
            fn["commit_authors"] = ctx.get("commit_authors", 0)
            fn["last_modified_days"] = ctx.get("last_modified_days", -1)
            h = history.get(fn["id"], {})
            fn["func_commits"] = h.get("commits", 0)
            fn["func_fix_commits"] = h.get("fix_commits", 0)
            fn["test_coverage"] = 0.0
            fn["function_coverage"] = 0.0
            fn["coverage_known"] = False
        churn = sorted(((f, c["recent_commits"], c["defect_commits"]) for f, c in file_ctx.items()
                        if f.endswith(".py")), key=lambda t: -t[1])[:8]
        hot = sorted(self.state["functions"], key=lambda f: (-f["func_fix_commits"], -f["func_commits"]))[:8]
        called = sorted(self.state["functions"], key=lambda f: -f["callers"])[:6]
        return {"files_with_history": len(file_ctx),
                "function_changes": sum(h["commits"] for h in history.values()),
                "fix_commits_mapped": sum(h["fix_commits"] for h in history.values()),
                "functions_with_fixes": sum(1 for h in history.values() if h["fix_commits"]),
                "churn": [{"file": f, "commits": c, "defects": d} for f, c, d in churn],
                "hot_functions": [{"id": f["id"], "commits": f["func_commits"], "fixes": f["func_fix_commits"]}
                                  for f in hot if f["func_commits"]],
                "most_called": [{"id": f["id"], "callers": f["callers"]} for f in called],
                "coverage": "not measured — repository code is never executed"}

    def stage_items(self) -> dict:
        from evaluation.evaluate import context_heuristic_scores

        functions = self.state["functions"]
        for fn in functions:
            fn["repo"] = self.name
            fn["severity"] = _map_severity(fn["complexity_rank"])
            fn["static_score"] = _compute_static_score(fn)
            fn["issue_type"], fn["tool"] = "function_debt", "aggregate"
            fn["message"] = "; ".join(fn["tool_messages"][:3]) or (
                f"Complexity {fn['complexity']} (rank {fn['complexity_rank']}), {fn['loc']} LOC")
        seen = Counter(fn["id"] for fn in functions)
        for fn in functions:  # overloads / property setters share a qualname
            if seen[fn["id"]] > 1:
                fn["id"] = f"{fn['id']}@{fn['line']}"
        for fn in functions:
            fn["source"] = _read_source(fn)
        # typing.overload stubs are signatures only; they are not debt.
        stubs = [fn for fn in functions if _is_overload_stub(fn["source"])]
        if stubs:
            self._log("items", f"Ignoring {len(stubs)} @overload stub(s)")
        items = [fn for fn in functions if is_candidate(fn) and not _is_overload_stub(fn["source"])]
        if not items:
            raise PipelineError("No function carries a debt signal — nothing to prioritise.")
        items.sort(key=lambda x: (-x["static_score"], -x["complexity"], -x["loc"], x["id"]))
        for rank, it in enumerate(items, 1):
            it["static_rank"] = rank
        heuristic = context_heuristic_scores(items)
        for rank, it in enumerate(sorted(items, key=lambda x: (-heuristic[x["id"]], x["id"])), 1):
            it["context_score"] = heuristic[it["id"]]
            it["context_rank"] = rank
        self.state["items"] = items
        self._log("items", f"{len(items)} of {len(functions)} functions are debt candidates")
        return {"candidates": len(items), "functions": len(functions),
                "items": [_slim(it) for it in items]}

    def stage_rag(self) -> dict:
        items = self.state["items"]
        subset = sorted(items, key=lambda x: x["context_rank"])[:RAG_MAX_ITEMS]
        try:
            from rag.embedder import format_issue_document
            from rag.retriever import DebtRetriever
            from rag.vector_store import DebtVectorStore
        except ImportError as exc:  # chromadb / sentence-transformers missing
            self.state["rag"] = {}
            return {"_status": "skipped", "_message": f"RAG libraries unavailable ({exc.name})."}
        self._log("rag", "Loading all-MiniLM-L6-v2 and indexing candidates in ChromaDB …")
        store = DebtVectorStore(collection_name=f"live_{re.sub(r'[^a-z0-9]', '_', self.name.lower())}_{int(time.time())}")
        store.ingest_issues(items)
        retriever = DebtRetriever(store)
        neighbours, contexts = {}, {}
        for it in subset:
            res = [r for r in retriever.retrieve(format_issue_document(it), top_k=6)
                   if r["metadata"].get("id") != it["id"]][:5]
            neighbours[it["id"]] = [{"id": r["metadata"].get("id", ""), "distance": round(r["distance"], 4)} for r in res]
            contexts[it["id"]] = retriever.format_retrieved_context_for_prompt(res)
        self.state["rag"] = contexts
        self._log("rag", f"Retrieved 5 neighbours for {len(subset)} candidates (self excluded)")
        return {"indexed": len(items), "queries": len(subset), "embedding_model": "all-MiniLM-L6-v2",
                "store": "ChromaDB", "neighbours": neighbours}

    def stage_llm(self) -> dict:
        if self.llm_factory is None:
            return {"_status": "skipped", "_message": "LLM review switched off for this run."}
        from agents.agents import run_single
        from agents.prompts import render_item
        from llm.client import LLMError

        try:
            llm = self.llm_factory()
        except (SystemExit, Exception) as exc:  # missing key, missing SDK
            return {"_status": "skipped", "_message": f"LLM unavailable: {exc}"}
        shortlist = sorted(self.state["items"], key=lambda x: x["context_rank"])[: self.shortlist]
        rag = self.state.get("rag", {})
        window = f"last {self.history_years} years"
        self._log("llm", f"{self.llm_label}: reviewing a shortlist of {len(shortlist)} candidates")
        results: dict[str, dict] = {}
        errors: list[str] = []

        def work(it: dict) -> tuple[str, dict | None, str]:
            ctx = render_item(it, it["source"], with_context=True, rag_context=rag.get(it["id"], ""),
                              history_window=window)
            for attempt in range(4):
                try:
                    return it["id"], run_single(llm, ctx), ""
                except LLMError as exc:
                    if "RateLimit" not in str(exc) or attempt == 3:
                        return it["id"], None, str(exc)
                    wait = 8 * (attempt + 1)
                    self._log("llm", f"rate limited; retrying {it['function']} in {wait}s")
                    time.sleep(wait)
            return it["id"], None, "rate limited"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(work, it) for it in shortlist]
            for done, fut in enumerate(as_completed(futures), 1):
                item_id, out, err = fut.result()
                if out:
                    results[item_id] = out
                    self.emit({"type": "llm_item", "id": item_id, "result": out,
                               "done": done, "total": len(shortlist)})
                else:
                    errors.append(err)
                    self._log("llm", f"{item_id}: {err}")
                if len(errors) >= 3 and not results:
                    for f in futures:
                        f.cancel()
                    break
        self.state["llm"] = results
        if not results:
            return {"_status": "error" if errors else "skipped",
                    "_message": errors[0] if errors else "No LLM results.", "results": {}}
        return {"model": self.llm_label, "reviewed": len(results), "failed": len(errors),
                "results": results}

    def stage_report(self) -> dict:
        items, llm = self.state["items"], self.state.get("llm", {})
        reviewed = sorted((it for it in items if it["id"] in llm),
                          key=lambda it: (-llm[it["id"]]["priority_score"], it["context_rank"]))
        rest = sorted((it for it in items if it["id"] not in llm), key=lambda it: it["context_rank"])
        final = []
        for rank, it in enumerate(reviewed + rest, 1):
            r = llm.get(it["id"])
            final.append({"id": it["id"], "rank": rank, "source": "llm" if r else "context",
                          "priority": r["priority"] if r else None,
                          "priority_score": r["priority_score"] if r else None,
                          "reason": r["reason"] if r else "", "action": r["action"] if r else ""})
        top = final[0]
        overlap = len({x["id"] for x in final[:10]} & {it["id"] for it in items[:10]})
        summary = {"repo": f"{self.owner}/{self.name}", "candidates": len(items),
                   "llm_reviewed": len(llm), "top": top["id"], "agreement_top10": overlap}
        self.state["summary"] = summary
        return {"final": final, "summary": summary}


def shallow_file_context(repo: str, now: datetime, lookback_days: int) -> dict[str, dict]:
    """
    File-level history that works on shallow clones (GitPython's stats need the
    missing parents). Commits at the shallow boundary are skipped because their
    diff would list every file as newly added.
    """
    boundary = set()
    shallow_file = os.path.join(repo, ".git", "shallow")
    if os.path.isfile(shallow_file):
        with open(shallow_file, encoding="utf-8") as fh:
            boundary = {ln.strip() for ln in fh if ln.strip()}
    since = (now - timedelta(days=lookback_days)).isoformat()
    out = _git(repo, "log", "--no-merges", f"--since={since}", "--numstat",
               "--format=%x1e%H%x1f%ae%x1f%ct%x1f%B%x1f", timeout=300)
    stats: dict[str, dict] = {}
    for record in out.split(""):
        if "" not in record:
            continue
        sha, author, ts, message, files = record.split("", 4)
        if sha in boundary:
            continue
        days = (now - datetime.fromtimestamp(int(ts), tz=timezone.utc)).days
        fix = is_defect_fix(message)
        for line in files.strip().splitlines():
            parts = line.split("	")
            if len(parts) != 3 or not parts[2].endswith(".py"):
                continue
            entry = stats.setdefault(parts[2], {"recent_commits": 0, "defect_commits": 0,
                                                "authors": set(), "last_modified_days": days})
            entry["recent_commits"] += 1
            entry["defect_commits"] += int(fix)
            entry["authors"].add(author)
            entry["last_modified_days"] = min(entry["last_modified_days"], days)
    return {path: {"recent_commits": e["recent_commits"], "defect_commits": e["defect_commits"],
                   "commit_authors": len(e["authors"]), "last_modified_days": e["last_modified_days"]}
            for path, e in stats.items()}


_OVERLOAD_RE = re.compile(r"^\s*@(?:[\w.]+\.)?overload\b", re.MULTILINE)


def _is_overload_stub(source: str) -> bool:
    """True for ``@typing.overload`` declarations (decorator lines precede ``def``)."""
    head = source.split("def ", 1)[0]
    return bool(_OVERLOAD_RE.search(head))


def _read_source(item: dict) -> str:
    try:
        with open(item["abs_file"], encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    body = lines[item["start_line"] - 1: item["end_line"]]
    if len(body) > SOURCE_PREVIEW_LINES:
        body = body[:SOURCE_PREVIEW_LINES] + [f"# … {len(body) - SOURCE_PREVIEW_LINES} more line(s)"]
    return "\n".join(body)


_SLIM_FIELDS = ("id", "file", "function", "line", "start_line", "complexity", "complexity_rank",
                "loc", "params", "maintainability_index", "code_smells", "tool_messages",
                "duplicate_lines", "dead_code", "static_score", "static_rank", "callers",
                "test_references", "is_private", "func_commits", "func_fix_commits",
                "recent_commits", "defect_commits", "commit_authors", "context_score",
                "context_rank", "docstring", "source")


def _slim(item: dict) -> dict:
    return {k: item.get(k) for k in _SLIM_FIELDS}
