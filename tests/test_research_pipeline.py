"""
Tests for the research pipeline: function history, dataset, agents (mock LLM),
evaluation metrics, expert rankings and the Phase 6 refactoring CI gate.

No network or API key is needed: the LLM is replaced by MockClient and git
history is created in temporary repositories.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from agents.agents import run_chain, run_single
from agents.prioritise import prioritise, rank_results, static_rankings
from agents.prompts import render_item
from dataset.duplication import find_duplicate_lines
from dataset.function_index import function_coverage, index_functions
from enricher.function_history import (build_function_history, canonical_path,
                                       function_spans, innermost_function, is_defect_fix,
                                       parse_changed_lines)
from evaluation.evaluate import (collect_methods, context_heuristic_scores,
                                 evaluate_against_defects, evaluate_against_experts,
                                 paired_difference)
from evaluation.expert import consensus, load_raters, make_expert_sample, save_rater
from evaluation.metrics import (kendalls_w, ndcg_at_k, precision_at_k, roc_auc, spearman,
                                topk_overlap)
from llm.client import MockClient, _DiskCache
from refactoring.code_utils import (PatchError, block_complexity, function_complexity,
                                    replace_function)

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], check=True,
                          capture_output=True, text=True, env=env).stdout


def commit(repo, message, when):
    env = dict(os.environ, GIT_AUTHOR_DATE=when.isoformat(), GIT_COMMITTER_DATE=when.isoformat())
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", message, env=env)


# ── function history ─────────────────────────────────────────────────────────

def test_is_defect_fix_heuristic():
    assert is_defect_fix("Fix crash when url is empty")
    assert is_defect_fix("Handle None\n\nresolves #123")
    assert not is_defect_fix("Fix typo in docstring")
    assert not is_defect_fix("Fix CI workflow")
    assert not is_defect_fix("Add new feature")


def test_canonical_path_strips_src():
    assert canonical_path("src/pkg/mod.py") == "pkg/mod.py"
    assert canonical_path("pkg\\mod.py") == "pkg/mod.py"


def test_spans_and_innermost():
    src = textwrap.dedent("""
        class A:
            @staticmethod
            def m(x):
                def inner():
                    return x
                return inner()

        def f():
            return 1
    """)
    spans = function_spans(src)
    names = {q for q, _, _ in spans}
    assert names == {"A.m", "A.m.inner", "f"}
    assert innermost_function(spans, 6) == "A.m.inner"
    assert innermost_function(spans, 3) == "A.m"  # decorator line belongs to the method
    assert innermost_function(spans, 1) is None


def test_parse_changed_lines():
    diff = textwrap.dedent("""\
        diff --git a/src/p.py b/src/p.py
        --- a/src/p.py
        +++ b/src/p.py
        @@ -3,0 +4,2 @@ def a():
        +x
        +y
        @@ -10 +12,0 @@
        -gone
    """)
    assert parse_changed_lines(diff) == {"src/p.py": {4, 5, 12}}


@pytest.fixture
def history_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    git(tmp_path, "init", "-q", str(repo))
    mod = repo / "src" / "pkg" / "mod.py"
    t0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    mod.write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n", encoding="utf-8")
    commit(repo, "initial", t0)
    mod.write_text("def a():\n    return 10\n\n\ndef b():\n    return 2\n", encoding="utf-8")
    commit(repo, "Fix wrong value in a", t0 + timedelta(days=10))
    mod.write_text("def a():\n    return 10\n\n\ndef b():\n    return 20\n", encoding="utf-8")
    commit(repo, "Refactor b", t0 + timedelta(days=40))
    return repo, t0


def test_build_function_history_windows(history_repo):
    repo, t0 = history_repo
    early = build_function_history(str(repo), t0 + timedelta(days=1), t0 + timedelta(days=20))
    assert early == {"pkg/mod.py::a": {"commits": 1, "fix_commits": 1,
                                       "fix_shas": early["pkg/mod.py::a"]["fix_shas"]}}
    late = build_function_history(str(repo), t0 + timedelta(days=20), t0 + timedelta(days=60))
    assert late["pkg/mod.py::b"]["commits"] == 1
    assert late["pkg/mod.py::b"]["fix_commits"] == 0
    assert "pkg/mod.py::a" not in late


def test_sweeping_commits_are_skipped(history_repo):
    repo, t0 = history_repo
    hist = build_function_history(str(repo), t0 + timedelta(days=1), t0 + timedelta(days=60),
                                  max_functions_per_commit=0)
    assert hist == {}


# ── dataset ──────────────────────────────────────────────────────────────────

def test_index_functions_and_coverage(sample_repo):
    records = index_functions(str(sample_repo), str(sample_repo))
    by_name = {r["function"]: r for r in records}
    assert by_name["very_complex_function"]["complexity"] >= 10
    assert by_name["UnusedClass.method"]["class_name"] == "UnusedClass"
    assert by_name["UnusedClass.method"]["params"] == 0
    assert function_coverage({"executed_lines": [2, 3], "missing_lines": [4, 9]}, 1, 5) == 66.7
    assert function_coverage({}, 1, 5) is None


def test_duplicate_detection():
    block = "\n".join(f"value_{i} = compute_something(argument_{i}, other_{i})" for i in range(8))
    found = find_duplicate_lines({"a.py": block, "b.py": "x = 1\n" + block, "c.py": "y = 2"})
    assert len(found["a.py"]) == 8 and len(found["b.py"]) == 8
    assert "c.py" not in found


# ── agents with a mock LLM ───────────────────────────────────────────────────

def make_item(**overrides):
    item = {
        "id": "pkg/mod.py::f", "file": "pkg/mod.py", "function": "f", "line": 1,
        "start_line": 1, "end_line": 2, "abs_file": "", "complexity": 12,
        "complexity_rank": "C", "loc": 40, "params": 2, "maintainability_index": 50.0,
        "code_smells": 2, "smell_types": ["too-many-branches"], "tool_messages": ["[pylint] x"],
        "style_issues": 1, "duplicate_lines": 0, "dead_code": False, "static_score": 5.0,
        "callers": 3, "test_references": 1, "is_private": False, "func_commits": 4,
        "func_fix_commits": 2, "recent_commits": 9, "defect_commits": 3, "commit_authors": 2,
        "last_modified_days": 30, "function_coverage": 55.0, "test_coverage": 70.0,
        "docstring": "Does f.", "static_rank": 1,
    }
    item.update(overrides)
    return item


def mock_responder(system, prompt, schema):
    props = schema["properties"]
    if "summary" in props:
        return {"summary": "complex", "debt_indicators": ["nesting"],
                "impact_factors": ["called often"], "risk_assessment": "medium"}
    if "action" in props and "refactoring_type" in props:
        return {"action": "extract helper", "refactoring_type": "extract_method",
                "expected_benefit": "lower cc", "risk": "low"}
    if "action" in props:
        return {"priority": "HIGH", "priority_score": 150, "reason": "r", "action": "a"}
    score = 90 if "Function-level history" in prompt and "bug fixes" in prompt else 40
    return {"priority": "HIGH" if score > 70 else "MEDIUM", "priority_score": score,
            "reason": "because"}


def test_prompt_context_toggle_and_no_leakage():
    item = make_item(future_fix_commits=99)
    with_ctx = render_item(item, "def f(): pass", with_context=True, rag_context="NEIGHBOURS")
    without = render_item(item, "def f(): pass", with_context=False, rag_context="NEIGHBOURS")
    assert "Repository context" in with_ctx and "NEIGHBOURS" in with_ctx
    assert "Repository context" not in without and "NEIGHBOURS" not in without
    assert "def f(): pass" in without
    assert "99" not in with_ctx  # ground-truth fields are never rendered


def test_run_chain_passes_outputs_forward():
    llm = MockClient(mock_responder)
    out = run_chain(llm, render_item(make_item(), "def f(): pass"))
    assert len(llm.calls) == 3
    assert "Analysis from the Code Analysis Expert" in llm.calls[1][1]
    assert "Prioritisation decision" in llm.calls[2][1]
    assert out["priority_score"] == 90 and out["refactoring_type"] == "extract_method"


def test_run_single_clamps_score():
    out = run_single(MockClient(mock_responder), "ctx")
    assert out["priority_score"] == 100


def test_prioritise_ranks_and_isolates_errors():
    items = [make_item(id=f"m.py::f{i}", function=f"f{i}") for i in range(3)]
    contexts = {it["id"]: ("Function-level history ... bug fixes" if it["id"].endswith("1")
                           else "plain") for it in items}

    results = prioritise(items, MockClient(mock_responder), contexts, mode="chain", workers=2)
    assert [r["llm_rank"] for r in results] == [1, 2, 3]
    assert results[0]["id"] == "m.py::f1"
    ranked = rank_results([{"id": "a", "error": "x"}, {"id": "b", "priority": "LOW",
                                                       "priority_score": 5}])
    assert ranked[0]["id"] == "b" and "llm_rank" not in ranked[1]


def test_static_rankings_has_both_baselines():
    items = [make_item(id="a", static_rank=2, complexity=30),
             make_item(id="b", static_rank=1, complexity=5)]
    ranks = static_rankings(items)
    assert [r["id"] for r in ranks] == ["b", "a"]
    assert {r["id"]: r["complexity_rank"] for r in ranks} == {"a": 1, "b": 2}


def test_disk_cache_roundtrip(tmp_path):
    cache = _DiskCache(str(tmp_path))
    key = cache.key("model", "sys", "prompt", {"s": 1})
    assert cache.get(key) is None
    cache.put(key, {"ok": True})
    assert cache.get(key) == {"ok": True}


# ── evaluation ───────────────────────────────────────────────────────────────

def test_metrics_basics():
    a = {"x": 3, "y": 2, "z": 1}
    assert spearman(a, {"x": 30, "y": 20, "z": 10}) == 1.0
    assert spearman(a, {"x": 1, "y": 1, "z": 1}) is None
    assert precision_at_k(["x", "y", "z"], {"x", "z"}, 2) == 0.5
    assert topk_overlap(["x", "y", "z"], ["y", "x", "z"], 2) == 1.0
    assert ndcg_at_k(["x", "y"], {"x": 1.0, "y": 0.0}, 2) == 1.0
    assert roc_auc({"x": 3, "y": 2, "z": 1}, {"x": True, "y": False, "z": False}) == 1.0
    assert kendalls_w([a, dict(a)]) == 1.0


def test_evaluation_end_to_end():
    items = [make_item(id=f"i{n}", static_score=float(n), static_rank=10 - n, complexity=n,
                       callers=n % 3, func_fix_commits=n % 2) for n in range(10)]
    llm = [{"id": f"i{n}", "priority": "HIGH", "priority_score": 100 - n, "llm_rank": n + 1}
           for n in range(10)]
    methods = collect_methods(items, llm, None)
    assert set(methods) == {"static_score", "complexity", "context_heuristic", "llm"}
    assert len(context_heuristic_scores(items)) == 10
    gt = {f"i{n}": {"future_fix_commits": int(n < 3), "future_commits": 10 - n,
                    "future_defect": n < 3} for n in range(10)}
    d = evaluate_against_defects(methods, gt, ks=(3,))
    assert d["methods"]["llm"]["precision@3"] == 1.0
    assert d["methods"]["static_score"]["precision@3"] == 0.0
    assert d["methods"]["llm"]["spearman_vs_change_commits"] == 1.0
    experts = {f"i{n}": 10 - n for n in range(10)}
    e = evaluate_against_experts(methods, experts)
    assert e["methods"]["llm"]["spearman"] == 1.0
    diff = paired_difference(methods["llm"], methods["static_score"], experts)
    assert diff["delta_spearman"] == 2.0 and diff["significant"]


def test_expert_sample_and_consensus(tmp_path):
    items = [make_item(id=f"i{n}", static_score=float(n)) for n in range(30)]
    sample = make_expert_sample(items, 9, seed=1)
    assert len(sample) == len(set(sample)) == 9
    tiers = [sum(1 for s in sample if int(s[1:]) >= 20), sum(1 for s in sample if int(s[1:]) < 10)]
    assert tiers == [3, 3]  # equal share from top and bottom tertile
    save_rater(str(tmp_path), "Ann Lee", {"i1": 8, "i2": 2})
    save_rater(str(tmp_path), "bob", {"i1": 6, "i2": 4})
    raters = load_raters(str(tmp_path))
    assert consensus(raters) == {"i1": 7.0, "i2": 3.0}
    assert os.path.isfile(tmp_path / "Ann_Lee.json")


# ── refactoring ──────────────────────────────────────────────────────────────

BRANCHY = textwrap.dedent('''\
    class Grader:
        def grade(self, score):
            """Map a score to a letter."""
            if score >= 90:
                return "A"
            elif score >= 80:
                return "B"
            elif score >= 70:
                return "C"
            elif score >= 60:
                return "D"
            else:
                return "F"
''')

SIMPLER = textwrap.dedent('''\
    def grade(self, score):
        """Map a score to a letter."""
        for bound, letter in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
            if score >= bound:
                return letter
        return "F"
''')


def test_replace_function_and_complexity():
    before = function_complexity(BRANCHY, "Grader.grade")
    patched, start, end = replace_function(BRANCHY, "Grader.grade", f"```python\n{SIMPLER}```")
    assert "    def grade(self, score):" in patched  # re-indented into the class
    assert function_complexity(patched, "Grader.grade") < before
    assert block_complexity(patched, start, end) == function_complexity(patched, "Grader.grade")
    with pytest.raises(PatchError):
        replace_function(BRANCHY, "Grader.grade", "def grade(self:\n  pass")
    with pytest.raises(PatchError):
        replace_function(BRANCHY, "Grader.grade", "def renamed(self):\n    return 1")


def test_refactor_item_end_to_end(tmp_path):
    from refactoring.refactor import refactor_item, select_items, summarise
    from refactoring.validate import run_tests

    repo = tmp_path / "proj"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    git(tmp_path, "init", "-q", str(repo))
    (repo / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "pkg" / "grades.py").write_text(BRANCHY, encoding="utf-8")
    (repo / "tests" / "test_grades.py").write_text(textwrap.dedent("""
        from pkg.grades import Grader

        def test_grades():
            g = Grader()
            assert [g.grade(s) for s in (95, 85, 75, 65, 10)] == list("ABCDF")
    """), encoding="utf-8")
    commit(repo, "initial", datetime(2023, 1, 1, tzinfo=timezone.utc))
    sha = git(repo, "rev-parse", "HEAD").strip()

    items = index_functions(str(repo), str(repo / "src"))
    for it in items:
        it.update(make_item(**{k: it[k] for k in ("id", "file", "function", "line",
                                                  "start_line", "end_line", "abs_file",
                                                  "complexity", "loc")}),
                  rel_file="src/pkg/grades.py")
    rankings = [{"id": items[0]["id"], "priority": "HIGH", "priority_score": 90, "llm_rank": 1,
                 "reason": "r", "action": "table-driven", "refactoring_type": "simplify_logic"}]
    selected = select_items(items, rankings, top_n=5)
    assert len(selected) == 1

    baseline = run_tests(str(repo), sys.executable, "src", "tests")
    assert baseline["passed"] == 1 and baseline["failed"] == 0

    answers = iter([{"new_code": "def grade(self, score):\n    return 'A'\n",
                     "explanation": "too simple, breaks tests"},
                    {"new_code": SIMPLER, "explanation": "table driven"}])
    llm = MockClient(lambda s, p, schema: next(answers))
    result = refactor_item(
        selected[0], llm, git_repo=str(repo), snapshot_sha=sha,
        worktree_root=str(tmp_path / "branches"), output_root=str(tmp_path / "out"),
        test_python=sys.executable, src_root="src", source_subdir="src", tests_subdir="tests",
        baseline=baseline, branch_prefix="ai-refactoring", max_attempts=2)

    assert result["success"] is True
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["new_failures"]  # first attempt broke a test
    assert "Tests that passed before now fail" in llm.calls[1][1]  # feedback was given
    assert result["complexity_reduction_target"] > 0
    assert result["branch"] == "ai-refactoring/grader-grade"
    assert "ai-refactoring/grader-grade" in git(repo, "branch", "--list")
    assert os.path.isfile(tmp_path / "out" / "grader-grade" / "patch.diff")
    summary = summarise([result])
    assert summary["success_rate"] == 1.0 and summary["test_pass_rate"] == 1.0
    # the original checkout is untouched
    assert (repo / "src" / "pkg" / "grades.py").read_text(encoding="utf-8") == BRANCHY
    json.dumps(result)  # serialisable
