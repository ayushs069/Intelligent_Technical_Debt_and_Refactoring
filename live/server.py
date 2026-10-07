"""
Live analysis server — paste a repository URL, watch the pipeline run.

Standard library only (ThreadingHTTPServer + Server-Sent Events). Binds to
127.0.0.1 by default; it clones repositories and runs analysis tools, so it is
not meant to be exposed to a network.

Routes:
  GET  /                      live analysis page
  GET  /research              research dashboard (reports/<repo>_dashboard.html)
  GET  /api/config            available LLM providers and limits
  POST /api/analyze           {"url", "provider", "shortlist", "history_years"} -> {"job"}
  GET  /api/jobs/<id>         full job state (events so far)
  GET  /api/jobs/<id>/events  Server-Sent Events stream (replays, then live)
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from live.pipeline import STAGES, LivePipeline, PipelineError, parse_repo_url

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
WORKDIR = os.path.join(config.PROJECT_DIR, "targets", "live")


def available_providers() -> list[dict]:
    """LLM options whose credentials are present (presence only, not balance)."""
    options = []
    if os.environ.get("GROQ_API_KEY"):
        options.append({"id": "groq", "label": "Groq · gpt-oss-20b",
                        "model": os.environ.get("TECHDEBT_GROQ_MODEL", "groq/openai/gpt-oss-20b")})
    if os.environ.get("OPENAI_API_KEY"):
        options.append({"id": "openai", "label": "OpenAI · gpt-4.1-mini",
                        "model": os.environ.get("TECHDEBT_OPENAI_MODEL", "gpt-4.1-mini")})
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        options.append({"id": "anthropic", "label": "Anthropic · Claude",
                        "model": os.environ.get("TECHDEBT_ANTHROPIC_MODEL", "claude-opus-5-5")})
    return options


class Job:
    def __init__(self, url: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.url = url
        self.events: list[dict] = []
        self.finished = False
        self.cond = threading.Condition()
        self.started = time.time()

    def emit(self, event: dict) -> None:
        with self.cond:
            event = {**event, "t": round(time.time() - self.started, 1), "seq": len(self.events)}
            self.events.append(event)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.finished = True
            self.cond.notify_all()


class Registry:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.busy = threading.Lock()

    def start(self, params: dict) -> Job:
        url = str(params.get("url", ""))
        parse_repo_url(url)  # raises PipelineError with a user-facing message
        if not self.busy.acquire(blocking=False):
            raise PipelineError("Another analysis is running. Wait for it to finish, then try again.")
        job = Job(url)
        self.jobs[job.id] = job
        provider = next((p for p in available_providers() if p["id"] == params.get("provider")), None)

        def llm_factory():
            from llm.client import make_client
            return make_client(provider["model"], config.LLM_EFFORT, config.LLM_CACHE_DIR)

        def run() -> None:
            try:
                job.emit({"type": "start", "url": url, "stages": STAGES,
                          "llm": provider["label"] if provider else ""})
                pipeline = LivePipeline(
                    url, WORKDIR, job.emit,
                    history_years=int(params.get("history_years", 3) or 3),
                    llm_factory=llm_factory if provider else None,
                    llm_label=provider["label"] if provider else "",
                    shortlist=int(params.get("shortlist", 10) or 10))
                summary = pipeline.run()
                job.emit({"type": "done", "summary": summary})
            except PipelineError as exc:
                job.emit({"type": "failed", "message": str(exc)})
            except Exception as exc:  # unexpected: surface briefly, keep server alive
                traceback.print_exc()
                job.emit({"type": "failed", "message": f"Unexpected error: {type(exc).__name__}: {exc}"})
            finally:
                job.finish()
                self.busy.release()

        threading.Thread(target=run, daemon=True).start()
        return job


REGISTRY = Registry()


class Handler(BaseHTTPRequestHandler):
    server_version = "TechDebtLive/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if "/events" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _file(self, path: str, ctype: str) -> None:
        if not os.path.isfile(path):
            self._send(404, b"Not found. Build it first (python run_dashboard.py).", "text/plain")
            return
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if path == "/research":
            return self._file(os.path.join(config.REPORTS_DIR, f"{config.DEFAULT_REPO_NAME}_dashboard.html"),
                              "text/html; charset=utf-8")
        if path == "/api/config":
            return self._json(200, {"providers": [{k: p[k] for k in ("id", "label")} for p in available_providers()],
                                    "research": os.path.isfile(os.path.join(
                                        config.REPORTS_DIR, f"{config.DEFAULT_REPO_NAME}_dashboard.html")),
                                    "busy": REGISTRY.busy.locked()})
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})(/events)?", path)
        if m:
            job = REGISTRY.jobs.get(m.group(1))
            if not job:
                return self._json(404, {"error": "Unknown job"})
            if not m.group(2):
                return self._json(200, {"id": job.id, "finished": job.finished, "events": job.events})
            return self._stream(job)
        self._send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/analyze":
            return self._send(404, b"Not found", "text/plain")
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 10_000:
            return self._json(413, {"error": "Request too large"})
        try:
            params = json.loads(self.rfile.read(length) or b"{}")
            job = REGISTRY.start(params if isinstance(params, dict) else {})
        except PipelineError as exc:
            return self._json(400, {"error": str(exc)})
        except (ValueError, TypeError):
            return self._json(400, {"error": "Invalid request"})
        self._json(202, {"job": job.id})

    def _stream(self, job: Job) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sent = 0
        try:
            while True:
                with job.cond:
                    if sent >= len(job.events) and not job.finished:
                        job.cond.wait(timeout=15)
                    batch = job.events[sent:]
                    finished = job.finished
                for ev in batch:
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode("utf-8"))
                sent += len(batch)
                if not batch:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                if finished and sent >= len(job.events):
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def serve(host: str = "127.0.0.1", port: int = 8600) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
