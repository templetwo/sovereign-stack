"""
Sovereign Stack web dashboard.

A small stdlib http.server that serves:
  GET /                  — single-page dashboard HTML (dark, real-time)
  GET /snapshot.json     — current DashboardState as JSON (poll-friendly)
  GET /events            — SSE stream pushing snapshot updates
  GET /static/<file>     — bundled static assets

No third-party dependencies. The page polls /snapshot.json every 3s by
default; an /events SSE channel exists for clients that want push updates.

Default port: 3435 (next to the MCP-SSE server on 3434).

Design notes (frontend):
  * Minimal dark theme — GitHub-style (#0d1117 / #161b22 / mono accent).
  * Inter font stack with system fallback so the page renders before
    web fonts arrive.
  * Pill-shaped status badges with semantic colors (green/amber/red/gray).
  * Live activity feed renders as a scrollable column, newest on top,
    category-coded by a left border accent.
  * Layout collapses to single-column under 800px wide (phone-friendly).
  * No JS framework — vanilla DOM updates. Keeps the page <50KB total.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import dashboard


# ── Shared activity feed + background watcher ──────────────────────────────
#
# A single process-wide ActivityFeed populated by a daemon thread that
# watches the same filesystem signals the TUI dashboard does. The /snapshot
# endpoint includes feed.to_list(limit=N) so browser polling sees a live
# feed without needing SSE plumbing on the client.

_GLOBAL_FEED = dashboard.ActivityFeed(maxlen=200)
_FEED_LIMIT_IN_SNAPSHOT = 30
_WATCHER_INTERVAL = 2.0  # seconds — faster than client poll, so events
                          # land in the next snapshot poll
_watcher_started = False
_watcher_lock = threading.Lock()


def _watcher_loop() -> None:
    """Background watcher — populates _GLOBAL_FEED. Daemon thread, runs
    until process exit. Mirrors dashboard.run_loop's filesystem watchers."""
    root = Path(os.environ.get(
        "SOVEREIGN_ROOT", Path.home() / ".sovereign",
    ))
    chronicle_index = dashboard._MtimeIndex()
    halts_index = dashboard._MtimeIndex()
    decisions_index = dashboard._MtimeIndex()
    honks_index = dashboard._MtimeIndex()

    # Seed indices so the first iteration doesn't dump everything as "new".
    chronicle_index.diff(dashboard._list_paths(
        root / "chronicle" / "insights", "*.jsonl", recursive=True,
    ))
    chronicle_index.diff(dashboard._list_paths(
        root / "chronicle" / "open_threads", "*.jsonl", recursive=True,
    ))
    halts_index.diff(dashboard._list_paths(
        root / "daemons" / "halts", "*.md",
    ))
    decisions_index.diff(dashboard._list_paths(
        root / "decisions", "metabolize_*.md",
    ))
    honks_index.diff([root / "nape" / "honks.jsonl"])

    _GLOBAL_FEED.add(
        dashboard.CAT_STARTUP,
        "watcher started — seeding from filesystem state",
    )

    while True:
        try:
            for jsonl in chronicle_index.diff(dashboard._list_paths(
                root / "chronicle" / "insights",
                "*.jsonl", recursive=True,
            )):
                tail = dashboard.read_chronicle_tail(jsonl)
                if tail:
                    layer = tail.get("layer", "?")
                    content = (tail.get("content") or "")[:80]
                    _GLOBAL_FEED.add(
                        dashboard.CAT_INSIGHT,
                        f"[{layer}] {content}…",
                    )

            for jsonl in chronicle_index.diff(dashboard._list_paths(
                root / "chronicle" / "open_threads",
                "*.jsonl", recursive=True,
            )):
                tail = dashboard.read_chronicle_tail(jsonl)
                if tail:
                    q = (tail.get("question") or "")[:80]
                    _GLOBAL_FEED.add(dashboard.CAT_THREAD, q)

            for halt in halts_index.diff(dashboard._list_paths(
                root / "daemons" / "halts", "*.md",
            )):
                _GLOBAL_FEED.add(
                    dashboard.CAT_HALT, f"halt note: {halt.name}",
                )

            for dec in decisions_index.diff(dashboard._list_paths(
                root / "decisions", "metabolize_*.md",
            )):
                _GLOBAL_FEED.add(
                    dashboard.CAT_DECISION,
                    f"new metabolize digest: {dec.name}",
                )

            if honks_index.diff([root / "nape" / "honks.jsonl"]):
                recent = dashboard.read_recent_honks(
                    root / "nape" / "honks.jsonl", limit=3,
                )
                for h in recent:
                    _GLOBAL_FEED.add(
                        dashboard.CAT_HONK,
                        f"[{h.get('level','?')}] {h.get('pattern','?')}: "
                        f"{h.get('trigger_tool','?')}",
                    )

            time.sleep(_WATCHER_INTERVAL)
        except Exception as e:
            _GLOBAL_FEED.add(
                dashboard.CAT_ERROR,
                f"watcher: {type(e).__name__}: {e}",
            )
            time.sleep(_WATCHER_INTERVAL)


def _ensure_watcher() -> None:
    """Start the watcher thread once per process (idempotent)."""
    global _watcher_started
    with _watcher_lock:
        if _watcher_started:
            return
        t = threading.Thread(target=_watcher_loop, daemon=True,
                             name="sovereign-dashboard-watcher")
        t.start()
        _watcher_started = True


# ── Static directory ────────────────────────────────────────────────────────


STATIC_DIR = Path(__file__).parent / "dashboard_web_static"


def _read_static(name: str) -> Optional[bytes]:
    p = STATIC_DIR / name
    if not p.exists() or not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


# ── Snapshot builder ────────────────────────────────────────────────────────


def build_snapshot() -> dict:
    """Build a DashboardState snapshot as a serializable dict.
    Pulls the live activity feed from the shared _GLOBAL_FEED that the
    watcher thread populates."""
    state = dashboard.collect_state(_GLOBAL_FEED)
    return {
        "timestamp": state.timestamp,
        "connectivity": state.connectivity_summary,
        "halts_count": state.halts_count,
        "decisions_count": state.decisions_count,
        "unacked_honks": state.unacked_honks,
        "listener_stale": state.listener_stale,
        "latest": state.latest,
        "feed": _GLOBAL_FEED.to_list(limit=_FEED_LIMIT_IN_SNAPSHOT),
    }


# ── Request handler ─────────────────────────────────────────────────────────


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard page + snapshot endpoint + SSE feed."""

    # Quiet the default per-request stderr logging — too noisy when the
    # browser polls every 3 seconds.
    def log_message(self, format, *args):
        return

    def _send_json(self, code: int, payload: dict, *, headers: Optional[dict] = None) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802  (BaseHTTPRequestHandler convention)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            body = _read_static("index.html")
            if body is None:
                self._send_json(500, {"error": "index.html missing"})
                return
            self._send_static(200, body, "text/html; charset=utf-8")
            return

        if path == "/snapshot.json":
            try:
                snapshot = build_snapshot()
                self._send_json(200, snapshot)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if path == "/events":
            self._stream_events()
            return

        if path.startswith("/static/"):
            name = path[len("/static/"):]
            # Path traversal guard.
            if "/" in name or ".." in name or name.startswith("."):
                self._send_json(404, {"error": "not found"})
                return
            body = _read_static(name)
            if body is None:
                self._send_json(404, {"error": "not found"})
                return
            ext = Path(name).suffix
            ct = CONTENT_TYPES.get(ext, "application/octet-stream")
            self._send_static(200, body, ct)
            return

        if path == "/health":
            self._send_json(200, {"status": "healthy",
                                  "service": "sovereign-dashboard-web"})
            return

        self._send_json(404, {"error": "not found", "path": path})

    def _stream_events(self) -> None:
        """SSE stream that pushes snapshot updates every 3 seconds."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                snapshot = build_snapshot()
                payload = f"data: {json.dumps(snapshot)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                time.sleep(3)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return


# ── Server entrypoint ───────────────────────────────────────────────────────


DEFAULT_PORT = 3435


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> HTTPServer:
    """Build and return a ThreadingHTTPServer (caller is responsible for
    serve_forever / shutdown). Threading lets SSE streams run in
    parallel with poll requests. Starts the activity-watcher thread on
    first call."""
    _ensure_watcher()
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    return server


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sovereign-dashboard-web",
        description="Web dashboard for Sovereign Stack.",
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="bind host (default: %(default)s)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="bind port (default: %(default)s)")
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_parser().parse_args(argv)
    server = serve(args.host, args.port)
    actual_host, actual_port = server.server_address[:2]
    print(f"sovereign-dashboard-web listening on http://{actual_host}:{actual_port}")
    print("  GET /                serves the dashboard")
    print("  GET /snapshot.json   current state")
    print("  GET /events          SSE feed")
    print("  GET /health          health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print("\ndashboard-web stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
