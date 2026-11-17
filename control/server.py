#!/usr/bin/env python3
"""Headset control panel for the emotiv-rs binaries.

Serves a single page and bridges it to `emotiv-raw` / `emotiv-cli`, which are
run with `--json` so their stdout is one JSON object per line.

Standard library only - no pip install, no virtual environment. The browser
receives events over Server-Sent Events; control actions are plain POSTs.

Run:  python3 server.py [--port 8765]
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
REPO = HERE.parent
RECORDINGS = HERE / "recordings"

# How often the server flushes buffered EEG samples to the browser. The
# headset sends 128 packets/s; forwarding each one as its own event is
# needless traffic, so they are batched.
FLUSH_INTERVAL_S = 0.06


def find_binary(name: str) -> str | None:
    """Locate a binary: PATH first, then the repo folder beside this script."""
    found = shutil.which(name)
    if found:
        return found
    local = REPO / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


class Session:
    """One running headset process and the state derived from its output."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.source: str | None = None
        self.subscribers: list[queue.Queue] = []
        self.labels: list[str] = []
        self.device: dict | None = None
        self.stats: dict = {}
        self.packets = 0
        self.started_at: float | None = None
        # The rate clock must start at the FIRST SAMPLE, not at process
        # launch: the binary spends about eight seconds scanning Bluetooth
        # before streaming, and counting that time reported 70 Hz for a
        # stream genuinely running at 127.5 Hz.
        self.first_sample_at: float | None = None
        self.last_error: str | None = None
        self.record_path: Path | None = None
        self.record_fh = None
        self.record_count = 0
        self._pending: list[list[float]] = []
        self._pending_cq: list[int] = []
        self._battery: int | None = None

    # -- subscriber plumbing -------------------------------------------------

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=512)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def publish(self, event: dict) -> None:
        payload = json.dumps(event)
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            try:
                q.put_nowait(payload)
            except queue.Full:
                # A browser that cannot keep up loses samples rather than
                # stalling the reader thread.
                pass

    # -- lifecycle -----------------------------------------------------------

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, source: str, device: str | None) -> dict:
        if self.is_running():
            return {"ok": False, "error": "A session is already running."}

        if source == "simulate":
            binary = find_binary("emotiv-cli")
            if not binary:
                return {"ok": False, "error": "emotiv-cli not found on PATH."}
            cmd = [binary, "--json", "--simulate"]
        elif source == "raw":
            binary = find_binary("emotiv-raw")
            if not binary:
                return {"ok": False, "error": "emotiv-raw not found on PATH."}
            cmd = [binary, "--json"]
            if device:
                cmd += ["--connect", device]
        else:
            return {"ok": False, "error": f"Unknown source: {source}"}

        self.labels = []
        self.device = None
        self.stats = {}
        self.packets = 0
        self.last_error = None
        self._pending = []
        self._battery = None
        self.started_at = time.time()
        self.first_sample_at = None
        self.source = source

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        threading.Thread(target=self._flush_loop, daemon=True).start()
        self.publish({"type": "session", "state": "started", "source": source,
                      "command": " ".join(cmd)})
        return {"ok": True, "command": " ".join(cmd)}

    def stop(self) -> dict:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            self.stop_recording()
            return {"ok": True, "note": "No session was running."}
        try:
            # Both binaries quit on any stdin line; fall back to signals.
            if proc.stdin:
                proc.stdin.write("q\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.stop_recording()
        self.publish({"type": "session", "state": "stopped"})
        return {"ok": True}

    # -- recording -----------------------------------------------------------

    def start_recording(self) -> dict:
        if self.record_fh is not None:
            return {"ok": False, "error": "Already recording."}
        RECORDINGS.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = RECORDINGS / f"{self.source or 'session'}_{stamp}.ndjson"
        self.record_fh = path.open("w")
        self.record_path = path
        self.record_count = 0
        header = {"type": "recording_header", "started": stamp,
                  "source": self.source, "device": self.device,
                  "labels": self.labels}
        self.record_fh.write(json.dumps(header) + "\n")
        self.publish({"type": "recording", "state": "started", "path": str(path)})
        return {"ok": True, "path": str(path)}

    def stop_recording(self) -> dict:
        if self.record_fh is None:
            return {"ok": True, "note": "Not recording."}
        self.record_fh.close()
        self.record_fh = None
        path = self.record_path
        self.publish({"type": "recording", "state": "stopped",
                      "path": str(path), "records": self.record_count})
        return {"ok": True, "path": str(path), "records": self.record_count}

    # -- reader threads ------------------------------------------------------

    def _read_stdout(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.publish({"type": "log", "stream": "stdout", "text": line})
                continue
            self._handle(event)
        code = proc.wait()
        self.publish({"type": "session", "state": "exited", "exit_code": code})
        self.stop_recording()

    def _read_stderr(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if not line:
                continue
            lowered = line.lower()
            if "error" in lowered or "warn" in lowered:
                self.last_error = line
            self.publish({"type": "log", "stream": "stderr", "text": line})

    def _handle(self, event: dict) -> None:
        kind = event.get("type")

        if self.record_fh is not None:
            try:
                self.record_fh.write(json.dumps(event) + "\n")
                self.record_count += 1
            except Exception:
                pass

        if kind == "connected":
            self.device = event.get("device")
            chans = (self.device or {}).get("channels") or []
            self.labels = list(chans)
            self.publish({"type": "connected", "device": self.device,
                          "labels": self.labels})
        elif kind == "labels":
            self.labels = list(event.get("labels") or [])
            self.publish({"type": "labels", "labels": self.labels})
        elif kind == "sample":
            if self.first_sample_at is None:
                self.first_sample_at = time.time()
            data = event.get("data") or {}
            uv = data.get("eeg_uv") or []
            self.packets = event.get("packet", self.packets + 1)
            self._battery = data.get("battery_percent", self._battery)
            cq = data.get("contact_quality") or []
            self._pending.append(uv)
            if cq:
                self._pending_cq = list(cq)
        elif kind == "eeg":
            if self.first_sample_at is None:
                self.first_sample_at = time.time()
            samples = self._align(event.get("samples") or [])
            self.packets += 1
            self._pending.append(samples)
        elif kind == "dev":
            self._battery = event.get("battery_percent", self._battery)
            cq = event.get("contact_quality") or []
            if cq:
                self._pending_cq = [int(v) for v in cq]
        elif kind == "stats":
            self.stats = event
            self.publish(event)
        elif kind in ("end", "error", "warning", "status", "debug"):
            if kind == "error":
                self.last_error = event.get("message")
            self.publish(event)

    def _align(self, samples: list) -> list:
        """Match a Cortex `eeg` row to the label list.

        Cortex prepends COUNTER and INTERPOLATE and appends two marker
        columns, so a row can be four wider than the electrode list.
        """
        n = len(self.labels)
        if not n or len(samples) == n:
            return samples
        if len(samples) == n + 4:
            return samples[2:-2]
        return samples[:n]

    def _flush_loop(self) -> None:
        while self.is_running():
            time.sleep(FLUSH_INTERVAL_S)
            if not self._pending and not self._pending_cq:
                continue
            block, self._pending = self._pending, []
            cq, self._pending_cq = self._pending_cq, []
            # Timed from the first sample so the connection phase does not
            # drag the reported rate down.
            anchor = self.first_sample_at or self.started_at or time.time()
            elapsed = time.time() - anchor
            self.publish({
                "type": "block",
                "samples": block,
                "contact_quality": cq,
                "battery": self._battery,
                "packets": self.packets,
                "seconds": round(elapsed, 2),
                "rate_hz": round(self.packets / elapsed, 1) if elapsed > 0 else 0,
                "recording": self.record_fh is not None,
                "records": self.record_count,
            })

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "source": self.source,
            "device": self.device,
            "labels": self.labels,
            "packets": self.packets,
            "stats": self.stats,
            "last_error": self.last_error,
            "recording": self.record_fh is not None,
            "record_path": str(self.record_path) if self.record_path else None,
            "records": self.record_count,
            "binaries": {
                "emotiv-raw": find_binary("emotiv-raw"),
                "emotiv-cli": find_binary("emotiv-cli"),
            },
        }


SESSION = Session()


def discover(timeout: int = 25) -> dict:
    """Run `emotiv-raw --list --json` once and return the device list."""
    binary = find_binary("emotiv-raw")
    if not binary:
        return {"ok": False, "error": "emotiv-raw not found on PATH."}
    try:
        res = subprocess.run([binary, "--list", "--json"], capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Discovery timed out after {timeout}s."}
    devices, stderr_tail = [], (res.stderr or "").strip().splitlines()[-6:]
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "devices":
            devices = obj.get("devices") or []
    return {"ok": True, "devices": devices, "log": stderr_tail,
            "exit_code": res.returncode}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        elif self.path == "/app.js":
            self._send_file(STATIC / "app.js", "text/javascript; charset=utf-8")
        elif self.path == "/style.css":
            self._send_file(STATIC / "style.css", "text/css; charset=utf-8")
        elif self.path == "/api/status":
            self._send_json(SESSION.status())
        elif self.path == "/api/stream":
            self._stream()
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}

        if self.path == "/api/discover":
            self._send_json(discover())
        elif self.path == "/api/start":
            self._send_json(SESSION.start(body.get("source", "simulate"),
                                          body.get("device")))
        elif self.path == "/api/stop":
            self._send_json(SESSION.stop())
        elif self.path == "/api/record/start":
            self._send_json(SESSION.start_recording())
        elif self.path == "/api/record/stop":
            self._send_json(SESSION.stop_recording())
        else:
            self.send_error(404)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = SESSION.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    payload = q.get(timeout=10)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")  # hold the connection
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            SESSION.unsubscribe(q)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"Headset control panel:  http://{args.host}:{args.port}")
    print(f"  emotiv-raw : {find_binary('emotiv-raw')}")
    print(f"  emotiv-cli : {find_binary('emotiv-cli')}")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SESSION.stop()


if __name__ == "__main__":
    main()
