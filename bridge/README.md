# Bridge

Python program on the PC. It collects hook events from the agents, keeps one ranked session list, and drives the board over USB. Design: [docs/architecture.md](../docs/architecture.md).

## Setup

```bash
cd bridge
pip install -e ".[dev]"
pytest
```

## What is here

| File | State |
| --- | --- |
| `src/agent_controller/events.py` | Session model, colours per state, queue ranking, `screen_payload()` for the board's `show_session` action. Tested |
| `tests/test_events.py` | Tests for ranking and the screen payload |

## Next to build

1. **Board driver:** open the board with `atech.Board.connect()`, give each session a light-grid slot (0–8), and send `status_pixel` when a state changes. Send `show_session` with `screen_payload(top_of_queue, 1, len(queue))` whenever the top of the queue changes. Chirp with `speaker_play_rtttl` when something enters `needs_input`.
2. **Hook listener:** a small local server, plus a command each agent's hook calls to forward its payload. Must exit cleanly when the bridge is not running.
3. **Claude Code adapter:** map hook payloads to `AgentEvent`.
4. **Codex adapter:** same for Codex.
5. **Approve flow:** a `PermissionRequest` hook that waits for the bridge, which answers when `approve` or `deny` is pressed. On timeout or with no bridge, print nothing so the agent's own prompt takes over. Details in [docs/architecture.md](../docs/architecture.md#approving-from-the-device).
