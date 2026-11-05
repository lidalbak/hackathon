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
| `src/agent_controller/events.py` | Session model, colours per state, queue ranking. Tested |
| `tests/test_events.py` | Tests for the ranking |

## Next to build

1. **Board driver:** open the board with `atech.Board.connect()`, give each session a light-grid slot (0–8), and send `status_pixel` when a state changes. Chirp with `speaker_play_rtttl` when something enters `needs_input`.
2. **Hook listener:** a small local server, plus a command each agent's hook calls to forward its payload. Must exit cleanly when the bridge is not running.
3. **Claude Code adapter:** map hook payloads to `AgentEvent`.
4. **Codex adapter:** same for Codex.
5. **Approve flow:** route `approve` / `deny` presses back to the agent. Needs a hook that waits for an answer (open question in the architecture doc).
