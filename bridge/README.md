# Bridge

Python program on the PC. Agents' hooks report to it; it keeps one ranked session list, shows the top of the queue on the board, and answers permission requests with the board's buttons. Design: [docs/architecture.md](../docs/architecture.md).

## Install and run

```bash
pip install "./bridge[voice]"   # or the .whl file; [voice] adds local Whisper for speech to text
agent-controller serve          # keeps the board's USB port: close `atech monitor` first
```

Leave it running while you work. Ctrl+C stops it; agents then fall back to their own prompts.
The first start downloads the Whisper model (base, about 150 MB). Options:
`--language no` (Norwegian; default is to detect), `--whisper-model small` (better Norwegian, about 480 MB), `--no-voice`.

## Connect Claude Code

```bash
agent-controller install-claude-hooks
```

adds the hooks to `~/.claude/settings.json` (the Claude desktop app's Code tab reads the same file). It keeps all other settings and hooks, replaces only its own entries, and writes a timestamped backup first. `agent-controller claude-settings` prints the block instead. Start a new Claude Code session afterwards.

## What you can do from the board

| Claude Code … | Board | You |
| --- | --- | --- |
| asks for permission (command, file, plan) | Chirps, shows the request | Left: allow. Right: deny. Nothing for 60 s: the app asks |
| asks a multiple-choice question | Shows the header and one option, `1/3` | Turn the knob through options, left picks, next question follows. Hold the knob to say a free-text answer instead. Right: answer in the app |
| ends its turn with a question in plain text | Shows the question, "Hold knob to answer", for 8 s | Hold the knob and speak. The board shows what it heard: left sends it and Claude continues, right lets you redo. Nothing within 8 s: the turn ends as usual |
| is working / done | Blue WORKING / green DONE | Turn the knob to look at other sessions, push it to jump back to what needs you most |

Every spoken answer is shown on the screen before it is sent. At the start of each session the hook asks Claude to put its questions as multiple choice (`AskUserQuestion`), so most questions can be answered with the knob.

The hook never breaks Claude Code: if the bridge is not running it exits at once with no output.

## Files

| File | What it does |
| --- | --- |
| `src/agent_controller/events.py` | Session model, colours, queue ranking, screen payload |
| `src/agent_controller/controller.py` | Sessions; permission, choice and text requests; knob, buttons and push-to-talk; what the board shows |
| `src/agent_controller/server.py` | Local HTTP server on 127.0.0.1:8765 that the hooks call |
| `src/agent_controller/claude.py` | Claude Code hook payloads → events, decisions → hook output, settings snippet |
| `src/agent_controller/voice.py` | Speech to text with local Whisper |
| `src/agent_controller/cli.py` | `agent-controller serve`, `hook claude`, `install-claude-hooks`, `claude-settings` |
| `tests/` | End to end with a fake board: approve/deny, knob choices, voice answers, plain-text questions, settings install |

## Next

- Codex adapter (`hook codex`), same bridge. Verify Codex's `PermissionRequest` output format first.
- Encrypt the Bluetooth link to the Claude app.
