# Headset control panel

A local web interface for testing and controlling the EMOTIV headset through the
`emotiv-rs` binaries. It needs no EMOTIV Launcher, no licence and no network.

Electroencephalography is written EEG below; Bluetooth Low Energy is written BLE.

## Running it

```
python3 ~/Documents/emotiv-rs/control/server.py
```

Then open <http://127.0.0.1:8777>. Stop it with Ctrl-C. `Start control panel.command`
in this folder does the same thing on a double-click.

The server is standard library only — no pip install and no virtual environment.
It binds to 127.0.0.1, so nothing outside the Mac can reach it.

## What the panel does

1. **Source** — choose the raw Bluetooth path (the real headset) or the simulator
   (a synthetic stream, for working on the interface with no headset present).
   *Discover headsets* lists what is in range with model, serial, battery and
   channel count; click one to select it, then press *Start*.
2. **Stream health** — packet count, achieved sample rate, elapsed time and
   battery, plus continuity, clipping rate, root mean square amplitude and a
   GOOD/WARN/BAD verdict. Those four are computed inside `emotiv-raw` and arrive
   every three seconds, so they stay blank until the first statistics line.
3. **Electrode contact quality** — one tile per electrode on the headset's 0–4
   scale, 4 best. Grey means no value reported yet.
4. **Live traces** — all channels stacked, about five seconds of history, with a
   selectable vertical scale and optional per-channel mean removal.
5. **Event log** — every non-sample event, including everything the binary writes
   to standard error, which is where connection failures appear.

*Start recording* writes the unmodified event stream to
`control/recordings/<source>_<timestamp>.ndjson`, one JSON object per line, with a
`recording_header` first line carrying the device and channel labels.

## The interface it depends on

This panel requires the `--json` flag added to `emotiv-raw` and `emotiv-cli` in
this repository. With it, standard output becomes one JSON object per line and
the human-readable tables are suppressed; log output stays on standard error, so
standard output carries nothing but JSON.

`emotiv-raw --json` emits:

| `type`      | when                | carries                                                    |
|-------------|---------------------|------------------------------------------------------------|
| `devices`   | with `--list`       | every discovered device, with model, serial, battery, channel names |
| `connected` | once, on connection | the device, its channel count and the physical microvolt range |
| `sample`    | every packet        | the complete `DecryptedData` — microvolts and raw counts for every channel, contact quality, signal quality, battery, counter, motion, both timestamps |
| `stats`     | every 3 s           | continuity, clipping rate, mean absolute and root mean square amplitude, average pairwise correlation, verdict |
| `debug`     | every 2 s with `--debug-view` | notification, decode and decrypt counters |
| `end`       | on exit             | packet total, duration, average rate |

`emotiv-cli --json` emits one object per Cortex event: `status`, `labels`, `eeg`,
`motion`, `dev`, `metrics`, `pow`, `command`, `facial`, `warning`, `error`.

Unlike the printed view, which shows the first five channels rounded to 0.1 µV
every sixteenth packet, the JSON stream carries every packet and every channel at
full precision.

## Troubleshooting

- **Nothing found when discovering.** The headset must be switched on and in
  range. The first time it runs, macOS asks the application you launched it from
  (Terminal, usually) for Bluetooth permission — if no prompt appeared, check
  System Settings → Privacy & Security → Bluetooth.
- **Discovery works but the stream never starts.** Another process may hold the
  headset. The EMOTIV Launcher's `CortexService` claims it when running; quit the
  Launcher before using the raw Bluetooth path.
- **`emotiv-raw not found on PATH`.** The server looks on PATH first and then in
  the repository folder beside `control/`. The binaries are symlinked into
  `~/.local/bin`, which `~/.zprofile` and `~/.zshrc` add to PATH.
- **The panel loads but no samples arrive.** Check the event log: anything the
  binary writes to standard error is forwarded there verbatim.
