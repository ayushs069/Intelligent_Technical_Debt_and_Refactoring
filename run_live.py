#!/usr/bin/env python3
"""
run_live.py — start the live analysis dashboard.

Paste any public Python repository URL and watch the full pipeline run:
clone → static analysis → function index → repository context → debt
candidates → RAG retrieval → LLM review → fix-first report.

Usage:
    python run_live.py                 # http://127.0.0.1:8600
    python run_live.py --port 9000 --no-browser

The research dashboard for the evaluated dataset is served at /research.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from live.server import serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Live technical-debt analysis dashboard.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default 127.0.0.1; do not expose publicly).")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    httpd = serve(args.host, args.port)
    url = f"http://{args.host}:{args.port}/"
    print(f"[live] Technical debt live dashboard on {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[live] Stopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
