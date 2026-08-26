# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Local-only HTTP application server backing DynosAI Studio."""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .application import DynosAIApplication
from .version import __version__


class StudioAPI:
    """Small provider-neutral JSON API over :class:`DynosAIApplication`."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.app = DynosAIApplication(self.root)

    def get(self, path: str, query: dict[str, list[str]] | None = None) -> tuple[int, dict[str, Any]]:
        query = query or {}
        work_id = (query.get("work_id") or [None])[0]
        if path == "/api/health":
            return 200, {"ok": True, "version": __version__, "root": str(self.root), "local_only": True}
        if path == "/api/project/detect":
            return 200, self.app.detect_project()
        if path == "/api/overview":
            return 200, self.app.project_overview(work_id)
        if path == "/api/work":
            detection = self.app.detector.detect(self.root)
            if not detection.get("has_dynosai"):
                return 200, {"items": []}
            return 200, {"items": self.app.list_work(state=(query.get("state") or [None])[0])}
        if path == "/api/validations":
            return 200, self.app.validation_discovery()
        if path == "/api/risk":
            return 200, self.app.risk_assessment(work_id)
        if path == "/api/blockers":
            return 200, self.app.explain_blockers(work_id)
        if path == "/api/events":
            detection = self.app.detector.detect(self.root)
            if not detection.get("has_dynosai"):
                return 200, {"items": []}
            self.app.engine.db.initialize()
            after = int((query.get("after_id") or ["0"])[0] or 0)
            return 200, {"items": self.app.list_events(after_id=after, work_id=work_id, limit=100)}
        return 404, {"error": "not_found", "message": f"Unknown API path: {path}"}

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/api/project/initialize":
            result = self.app.initialize_project(
                name=payload.get("name"),
                agent=payload.get("agent"),
                allow_git_init=bool(payload.get("allow_git_init", False)),
                language=payload.get("language"),
                test_command=payload.get("test_command"),
            )
            return 200, result
        if path == "/api/work/start":
            description = str(payload.get("description") or "").strip()
            if not description:
                return 400, {"error": "validation", "message": "description is required"}
            provider = str(payload.get("provider") or "codex").lower()
            if provider not in {"cursor", "codex", "claude"}:
                return 400, {"error": "validation", "message": "provider must be cursor, codex or claude"}
            result = self.app.start_feature(
                description,
                provider=provider,
                workspace_strategy=str(payload.get("workspace_strategy") or "interactive_branch"),
            )
            return 200, result
        if path == "/api/validations/approve":
            names = payload.get("names")
            if names is not None and (not isinstance(names, list) or not all(isinstance(x, str) for x in names)):
                return 400, {"error": "validation", "message": "names must be an array of validation profile names"}
            return 200, self.app.approve_discovered_validations(names, overwrite=bool(payload.get("overwrite", False)))
        if path == "/api/interaction/resolve":
            interaction_id = str(payload.get("interaction_id") or "").strip()
            action = str(payload.get("action") or "").strip()
            if not interaction_id or not action:
                return 400, {"error": "validation", "message": "interaction_id and action are required"}
            content = payload.get("content") if isinstance(payload.get("content"), dict) else None
            return 200, self.app.resolve_interaction(interaction_id, action, content)
        return 404, {"error": "not_found", "message": f"Unknown API path: {path}"}


class DynosAIStudioServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the selected project API."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: str | Path):
        host = address[0]
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("DynosAI Studio only binds to a loopback address")
        self.api = StudioAPI(root)
        super().__init__(address, DynosAIStudioHandler)


class DynosAIStudioHandler(BaseHTTPRequestHandler):
    server: DynosAIStudioServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - stdlib override
        return

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: Exception) -> None:
        self._json(500, {"error": exc.__class__.__name__, "message": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol
        if not self._host_allowed():
            self._json(403, {"error": "forbidden_host"})
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                status, payload = self.server.api.get(parsed.path, parse_qs(parsed.query))
                self._json(status, payload)
            except Exception as exc:  # surface bounded local API errors to Studio
                self._error(exc)
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol
        if not self._host_allowed():
            self._json(403, {"error": "forbidden_host"})
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._json(404, {"error": "not_found"})
            return
        if (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"error": "content_type", "message": "application/json is required"})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or "0"), 1_000_000)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                self._json(400, {"error": "validation", "message": "JSON object required"})
                return
            status, result = self.server.api.post(parsed.path, payload)
            self._json(status, result)
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid_json"})
        except Exception as exc:
            self._error(exc)

    def _static(self, path: str) -> None:
        requested = "index.html" if path in {"", "/"} else path.lstrip("/")
        if requested not in {"index.html", "app.js", "styles.css"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        asset = resources.files("dynosai_flow.studio_assets").joinpath(requested)
        data = asset.read_bytes()
        content_type = mimetypes.guess_type(requested)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)


def create_server(root: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> DynosAIStudioServer:
    """Create, but do not start, a loopback-only Studio server."""
    return DynosAIStudioServer((host, int(port)), root)


def serve_studio(root: str | Path, *, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Serve the Local Studio until interrupted."""
    server = create_server(root, host=host, port=port)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"DynosAI Studio {__version__} -> {url}")
    print(f"Project -> {Path(root).expanduser().resolve()}")
    print("Local-only server. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
