# Hardware

The prototype runs on the [Atech](https://atech.dev) kit: an ESP32-S3 motherboard with numbered ports and modules that slide in without soldering. The board talks to the PC as JSON lines over USB serial ([Atech docs](https://atech.dev/docs)).

## What we have

The Small Kit (10 modules), plus access to add-on modules such as a speaker, a microphone and sensors.

## Planned layout

Defined in [`firmware/controller/project.yaml`](../firmware/controller/project.yaml) and checked with `atech validate`.

| Port | Module | Instance | Source | Job in the controller |
| --- | --- | --- | --- | --- |
| 1–2 | Screen, 160×80 colour (`st7735_tft`) | `screen` | Small Kit | Show the selected request and its risk |
| 3–4 | Speaker (`speaker`) | `speaker` | Add-on | Chirp when an agent needs you |
| 5–6 | Microphone (`microphone`) | `mic` | Add-on | Push-to-talk answers, later |
| 7 | Button (`button`) | `approve` | Small Kit | Approve the selected request |
| 9 | Light grid 3×3 (`neopixel`) | `status` | Small Kit | One LED per session, up to 9 |
| 10 | Button (`button`) | `deny` | Small Kit | Deny the selected request |
| 11 | Distance sensor (`distance_sensor`) | `presence` | Small Kit | Wake the screen when you lean in |
| 13–14 | free | | | Room for a knob (`rotary_encoder`) to scroll the queue |
| 8 | USB-C | | | Power and data. Not in `project.yaml` |

That leaves no spare double port except 13–14.

### Upgrade path

- **Big Screen (`st7789_tft`, 320×240).** The small screen fits a status line and a short summary, not a command or a diff. For safe approval the Big Screen is the better choice. It takes two ports, the same calls, and replaces `st7735_tft`.
- **Knob (`rotary_encoder`).** Turn to move through the queue, press to jump to that session on the PC. Fits ports 13–14.

## Constraints that shape the design

- **USB, not WiFi.** Over WiFi, Atech routes messages through its own cloud gateway (`gateway.atech.dev`). Agent requests can contain code and commands, so we keep the controller on USB.
- **One program per board.** Only one program can hold the serial port. While the bridge runs, stop `atech monitor`, and stop the bridge before `atech upload`.
- **Confirm each action.** The board can drop actions that arrive in a flood. The bridge waits for each `board.call()` to be confirmed before sending the next.
- **BLE is possible, not built in.** The ESP32-S3 does BLE only, and the Atech SDK generates nothing for it. Pairing directly with Anthropic's Hardware Buddy API would mean writing that BLE service ourselves.
- **One distance sensor per project.** It uses the chip's only hardware I2C bus.

## Open questions

- [ ] **Which board is in the Small Kit: 10-port or 14-port?** The layout assumes 14-port. Run `atech view firmware/controller` and compare with the board in your hand.
- [ ] **What exactly is in our Small Kit and in the add-on pile?** Confirm we have two buttons, the screen, the light grid and the distance sensor, plus the speaker and microphone.
- [ ] **Can we get a Big Screen and a knob?** Both make the demo much stronger.
- [ ] **How do the modules get mounted?** Clawd uses a printed shell and ribbon cables. Do we have access to a 3D printer?
