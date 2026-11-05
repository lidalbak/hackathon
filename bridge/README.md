# Bridge

Python program on the PC. Agents' hooks report to it; it keeps one ranked session list, shows the top of the queue on the board, and answers permission requests with the board's buttons. Design: [docs/architecture.md](../docs/architecture.md).

## Install and run

```bash
pip install ./bridge            # or the .whl file
agent-controller serve          # keeps the board's USB port: close `atech monitor` first
```

Leave it running while you work. Ctrl+C stops it; agents then fall back to their own prompts.

## Connect Claude Code

```bash
agent-controller claude-settings
```

prints a `hooks` block. Merge it into `~/.claude/settings.json` (the Claude desktop app's Code tab reads the same file). Restart the Claude Code session afterwards.

What the hooks do:

| Hook | Effect on the board |
| --- | --- |
| `PermissionRequest` | Screen shows the request, the board chirps. Left button allows, right button denies. No press within 60 s (`--wait`): Claude Code asks in the app as usual |
| `UserPromptSubmit`, `PreToolUse`, `PostToolUse` | Working, with the prompt or the tool |
| `Stop` | Done, your turn |
| `SessionStart` / `SessionEnd` | Session added / removed |

With several sessions, **turn the knob** to move through them (the screen shows `2/3`, the ring shows where you are). The buttons answer the one on screen. **Push the knob** to jump back to what needs you most. A new request jumps to the top unless you are already looking at one that waits.

The hook never breaks Claude Code: if the bridge is not running it exits at once with no output.

## Files

| File | What it does |
| --- | --- |
| `src/agent_controller/events.py` | Session model, colours, queue ranking, screen payload |
| `src/agent_controller/controller.py` | Sessions, pending requests, what the board shows, button handling |
| `src/agent_controller/server.py` | Local HTTP server on 127.0.0.1:8765 that the hooks call |
| `src/agent_controller/claude.py` | Claude Code hook payloads → events, decisions → hook output, settings snippet |
| `src/agent_controller/cli.py` | `agent-controller serve`, `hook claude`, `claude-settings` |
| `tests/` | End to end with a fake board: hook → bridge → button → hook output |

## Next

- Codex adapter (`hook codex`), same bridge. Verify Codex's `PermissionRequest` output format first.
- Encrypt the Bluetooth link to the Claude app.
