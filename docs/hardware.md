# Hardware

The prototype runs on the [Atech](https://atech.dev) kit: an ESP32-S3 motherboard with numbered ports and modules that slide in without soldering. The board talks to the PC as JSON lines over USB serial ([Atech docs](https://atech.dev/docs)).

## What we have

The Small Kit (10 modules) with the 14-port board, plus access to add-on modules such as a speaker, a microphone and sensors.

## Planned layout

Defined in [`firmware/controller/project.yaml`](../firmware/controller/project.yaml) and checked with `atech validate`.

| Port | Module | Instance | Source | Job in the controller |
| --- | --- | --- | --- | --- |
| 1–2 | Screen, 160×80 colour (`st7735_tft`) | `screen` | Small Kit | Which agent, which project, its state and what it wants |
| 3–4 | Speaker (`speaker`) | `speaker` | Add-on | Chirp when an agent needs you |
| 5–6 | Microphone (`microphone`) | `mic` | Add-on | Push-to-talk answers, later |
| 7 | Button (`button`) | `approve` | Small Kit | Approve the selected request |
| 9 | Light grid 3×3 (`neopixel`) | `status` | Small Kit | One LED per session: 0–7 from the bridge, 8 for the Claude app |
| 10 | Button (`button`) | `deny` | Small Kit | Deny the selected request |
| 11 | free | | | |
| 13–14 | Knob (`rotary_encoder`) | `knob` | Add-on | Turn: move through waiting requests. Push: back to the top. Ring: place in queue, in the state's colour |
| 8 | USB-C | | | Power and data. Not in `project.yaml` |

That leaves port 11 and the double port 13–14 free.

### Upgrade path

- **Big Screen (`st7789_tft`, 320×240).** The small screen fits a status line and a short summary, not a command or a diff. For safe approval the Big Screen is the better choice. It takes two ports, the same calls, and replaces `st7735_tft`.
- **Knob (`rotary_encoder`).** Turn to move through the queue, press to jump to that session on the PC. Fits ports 13–14.

## Constraints that shape the design

- **USB, not WiFi.** Over WiFi, Atech routes messages through its own cloud gateway (`gateway.atech.dev`). Agent requests can contain code and commands, so we keep the controller on USB.
- **One program per board.** Only one program can hold the serial port. While the bridge runs, stop `atech monitor`, and stop the bridge before `atech upload`.
- **Confirm each action.** The board can drop actions that arrive in a flood. The bridge waits for each `board.call()` to be confirmed before sending the next.
- **BLE is possible, not built in.** The ESP32-S3 does BLE only, and the Atech SDK generates nothing for it. Pairing directly with Anthropic's Hardware Buddy API would mean writing that BLE service ourselves.

## Open questions

- [x] **Which board is in the Small Kit?** 14-port, as the layout assumes.
- [ ] **What exactly is in our Small Kit and in the add-on pile?** Confirm we have two buttons, the screen and the light grid, plus the speaker and microphone.
- [ ] **Can we get a Big Screen and a knob?** Both make the demo much stronger.
- [ ] **How do the modules get mounted?** Clawd uses a printed shell and ribbon cables. Do we have access to a 3D printer?
