# emotiv-rawread

Raw 14-channel EEG from an **Emotiv EPOC+**, offline — no EMOTIV Launcher, no
Cortex licence, no account, no network.

This repository contains a local control panel for the headset and the patches
that make the `emotiv-rs` crate decode an EPOC+ correctly. Everything runs on
your own machine.

Verified on macOS 15 (Apple silicon) against an EPOC+, firmware `0x633`, through
its USB receiver: **127.5 Hz**, 14 channels, 0.5% packet loss.

---

## Why this exists

`emotiv-rs` could not read this headset. It connected, reported success, and
produced confident nonsense — every channel pinned to the hardware rails, a
battery reading sweeping randomly between 0 and 100%, and contact-quality values
outside the scale. Three separate faults caused it, and each masked the others.

### 1. The Bluetooth service identifier was wrong

The crate only requests the `00001100-d102-11e1-9b23-00025b00a5a5` service
family. The EPOC+ publishes an entirely different one:

```
81072f40-9f3d-11e3-a9dc-0002a5d5c51b
  ...2f41  notify         encrypted EEG, ~257 notifications/s
  ...2f42  notify         motion, ~32/s
  ...2f43  write, notify  control
  ...2f44  read           six-byte status word
```

A Web Bluetooth grant only reaches services it names in advance, so the
connection failed with `NotFoundError: no accessible primary services on this
device`. Notifications begin as soon as they are subscribed to — no start-stream
command is required.

### 2. The decryption key comes from the receiver, not the headset

This is the one that costs people days. The AES key is **not** derived from the
serial printed on the headset or shown in the Launcher (`4A2C0290` here). It is
derived from the **USB receiver's** serial — `UD201807260038EF` — using only its
**last four characters**:

```
k = [s[-1], s[-2], s[-2], s[-3], s[-3], s[-3], s[-2], s[-4],
     s[-1], s[-4], s[-2], s[-2], s[-4], s[-4], s[-2], s[-1]]
```

AES-128-ECB. `emotiv-rs` instead required a serial of at least twelve
characters, so when handed an eight-character one it synthesised padding
(`EPOC4A2C0290`) and produced a key that decrypted to noise rather than failing.

### 3. The EPOC+ packet is not bit-packed

The crate unpacks 14-bit fields at explicit bit offsets. The EPOC+ sends plain
**byte pairs**: a one-byte counter, then seven electrodes in bytes 2..16 and
seven more in bytes 18..32, skipping bytes 16-17.

```
microvolts = (b1 * 0.128205128205129) + 4201.02564096001
                                      + ((b2 - 128) * 32.82051289)
```

Packets whose second byte is `32` are motion frames at 32 Hz and carry no
electrode payload; EEG frames have `16` there. The two types carry **independent
counters**, so any continuity check must filter on byte 1 first.

### 4. Only one USB interface carries EEG

The receiver exposes two HID interfaces. Interface 1 (usage page `0xffff`)
carries the data; interface 0 (`0xf0ff`) opens successfully and then delivers
nothing. Discovery here reports only the former.

---

## How to tell real EEG from a wrong key

Decrypting with a wrong key yields uniformly random values, which are easy to
mistake for a signal. These checks are decisive, and all are reported by the
control panel:

| Check | Wrong key | Correct key |
|---|---|---|
| Counter steps of +1 | ~0% | >99% |
| Per-channel standard deviation | ~4800 µV, all alike | 2-12 µV off-head, differing severalfold |
| Sample range | pinned at ±8399 µV | a narrow band around a stable baseline |
| Battery | jumps randomly | steady |

The ~4800 µV figure is not arbitrary: uniform noise over ±8399 µV has a standard
deviation of 8399/√3 = 4849 µV. Measured output matched that to within 0.4%.

**A flashing LED, a successful connection, and "low signal quality" warnings all
occur with a wrong key.** Signal quality, contact quality and battery are fields
*inside* the encrypted packet, so they are random when the key is. A healthy
radio link and correct decryption are independent questions.

---

## Setting it up

### 1. Build the patched library

```bash
# Get the upstream crate (either source works)
cargo new --lib /tmp/x && cd /tmp
curl -O https://static.crates.io/crates/emotiv/emotiv-0.0.14.crate
tar xzf emotiv-0.0.14.crate
cd emotiv-0.0.14

# Apply the patches
cp /path/to/emotiv-rawread/patches/src_raw_usb.rs.new src/raw/usb.rs
for p in /path/to/emotiv-rawread/patches/*.patch; do
    target=$(basename "$p" .patch | tr '_' '/' | sed 's#^src/#src/#')
    patch -p0 < "$p"     # paths are in the diff headers; adjust if needed
done

# Build - note the `usb` feature, without which the receiver is invisible
cargo build --release --features simulate,config-toml,raw,usb
```

Put the three binaries (`emotiv-cli`, `emotiv-raw`, `emotiv-tui`) somewhere on
your `PATH`. Symlinks work well, so a rebuild needs no reinstall:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/target/release/emotiv-raw" ~/.local/bin/emotiv-raw
```

zsh reads `.zshrc` only for interactive shells and `.zprofile` only for login
shells, so put the PATH entry in **both**, guarded so it is added once:

```bash
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
```

### 2. Run the control panel

```bash
python3 control/server.py          # then open http://127.0.0.1:8777
```

Standard library only — no pip install, no virtual environment. It binds to
localhost. On macOS, `control/Start control panel.command` starts it on a
double-click and opens the browser once the server answers.

---

## Using it

1. Plug in the USB receiver and switch the headset on. It sleeps quickly when it
   is not being worn, so put it on first.
2. Press **Discover headsets**. The transport of each result is shown on its row.
3. Select the headset, press **Start**.
4. **Start recording** writes the unmodified event stream to
   `control/recordings/<source>_<timestamp>.ndjson`, one JSON object per line.

The **Simulator** source needs no headset at all and is the way to work on the
interface itself.

### Bluetooth versus the receiver

The headset advertises over Bluetooth **only when the receiver is unplugged** —
with it attached, the headset links to the receiver and stays silent. A
Bluetooth scan is therefore only evidence about the headset's state when the
receiver is out.

It will also never appear in macOS System Settings → Bluetooth, which lists
pairable peripherals such as keyboards. A sensor that merely advertises GATT
services is found by scanning, which is what the panel does.

---

## The JSON interface

The patches add `--json` to `emotiv-raw` and `emotiv-cli`: standard output
becomes one JSON object per line and the human-readable tables are suppressed.
Logging already goes to standard error, so standard output carries nothing else.

`emotiv-raw --json` emits:

| `type` | when | carries |
|---|---|---|
| `devices` | with `--list` | discovered devices: model, serial, battery, channel names |
| `connected` | once | the device, channel count, physical range |
| `sample` | every packet | the whole decrypted struct: microvolts and raw counts for every channel, counter, timestamps |
| `stats` | every 3 s | continuity, clipping, amplitude about a running mean, verdict |
| `debug` | with `--debug-view` | notification, decode and decrypt counters |
| `end` | on exit | packet total, duration, average rate |

`EMOTIV_DUMP_RAW=1` additionally emits each encrypted packet as hex, before
decryption, for offline analysis.

---

## Recovering the key for a different headset

If your receiver's serial differs, `tools/find_serial.py` finds the key from a
capture without needing the hardware again:

```bash
EMOTIV_DUMP_RAW=1 emotiv-raw --json --connect <receiver-serial> > capture.ndjson
python3 -c "
import json
print('\n'.join(json.loads(l)['hex'] for l in open('capture.ndjson')
                 if l.strip() and json.loads(l).get('type')=='raw'))" > raw.hex
python3 tools/find_serial.py raw.hex
```

It reimplements every known derivation, reassembles fragmented notifications,
and scores each candidate by how often the packet counter steps by one. A
correct key scores near 1.0; chance is 0.000015. Run
`python3 tools/find_serial.py --self-test` first — it encrypts a synthetic
stream with a known key and must recover it.

---

## What is not done

Stated plainly, because a confident-looking wrong answer is worse than a gap:

- **Contact quality and battery are not decoded** on the receiver path. Byte 16
  of the EEG packet is multiplexed by the counter — variance explained 0.97 —
  which is where per-electrode contact quality lives, but mapping slots to
  electrodes needs a capture taken while the headset is actually worn. Until
  then the panel shows "not reported" rather than inventing a value.
- **The Bluetooth path still decodes to noise.** The service fix lands the
  connection and the packets arrive, but the key derives from the receiver's
  serial and the receiver is not part of a Bluetooth link. Whether the same key
  applies there is untested.
- **Motion frames are discarded.** They decrypt, and the layout is unverified.

---

## Licence

The code in this repository is MIT licensed — see `LICENSE`. It builds on
`emotiv-rs`, `emokit` and `CyKit`; see `NOTICE.md` for what came from where.
