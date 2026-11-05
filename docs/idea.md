# Idea: Agent Controller

*Research as of 5 November 2026.*

## Summary

A physical controller next to the PC that shows the status of every AI coding agent and lets you answer them without opening the agent's window or terminal. The idea itself is not new: OpenAI sells one for Codex, and hobby status lights exist. What does not exist is a physical controller that works **across agents** and lets you **act**, not just look. That is where we aim.

## Problem

Agents work in parallel, but the human is still the bottleneck. Every session stops and waits when it needs an approval or an answer. To see which one is waiting, you have to leave what you are doing and go into the tool or the terminal.

- **Context switching.** Every check interrupts your work, even when nothing is waiting.
- **Many windows.** Heavy users run 10–15 sessions at once.
- **Several agents.** Claude Code, Codex, Cursor and others each have their own interface. None gives one combined view.

## Solution

A controller beside the keyboard:

- **Status per session:** working, done, or red because it needs you.
- **Answer without opening the tool:** approve or deny with a button. Voice through the microphone later.
- **One press to the right session** when you need to look yourself.
- **Across agents:** the same controller for Claude Code, Codex and others.

## Target users

- **Primary:** developers who run 5+ parallel sessions in at least two tools, such as Claude Code and Codex. Using several tools side by side is common ([CodeAgentSwarm](https://www.codeagentswarm.com/en/guides/claude-code-vs-cursor-vs-codex)).
- **Primary:** people who work on something else on the PC while agents run in the background.
- **Secondary:** people who run agents in the cloud or on a separate machine (for example a Mac mini). The controller then has to work without being plugged into that machine.
- **Not the target:** single-agent users. Their vendor already offers this (see below).

## Competitors

| Product | Type | Agents | What it does | Gap |
| --- | --- | --- | --- | --- |
| [Codex Micro](https://rohitai.com/blog/openai-codex-micro-agent-control-surface) (OpenAI × Work Louder) | Hardware, USD 230, limited run | Codex only, via the ChatGPT app | 6 status keys, approve/decline, push-to-talk, reasoning dial | Locked to one vendor. Criticised for approving without showing what is approved ([Axios](https://www.axios.com/2026/07/15/openai-keyboard-codex-agents)) |
| [Claude Desktop Buddy](https://github.com/anthropics/claude-desktop-buddy) (Anthropic) | Open BLE API + reference firmware | Claude in the desktop app | Status and permission prompts on your own hardware, approve with a button | Claude only, Developer Mode only, not a supported product |
| [Clawd](https://atech.dev/b/clawd) (Atech demo) | DIY on the Atech kit, parts USD 159–209 | Claude Code only | Screen eyes chirp and look up when a session needs you, push-to-talk | Same kit as ours, open code: a Claude-only version is not a product |
| [Claude Code Remote Control](https://technicallycurious.substack.com/p/keep-clauding-from-anywhere) | Phone and web | Claude Code only | Follow and approve sessions from the phone | Free for single-vendor users |
| [Openbase](https://www.producthunt.com/posts/openbase-d65c4e76-aef1-4209-9a77-51bdd1a0fd3e) | Voice app, USD 20/month | Several | Steer and approve agents by voice, no screen | Phone app, not a desk device. Admits context without a screen is unsolved |
| Status lights: [LumoCue](https://lumocue.dev/), [Signal One](https://signalone.shop/en), [Agent Needs You](https://github.com/jfcbxp/agent-needs-you), [Agent Andon Light](https://github.com/toadish-frog/agent-andon-light) | Hardware, commercial and DIY | Claude Code, Codex, Cursor | Green/yellow/red on the desk. LumoCue deliberately never approves | Status only, easy to copy |
| [Conductor](https://www.codeagentswarm.com/en/guides/best-tools-to-run-multiple-ai-coding-agents), [Nimbalyst](https://nimbalyst.com/blog/best-agent-management-tools-2026/), [VS Code Agent Sessions](https://code.visualstudio.com/blogs/2026/02/05/multi-agent-development) and others | Software on screen | 2–21 agents | Combined dashboard for many agents | You have to open them, which is what we want to avoid |

## Positioning

Among tools that show agent status outside the agent itself, the "several agents and can act" corner is nearly empty. Only Openbase is there, and it is a phone voice app, not a desk controller.

What does **not** set us apart: status lights (four exist), an approve button alone (Codex Micro and Hardware Buddy have one), or an on-screen dashboard (that is what we want to avoid).

Three angles that can:

1. **Codex Micro for every agent.** One controller for Claude Code, Codex and others.
2. **Priority, not just lights.** Today's devices say *that* something is waiting, not *what is most urgent*. Show the queue in priority order and escalate only what needs you.
3. **Safe approval on the device.** Show what the agent wants to do and how risky it is. Send risky requests back to the PC.

Pitch line: *"We are building the future interface for managing agents, not a screen with buttons."* It is only credible if the controller means you rarely need to open the tool at all.

## Risks

- **The team are not users yet.** Nobody on the team uses coding agents today. User insight has to come from outside, fast.
- **Platform risk.** Anthropic and OpenAI own both the agents and their state. A third party lives on their hooks and APIs.
- **Uneven integrations.** Cursor, for example, has no hook that says an approval is waiting, only "working" and "your turn" ([dev.to](https://dev.to/icegaming/i-gave-my-ai-coding-agents-a-face-so-they-stop-waiting-on-me-265o)).
- **Margin.** Codex Micro is USD 230 and the Clawd parts cost USD 159–209 at retail. Buying parts at retail leaves little margin on hardware.
- **Security.** Approving from a button opens a new attack surface. When command and approval share one channel, the human check disappears ([Hsu](https://binhsu.github.io/driving-a-local-coding-agent-from-your-phone/)).

## Proving it can be sold

The organisers want this sellable today, so show that someone will pay before polishing hardware.

- [ ] Talk to 5–10 developers who run several agents in parallel: which tools, what waiting costs them, would they pay.
- [ ] Get status from Claude Code and Codex onto the board over USB.
- [ ] Show the prioritised queue and approval with a button. That is what sets the demo apart from Clawd and Codex Micro.
- [ ] Set a price and put up a simple order or pre-order page. The number of orders is the proof.
- [ ] Consider selling only the software to people who already own an Atech kit. That gives margin without buying parts at retail.
