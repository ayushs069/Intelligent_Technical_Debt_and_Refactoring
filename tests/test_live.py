"""Tests for the live analysis pipeline and server (no network, mock LLM)."""

import json
import os
import subprocess
import textwrap
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from live.pipeline import (STAGES, LivePipeline, PipelineError, _is_overload_stub,
                           detect_source_dir, parse_repo_url, shallow_file_context)
from llm.client import MockClient

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def git(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *GIT_ID, *args], check=True,
                   capture_output=True, text=True, env=env)


def commit(repo, message, when):
    env = dict(os.environ, GIT_AUTHOR_DATE=when.isoformat(), GIT_COMMITTER_DATE=when.isoformat())
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-q", "-m", message, env=env)


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/psf/requests", ("https://github.com/psf/requests.git", "psf", "requests")),
    ("https://github.com/psf/requests.git", ("https://github.com/psf/requests.git", "psf", "requests")),
    ("pallets/click", ("https://github.com/pallets/click.git", "pallets", "click")),
    ("https://gitlab.com/group/project/", ("https://gitlab.com/group/project.git", "group", "project")),
])
def test_parse_repo_url_accepts(url, expected):
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize("url", [
    "", "file:///etc/passwd", "http://github.com/a/b", "https://evil.example/a/b",
    "https://github.com/a/b;rm -rf /", "--upload-pack=touch /tmp/x", "https://github.com/a",
    "git@github.com:a/b.git", "https://github.com/a/b/../../c",
])
def test_parse_repo_url_rejects(url):
    with pytest.raises(PipelineError):
        parse_repo_url(url)


def test_detect_source_dir_prefers_largest_package(tmp_path):
    (tmp_path / "src" / "big").mkdir(parents=True)
    (tmp_path / "src" / "big" / "__init__.py").write_text("x = 1\n" * 50, encoding="utf-8")
    (tmp_path / "small").mkdir()
    (tmp_path / "small" / "__init__.py").write_text("y = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("z = 1\n" * 500, encoding="utf-8")
    assert detect_source_dir(str(tmp_path)) == str(tmp_path / "src" / "big")


def test_overload_stub_detection():
    assert _is_overload_stub("    @t.overload\n    def f(self) -> int: ...")
    assert _is_overload_stub("@typing.overload\ndef f(): ...")
    assert not _is_overload_stub("@property\ndef f(self):\n    return 1")
    assert not _is_overload_stub("def f():\n    # @overload mentioned in a comment\n    return 1")


BRANCHY = textwrap.dedent('''\
    def classify(a, b, c, d):
        """Classify four flags."""
        if a and b:
            return 1
        elif a and c:
            return 2
        elif b and d:
            return 3
        elif c or d:
            if a:
                return 4
            return 5
        return 0


    def helper():
        return classify(1, 0, 1, 0)
''')


@pytest.fixture
def local_repo(tmp_path):
    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    git(tmp_path, "init", "-q", str(repo))
    now = datetime.now(tz=timezone.utc)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "core.py").write_text(BRANCHY, encoding="utf-8")
    (repo / "tests" / "test_core.py").write_text("from pkg.core import classify\n\n\ndef test_x():\n    assert classify(1, 1, 0, 0) == 1\n", encoding="utf-8")
    commit(repo, "initial", now - timedelta(days=100))
    (repo / "pkg" / "core.py").write_text(BRANCHY.replace("return 5", "return 50"), encoding="utf-8")
    commit(repo, "Fix wrong value in classify", now - timedelta(days=20))
    return repo


def test_shallow_file_context_counts_fixes(local_repo):
    ctx = shallow_file_context(str(local_repo), datetime.now(tz=timezone.utc), 365)
    assert ctx["pkg/core.py"]["recent_commits"] == 2
    assert ctx["pkg/core.py"]["defect_commits"] == 1


def test_live_pipeline_end_to_end(local_repo, tmp_path, monkeypatch):
    # RAG is covered by test_rag.py; stub it here to keep this test fast.
    monkeypatch.setattr(LivePipeline, "stage_rag", lambda self: {
        "indexed": len(self.state["items"]), "queries": 0, "embedding_model": "stub",
        "store": "stub", "neighbours": {}})
    events = []

    def responder(system, prompt, schema):
        return {"priority": "HIGH", "priority_score": 88, "reason": "complex and recently fixed",
                "action": "split the branches into a lookup table"}

    pipeline = LivePipeline("", str(tmp_path / "work"), events.append,
                            clone_url=str(local_repo), shortlist=5,
                            llm_factory=lambda: MockClient(responder), llm_label="mock")
    summary = pipeline.run()

    stage_events = [e for e in events if e["type"] == "stage" and e["status"] != "running"]
    assert [e["stage"] for e in stage_events] == [k for k, _ in STAGES]
    assert all(e["status"] == "done" for e in stage_events)
    by_stage = {e["stage"]: e["data"] for e in stage_events}
    assert by_stage["clone"]["source_dir"] == "pkg"
    assert by_stage["context"]["fix_commits_mapped"] == 1
    item_ids = [x["id"] for x in by_stage["items"]["items"]]
    assert "pkg/core.py::classify" in item_ids
    assert any(e["type"] == "llm_item" for e in events)
    final = by_stage["report"]["final"]
    assert final[0]["id"] == "pkg/core.py::classify" and final[0]["priority"] == "HIGH"
    assert summary["llm_reviewed"] >= 1
    json.dumps(events)  # everything streamed must be JSON-serialisable


def test_live_pipeline_llm_unavailable_is_skipped(local_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(LivePipeline, "stage_rag", lambda self: {"neighbours": {}})

    def no_key():
        raise SystemExit("no key configured")

    events = []
    LivePipeline("", str(tmp_path / "w"), events.append, clone_url=str(local_repo),
                 llm_factory=no_key, llm_label="x").run()
    llm = next(e for e in events if e["type"] == "stage" and e["stage"] == "llm" and e["status"] != "running")
    assert llm["status"] == "skipped" and "no key" in llm["message"]


def test_server_api_validation():
    from live.server import serve

    httpd = serve("127.0.0.1", 0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=10) as r:
            cfg = json.loads(r.read())
        assert "providers" in cfg and "busy" in cfg
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/analyze", method="POST",
                                     data=json.dumps({"url": "file:///etc/passwd"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=10)
        assert err.value.code == 400
        assert "repository URL" in json.loads(err.value.read())["error"]
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/000000000000", timeout=10)
        assert err.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
