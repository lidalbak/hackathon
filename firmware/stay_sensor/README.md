# stay_sensor

Atech project for the Airbnb stay sensor, on the **14port** board: temperature and
humidity (AHT20, port 1), light grid (port 2), microphone for sound level only (ports
3-4), motion sensor for tamper detection (port 5), small screen (ports 13-14), distance sensor for the DO NOT TOUCH warning (port 6).

It needs power only: readings go to the agent over Wi-Fi. Interface and setup:
[PROTOCOL.md](PROTOCOL.md). The agent: [`stay_agent/`](../../stay_agent).

```bash
atech validate firmware/stay_sensor
atech upload firmware/stay_sensor
atech check                       # th and imu answer; screen, light and mic can't report
stay-agent setup-device ...       # once, over USB; see PROTOCOL.md
```

Working with a coding agent? `AGENTS.md` / `CLAUDE.md` in this folder tell it how
to use the SDK (same text as `atech guide`).
