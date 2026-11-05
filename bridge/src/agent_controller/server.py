"""Local HTTP server the hooks talk to. Listens on 127.0.0.1 only.

POST /event       {"agent","session_id","state","summary","project","ended"}  -> {"ok": true}
POST /permission  {"agent","session_id","summary","project"}  -> {"decision": "allow"|"deny"|null}
GET  /health      -> {"ok": true, "sessions": [...]}
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_controller.controller import Controller
from agent_controller.events import Agent, AgentEvent, SessionState

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765


def event_from_json(data: dict, state: SessionState | None = None) -> AgentEvent:
    return AgentEvent(
        agent=Agent(data["agent"]),
        session_id=str(data["session_id"]),
        state=state or SessionState(data["state"]),
        summary=str(data.get("summary", "")),
        project=str(data.get("project", "")),
    )


def make_server(controller: Controller, port: int = DEFAULT_PORT, wait_seconds: float = 60) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._reply(200, {"ok": True, "sessions": controller.snapshot()})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/event":
                    controller.handle_event(event_from_json(data), ended=bool(data.get("ended")))
                    self._reply(200, {"ok": True})
                elif self.path == "/permission":
                    event = event_from_json(data, SessionState.NEEDS_INPUT)
                    decision = controller.request_permission(event, wait_seconds)
                    self._reply(200, {"decision": decision})
                else:
                    self._reply(404, {"error": "not found"})
            except (KeyError, ValueError) as exc:
                self._reply(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args) -> None:
            log.debug(fmt, *args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
