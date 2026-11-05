# Stay sensor: device interface

The device only needs power. Every 5 s it POSTs its readings to the agent over Wi-Fi
and shows what the agent answers. Because the device only makes outgoing requests, the
same firmware works with the agent on a laptop (demo) and in the cloud (production):
only the server URL changes. USB is used once, to store the Wi-Fi and server settings.

## Device → agent: `POST {server}/api/readings`

Header `X-Device-Token: <token>` (the agent rejects other tokens when it has one set).

```json
{"device_id": "oslo-1", "temp": 21.4, "hum": 43.0, "db": 48.2, "db_max": 61.0,
 "moving": 0, "tampered": 0, "angle": 3, "near": 0, "uptime_s": 5120}
```

| Field | Meaning |
|---|---|
| `temp`, `hum` | AHT20 (port 1). `-99` / `-1` if the sensor stops answering |
| `db` | sound level, 5 s energy average. Approximate (datasheet calibration) |
| `db_max` | loudest 50 ms of those 5 s |
| `moving` | 1 while the unit is being picked up, bumped or rotated |
| `tampered` | 1 once it has been turned more than 20° from where it was placed, for 0.7 s; 0 again once it is back within 10° and still |
| `near` | 1 while something new is within 10 cm of the distance sensor (port 6, at least 10 of its 64 zones, closer than when the device was placed); the screen then flashes DO NOT TOUCH. Back to 0 after 4 s with nothing near |
| `angle` | degrees away from the placed position |

Posts every ~5 s, and at once when `tampered` changes or something comes within 10 cm. The microphone is only read as a
sound level: no audio is recorded or sent. There is no CO2 sensor.

## Agent → device: the response body

```json
{"level": "ok" | "watch" | "alert", "text": "Sent Anna a note about noise", "guest": "Anna", "guests": 2}
```

`level` sets the header and light colour (green, amber, pulsing red), `text` is the
bottom line, `guest`/`guests` the header. Any field may be left out.

## One-time setup over USB

```bash
stay-agent setup-device --ssid "<wifi>" --password "<password>" \
    --server http://<laptop-ip>:8080 --token <token> --id oslo-1
```

or by hand: `atech send net_set '{"ssid": "...", "pass": "...", "server": "...", "token": "...", "id": "..."}'`.
Settings are stored in the device's flash, never in this repo. It reboots and joins.
Until it reaches the agent, the bottom line of the screen says why: not set up,
joining Wi-Fi, Wi-Fi not reachable, or the agent's HTTP error.

## Over USB instead of Wi-Fi

The same readings come as an `env` event every 5 s, and at once when `tampered` or
`near` changes. The agent answers with `status_set` (same JSON as the Wi-Fi reply);
the screen shows the agent's text while it hears from it at least every 30 s.
`stay-agent serve --usb` does this. To stop the device trying Wi-Fi:
`atech send net_set '{"ssid": "", "server": ""}'`.

## USB actions (setup and debugging)

| Key | Value | Effect |
|---|---|---|
| `net_set` | JSON, see above | stores Wi-Fi/server settings and reboots |
| `net_status` | none | emits a `net` event: state, SSID, IP, server, last HTTP code, id |
| `status_set` / `stay_set` | same JSON as the agent's answer | drives the screen by hand |
| `tamper_arm` | none | takes the current position as "where it belongs", and what the distance sensor sees as its background (otherwise both are set ~8 s after power-on, once still) |
| `dist_grid` | none | emits 8 `dist_row` events: `{"row", "now": [8 mm], "bg": [8 mm]}`, what the distance sensor sees now and its background |
| `noise_cal` | e.g. `125` | dB offset (default 120): raise it if the device reads lower than a phone dB meter |

Over USB the device also prints an `env` event every 5 s with the same readings, for `atech monitor`.
