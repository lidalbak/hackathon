"""HTTP server the device posts to, and the replica reads from.

POST /api/readings   device -> {"device_id","temp","hum","db","db_max","moving","tampered","angle"}
                     <- {"level","text","guest","guests"}  (what the device shows)
                     header X-Device-Token must match settings.device_token when that is set
GET  /api/state      current booking, latest reading, incidents, messages, stats
GET  /api/reports/<booking_id>   the post-stay report, with the review draft
GET  /health         {"ok": true}

Listens on all interfaces: the device reaches it over Wi-Fi.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from stay_agent.harness import StayHarness
from stay_agent.models import Reading

log = logging.getLogger(__name__)

DEFAULT_PORT = 8080
TICK_SECONDS = 5


def make_server(harness: StayHarness, port: int = DEFAULT_PORT, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    settings = harness.settings

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, body: object) -> None:
            data = json.dumps(body, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")  # the replica's page reads /api/state
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/readings":
                return self._reply(404, {"error": "not found"})
            if settings.device_token and self.headers.get("X-Device-Token") != settings.device_token:
                return self._reply(401, {"error": "bad device token"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                reading = Reading.model_validate({**body, "at": datetime.now(settings.tz)})
            except (json.JSONDecodeError, ValidationError) as e:
                return self._reply(400, {"error": str(e)})
            self._reply(200, harness.ingest(reading).model_dump())

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                return self._reply(200, {"ok": True})
            if self.path == "/api/state":
                return self._reply(200, harness.state())
            if self.path.startswith("/api/reports/"):
                report = harness.reports.get(self.path.rsplit("/", 1)[-1])
                if report is None:
                    return self._reply(404, {"error": "no report for that booking yet"})
                return self._reply(200, report.model_dump(mode="json"))
            self._reply(404, {"error": "not found"})

        def log_message(self, fmt: str, *args: object) -> None:
            log.debug(fmt, *args)

    def ticker() -> None:
        while True:
            time.sleep(TICK_SECONDS)
            try:
                harness.tick(datetime.now(settings.tz))
            except Exception:
                log.exception("tick failed")

    threading.Thread(target=ticker, name="stay-agent-tick", daemon=True).start()
    return ThreadingHTTPServer((host, port), Handler)
