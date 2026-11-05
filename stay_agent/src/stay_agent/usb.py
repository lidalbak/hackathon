"""The stay sensor over USB instead of Wi-Fi: the demo setup.

The board prints an ``env`` event every 5 s, and at once when it is moved or touched.
Each one goes through the harness like a Wi-Fi POST would, and the answer goes back
with the ``status_set`` action. The board shows the agent's text while it hears from
it at least every 30 s, so every reading gets an answer.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from pydantic import ValidationError

from stay_agent.harness import StayHarness
from stay_agent.models import Reading

log = logging.getLogger(__name__)

RETRY_SECONDS = 3


def run_usb(harness: StayHarness, port: Optional[str] = None) -> None:
    """Runs until interrupted; reconnects when the board is unplugged."""
    from atech import Board, NoBoardFoundError  # only needed in USB mode

    tz = harness.settings.tz
    while True:
        try:
            with Board.connect(port=port) as board:
                log.info("board connected over USB")
                for event in board.events(keys=["env"]):
                    try:
                        reading = Reading.model_validate({**event.value, "device_id": "usb", "at": datetime.now(tz)})
                    except ValidationError:
                        log.warning("unreadable env event: %r", event.value)
                        continue
                    reply = harness.ingest(reading)
                    board.call("status_set", reply.model_dump())
        except NoBoardFoundError:
            log.warning("no board on USB; retrying in %d s", RETRY_SECONDS)
        except KeyboardInterrupt:
            raise
        except Exception:  # unplugged, port taken, no ack: try again
            log.exception("USB connection lost; reconnecting in %d s", RETRY_SECONDS)
        time.sleep(RETRY_SECONDS)
