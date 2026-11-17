"use strict";
// Control panel front end. Avoids optional chaining so the file can be
// syntax-checked with the pure-Python esprima parser.

var BUF = 640;             // samples held per channel (5 s at 128 Hz)
var labels = [];
var ring = [];             // one Float32Array per channel
var head = 0;
var filled = 0;
var cq = [];
var running = false;
var everConnected = false;   // did any data or connect event arrive this session?
var recording = false;
var selectedDevice = null;
var linkLabel = null;        // how the headset is attached, for the status pill

var el = function (id) { return document.getElementById(id); };

// How the headset is reaching the computer, in plain words rather than the
// library's internal transport label.
function linkName(transport) {
  if (!transport) { return "\u2013"; }
  if (transport === "USB HID") { return "USB receiver (dongle)"; }
  if (transport === "USB Serial") { return "USB serial"; }
  if (transport === "BLE") { return "Bluetooth"; }
  return transport;
}



function setConn(state, text) {
  var n = el("conn");
  n.className = "pill pill-" + state;
  n.textContent = text;
}

function log(text, cls) {
  var pre = el("log");
  var stamp = new Date().toLocaleTimeString();
  var line = document.createElement("div");
  if (cls) { line.className = cls; }
  line.textContent = "[" + stamp + "] " + text;
  pre.appendChild(line);
  while (pre.childNodes.length > 400) { pre.removeChild(pre.firstChild); }
  pre.scrollTop = pre.scrollHeight;
}

function allocate(n) {
  ring = [];
  for (var i = 0; i < n; i++) { ring.push(new Float32Array(BUF)); }
  head = 0; filled = 0;
}

function setLabels(list) {
  labels = list || [];
  allocate(labels.length);
  var box = el("cq");
  box.innerHTML = "";
  for (var i = 0; i < labels.length; i++) {
    var d = document.createElement("div");
    d.className = "el";
    d.id = "el-" + i;
    d.innerHTML = '<div class="lbl">' + labels[i] + '</div><div class="val">&ndash;</div>';
    box.appendChild(d);
  }
}

function pushSamples(block) {
  if (!block.length) { return; }
  if (!ring.length && block[0]) { allocate(block[0].length); }
  for (var s = 0; s < block.length; s++) {
    var row = block[s];
    for (var c = 0; c < ring.length && c < row.length; c++) {
      ring[c][head] = row[c];
    }
    head = (head + 1) % BUF;
    if (filled < BUF) { filled++; }
  }
}

function updateCq(values) {
  // The headset's own contact-quality field is not decoded on this path, so
  // the tiles report a MEASURED quantity instead: each electrode's standard
  // deviation over the trace buffer. That answers the same practical question
  // - which electrode is badly seated - without inventing a value.
  cq = values;
}

function refreshSpread() {
  if (!ring.length || filled < 32) { return; }
  for (var c = 0; c < ring.length; c++) {
    var node = el("el-" + c);
    if (!node) { continue; }
    var buf = ring[c], n = filled, sum = 0, k, idx;
    for (k = 0; k < n; k++) {
      idx = (head - n + k + BUF * 2) % BUF;
      sum += buf[idx];
    }
    var mean = sum / n, ss = 0, d;
    for (k = 0; k < n; k++) {
      idx = (head - n + k + BUF * 2) % BUF;
      d = buf[idx] - mean;
      ss += d * d;
    }
    var sd = Math.sqrt(ss / n);
    // Bands chosen from measured data: a seated electrode on a resting head
    // sits around 10-50 uV; 302 uV was a visibly loose one.
    var band = sd < 2 ? 0 : sd < 10 ? 2 : sd <= 60 ? 4 : sd <= 150 ? 2 : 1;
    node.className = "el q" + band;
    node.lastChild.textContent = (sd < 100 ? sd.toFixed(1) : Math.round(sd)) + " \u00b5V";
  }
}

function fmt(v, digits) {
  if (v === null || v === undefined) { return "\u2013"; }
  return Number(v).toFixed(digits === undefined ? 1 : digits);
}

function onEvent(ev) {
  var kind = ev.type;
  if (kind === "connected") {
    everConnected = true;
    var dev = ev.device || {};
    var link = linkName(dev.transport);
    setLabels(ev.labels || dev.channels || []);
    setConn("live", (dev.model || "headset") + " via " + link);
    el("m-link").textContent = link;
    linkLabel = link;
    log("connected: " + (dev.model || "?") + " over " + link +
        "  serial " + (dev.serial || "?"));
  } else if (kind === "labels") {
    setLabels(ev.labels);
    log("channel labels: " + (ev.labels || []).join(", "));
  } else if (kind === "block") {
    if ((ev.samples || []).length) { everConnected = true; }
    pushSamples(ev.samples || []);
    updateCq(ev.contact_quality);
    el("m-packets").textContent = ev.packets;
    el("m-rate").textContent = fmt(ev.rate_hz) + " Hz";
    el("m-elapsed").textContent = fmt(ev.seconds) + " s";
    el("m-battery").textContent = ev.battery === null || ev.battery === undefined
      ? "\u2013" : ev.battery + "%";
    if (ev.recording) {
      el("btn-record").textContent = "Stop recording (" + ev.records + ")";
    }
    if (!running) {
      running = true;
      setConn("live", linkLabel ? "streaming via " + linkLabel : "streaming");
    }
  } else if (kind === "stats") {
    el("m-cont").textContent = fmt(ev.continuity, 3);
    el("m-clip").textContent = fmt(ev.clip_rate * 100, 1) + "%";
    el("m-rms").textContent = fmt(ev.rms_uv) + " \u00b5V";
    var v = el("m-verdict");
    v.textContent = ev.verdict;
    v.className = "v " + (ev.verdict === "GOOD" ? "good" : ev.verdict === "WARN" ? "warn" : "bad");
  } else if (kind === "session") {
    if (ev.state === "started" && ev.source === "simulate") {
      el("m-link").textContent = "simulator (no headset)";
    }
    log("session " + ev.state + (ev.command ? ": " + ev.command : "") +
        (ev.exit_code === undefined ? "" : " (exit " + ev.exit_code + ")"));
    if (ev.state === "stopped" || ev.state === "exited") {
      if (ev.state === "exited" && !everConnected) {
        setConn("err", "never connected");
        log("the stream never started: the binary exited without connecting to a headset. " +
            "On the Bluetooth path this means no headset was found.", "bad");
        var box = el("devices");
        if (box && !box.innerHTML) {
          box.innerHTML = '<div class="result none"><strong>The stream never started.</strong>' +
            '<div class="why">The binary exited without connecting. Press ' +
            "<em>Discover headsets</em> first and select one.</div></div>";
        }
      }
      running = false;
      everConnected = false;
      linkLabel = null;
      el("m-link").textContent = "\u2013";
      setConn("idle", "stopped");
      if (!(ev.state === "exited" && el("conn").textContent === "never connected")) {
        setConn("idle", "stopped");
      }
      el("btn-start").disabled = false;
      el("btn-stop").disabled = true;
      el("btn-record").disabled = true;
    }
  } else if (kind === "recording") {
    recording = ev.state === "started";
    var b = el("btn-record");
    b.className = recording ? "recording" : "";
    b.textContent = recording ? "Stop recording" : "Start recording";
    log("recording " + ev.state + ": " + ev.path +
        (ev.records === undefined ? "" : " (" + ev.records + " records)"));
  } else if (kind === "end") {
    running = false;
    linkLabel = null;
    setConn("idle", "stopped");
    el("m-link").textContent = "\u2013";
    log("stream ended: " + ev.packets + " packets in " + fmt(ev.seconds) +
        " s (" + fmt(ev.rate_hz) + " Hz)");
  } else if (kind === "error") {
    setConn("err", "error");
    log("ERROR: " + ev.message, "bad");
  } else if (kind === "warning") {
    log("warning " + ev.code + ": " + ev.message, "warn");
  } else if (kind === "log") {
    log(ev.stream + ": " + ev.text);
  } else if (kind === "status") {
    log("status: " + ev.event);
  }
}

// ---- drawing ---------------------------------------------------------------

function draw() {
  var cv = el("traces");
  var ctx = cv.getContext("2d");
  var W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);

  var n = ring.length;
  if (!n || !filled) {
    ctx.fillStyle = "#8e99ad";
    ctx.font = "14px -apple-system, Helvetica, sans-serif";
    ctx.fillText("No samples yet \u2014 choose a source and press Start.", 18, 30);
    requestAnimationFrame(draw);
    return;
  }

  var scaleSel = el("scale").value;
  var detrend = el("detrend").checked;
  var lane = H / n;
  var left = 54;
  var plotW = W - left - 10;

  for (var c = 0; c < n; c++) {
    var buf = ring[c];
    var mean = 0;
    if (detrend) {
      for (var k = 0; k < filled; k++) { mean += buf[k]; }
      mean /= filled;
    }
    var amp;
    if (scaleSel === "auto") {
      var mx = 1e-6;
      for (var k2 = 0; k2 < filled; k2++) {
        var a = Math.abs(buf[k2] - mean);
        if (a > mx) { mx = a; }
      }
      amp = mx;
    } else {
      amp = parseFloat(scaleSel);
    }

    var y0 = lane * c + lane / 2;
    ctx.strokeStyle = "#232a36";
    ctx.beginPath(); ctx.moveTo(left, y0); ctx.lineTo(W - 10, y0); ctx.stroke();

    ctx.fillStyle = "#8e99ad";
    ctx.font = "11px ui-monospace, Menlo, monospace";
    ctx.fillText(labels[c] || ("ch" + c), 8, y0 + 4);

    ctx.strokeStyle = "#4da3ff";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (var i = 0; i < filled; i++) {
      var idx = (head - filled + i + BUF * 2) % BUF;
      var val = buf[idx] - mean;
      var y = y0 - (val / amp) * (lane * 0.45);
      if (y < lane * c) { y = lane * c; }
      if (y > lane * (c + 1)) { y = lane * (c + 1); }
      var x = left + (i / (filled - 1 || 1)) * plotW;
      if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
    }
    ctx.stroke();
  }
  requestAnimationFrame(draw);
}

setInterval(refreshSpread, 1000);

// ---- controls --------------------------------------------------------------

function post(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {})
  }).then(function (r) { return r.json(); });
}

function currentSource() {
  var radios = document.getElementsByName("source");
  for (var i = 0; i < radios.length; i++) {
    if (radios[i].checked) { return radios[i].value; }
  }
  return "simulate";
}

el("btn-discover").onclick = function () {
  var b = el("btn-discover");
  b.disabled = true; b.textContent = "Scanning\u2026";
  log("scanning for headsets over Bluetooth Low Energy\u2026");
  post("/api/discover").then(function (res) {
    b.disabled = false; b.textContent = "Discover headsets";
    var box = el("devices");
    box.innerHTML = "";
    if (!res.ok) {
      box.innerHTML = '<div class="result none"><strong>Discovery failed.</strong>' +
        '<div class="why">' + res.error + "</div></div>";
      log("discovery failed: " + res.error, "bad");
      return;
    }
    var list = res.devices || [];
    if (!list.length) {
      box.innerHTML = '<div class="result none">' +
        "<strong>Nothing found.</strong>" +
        '<div class="why">The Bluetooth scan completed and found nothing advertising, and no ' +
        "Emotiv USB receiver is attached. Check the headset is switched on and charged, and " +
        "that the receiver is in a USB port.</div></div>";
      log("nothing found: 0 Bluetooth advertisements and no USB receiver attached", "warn");
      return;
    }
    box.innerHTML = '<div class="result found">Found ' + list.length +
      " headset(s). Click one to select it, then press Start.</div>";
    list.forEach(function (d, i) {
      var node = document.createElement("div");
      node.className = "device";
      // The USB receiver reports no battery of its own; the headset's level
      // only arrives once packets start, so showing "0%" would be a lie.
      var batt = d.transport === "USB HID"
        ? "battery reported once streaming"
        : "battery " + d.battery_percent + "%";
      node.innerHTML = '<div class="n">' + d.model + " \u2014 " + d.serial +
        ' <span class="tag">' + d.transport + "</span></div>" +
        '<div class="d">' + d.name + "  &middot;  " + batt +
        "  &middot;  " + d.channel_count + " channels</div>";
      node.onclick = function () {
        selectedDevice = d.serial || d.address || d.ble_id;
        var all = box.getElementsByClassName("device");
        for (var k = 0; k < all.length; k++) { all[k].className = "device"; }
        node.className = "device sel";
        log("selected " + selectedDevice);
      };
      box.appendChild(node);
      if (i === 0) { node.onclick(); }
    });
    log("found " + list.length + " headset(s)");
  });
};

el("btn-start").onclick = function () {
  var src = currentSource();
  if (src === "raw" && !selectedDevice) {
    log("no headset selected - the binary will scan and take the first it finds. " +
        "Press Discover headsets first to choose explicitly.", "warn");
  }
  post("/api/start", { source: src, device: src === "raw" ? selectedDevice : null })
    .then(function (res) {
      if (!res.ok) { log("start failed: " + res.error, "bad"); setConn("err", "error"); return; }
      el("cmdline").textContent = "$ " + res.command;
      el("btn-start").disabled = true;
      el("btn-stop").disabled = false;
      el("btn-record").disabled = false;
      setConn("live", "starting\u2026");
    });
};

el("btn-stop").onclick = function () { post("/api/stop"); };

el("btn-record").onclick = function () {
  post(recording ? "/api/record/stop" : "/api/record/start").then(function (res) {
    if (!res.ok) { log("recording: " + res.error, "warn"); }
  });
};

// ---- event stream ----------------------------------------------------------

function connect() {
  var es = new EventSource("/api/stream");
  es.onopen = function () { log("connected to server event stream"); };
  es.onmessage = function (m) {
    try { onEvent(JSON.parse(m.data)); } catch (e) { log("bad event: " + m.data, "warn"); }
  };
  es.onerror = function () {
    setConn("err", "server unreachable");
    es.close();
    setTimeout(connect, 2000);
  };
}

fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
  log("emotiv-raw: " + (s.binaries["emotiv-raw"] || "NOT FOUND"));
  log("emotiv-cli: " + (s.binaries["emotiv-cli"] || "NOT FOUND"));
  if (s.labels && s.labels.length) { setLabels(s.labels); }
  if (s.running) {
    running = true;
    setConn("live", "streaming");
    el("btn-start").disabled = true;
    el("btn-stop").disabled = false;
    el("btn-record").disabled = false;
  }
});

connect();
requestAnimationFrame(draw);
