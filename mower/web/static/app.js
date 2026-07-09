// Mower control UI — vanilla JS.
//
// Wire up:
//   - the STOP button and every [data-cmd] button to POST /api/cmd/<name>
//   - the set-time controls to POST /api/set-time
//   - a WebSocket subscription to /api/telemetry that updates the "latest"
//     panel and appends to the live feed

const $ = (id) => document.getElementById(id);
const feed = $("feed");
const latestCodename = document.querySelector("#latest .codename");
const latestHex = document.querySelector("#latest .hex");
const connDot = $("conn");
const ipLabel = $("ip");
const lastTs = $("last-ts");

const FEED_MAX_LINES = 200;
const STALE_AFTER_SEC = 60;     // amber after 60s without a fresh state reply
const VERY_STALE_AFTER_SEC = 600;  // red after 10 minutes

const batteryV = $("battery-v");
const batteryAge = $("battery-age");
const stateLabel = $("state-label");
const alertBanner = $("alert-banner");
const mowerDot = $("device-mower-dot");
const mowerStatus = $("device-mower-status");
const piDot = $("device-pi-dot");
const piStatus = $("device-pi-status");
let lastStateTs = null;         // ISO string of last state-bearing sample
let lastMowerOkTs = null;
let lastMowerError = null;

async function sendCmd(name) {
  flash(`> ${name}`);
  try {
    const r = await fetch(`/api/cmd/${name}`, { method: "POST" });
    if (!r.ok) {
      const err = await r.text();
      flash(`! ${name}: ${err}`, "err");
      return;
    }
    const data = await r.json();
    flash(`< ${name}: ${data.replies.length} reply`, "ok");
  } catch (e) {
    flash(`! ${name}: ${e}`, "err");
  }
}

async function setTime(iso) {
  const body = iso ? { datetime: iso } : {};
  flash(`> set-time ${iso ?? "(now)"}`);
  try {
    const r = await fetch("/api/set-time", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      flash(`! set-time: ${await r.text()}`, "err");
      return;
    }
    flash(`< set-time ok`, "ok");
  } catch (e) {
    flash(`! set-time: ${e}`, "err");
  }
}

function flash(msg, klass = "") {
  const line = document.createElement("div");
  line.textContent = `${ts()} ${msg}`;
  if (klass) line.className = klass;
  feed.appendChild(line);
  while (feed.childElementCount > FEED_MAX_LINES) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function ts() {
  return new Date().toLocaleTimeString();
}

function bindButtons() {
  $("stop").addEventListener("click", () => sendCmd("stop"));
  document.querySelectorAll("[data-cmd]").forEach((b) => {
    b.addEventListener("click", () => sendCmd(b.dataset.cmd));
  });
  $("set-time-now").addEventListener("click", () => setTime(null));
  $("set-time-pick").addEventListener("click", () => {
    const v = $("set-time-dt").value;
    if (!v) {
      flash("! pick a datetime first", "err");
      return;
    }
    setTime(v);
  });
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/api/telemetry`);
  ws.addEventListener("open", () => {
    connDot.classList.remove("dot-bad");
    connDot.classList.add("dot-good");
    flash("ws connected", "ok");
  });
  ws.addEventListener("close", () => {
    connDot.classList.remove("dot-good");
    connDot.classList.add("dot-bad");
    flash("ws closed — retrying in 2s", "err");
    setTimeout(connectWS, 2000);
  });
  ws.addEventListener("error", () => {
    // close handler fires too; nothing to do here
  });
  ws.addEventListener("message", (ev) => {
    let sample;
    try { sample = JSON.parse(ev.data); } catch { return; }
    onSample(sample);
  });
}

function setDotState(el, state) {
  el.classList.remove("dot-good", "dot-bad");
  if (state === "good") el.classList.add("dot-good");
  if (state === "bad") el.classList.add("dot-bad");
}

function updateMowerChip(ok, text) {
  setDotState(mowerDot, ok ? "good" : "bad");
  mowerStatus.textContent = text;
}

function updatePiChip(state, text) {
  setDotState(piDot, state);
  piStatus.textContent = text;
}

function onSample(s) {
  if (s.codename === "ERROR") {
    lastMowerError = s.fields?.error ?? "unknown error";
    updateMowerChip(false, `offline (${lastMowerError})`);
    flash(`error: ${s.fields?.error ?? "?"}`, "err");
    return;
  }
  lastMowerOkTs = s.ts || new Date().toISOString();
  lastMowerError = null;
  updateMowerChip(true, `online (last reply ${lastMowerOkTs})`);
  latestCodename.textContent = s.codename;
  latestHex.textContent = s.binary_hex ?? "(no binary)";
  lastTs.textContent = s.ts ?? "";

  if (s.decoded) {
    if (s.decoded.voltage_v !== undefined) {
      batteryV.textContent = `${s.decoded.voltage_v.toFixed(2)} V`;
    }
    if (s.decoded.state !== undefined) {
      stateLabel.textContent = s.decoded.state;
    }
    lastStateTs = s.ts;
    refreshBatteryAge();
  }
  if (s.alert) updateAlert(s.alert);

  // Feed line: timestamp + codename + first 16 hex chars + decoded summary
  const head = (s.binary_hex ?? "").slice(0, 32);
  const dec = s.decoded ? ` [${Object.entries(s.decoded)
    .map(([k, v]) => `${k}=${v}`).join(" ")}]` : "";
  flash(`${s.codename} ${head}${dec}`);
}

function refreshBatteryAge() {
  if (!lastStateTs) {
    batteryAge.textContent = "";
    batteryV.className = "";
    return;
  }
  const ageSec = Math.max(0, (Date.now() - Date.parse(lastStateTs)) / 1000);
  batteryAge.textContent = `(${formatAge(ageSec)} ago)`;
  batteryV.className = ageSec > VERY_STALE_AFTER_SEC ? "bad"
                     : ageSec > STALE_AFTER_SEC ? "stale"
                     : "";
}

function formatAge(sec) {
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${Math.round(sec / 3600)}h`;
}

setInterval(refreshBatteryAge, 1000);

// --- Pi-side panels (camera + IMU) ----------------------------------------
// Only activated if /api/status returned a pi_url. Talks directly to the Pi
// (CORS is open on that side). Phase 2 will add an MJPEG "live" option.

let piUrl = null;
let camTimerId = null;
let camIntervalSec = 30;
let imuTimerId = null;
let videoStatusTimerId = null;
let camObjectUrl = null;
let piCameraOk = false;
let piImuOk = false;
let piVideoRunning = false;

function updatePiChipFromSignals() {
  if (!piUrl) {
    updatePiChip("", "not configured (start with --pi-url)");
    return;
  }
  if (piCameraOk || piImuOk) {
    const details = [];
    details.push(piCameraOk ? "camera ok" : "camera down");
    details.push(piImuOk ? "imu ok" : "imu down");
    updatePiChip("good", `reachable (${details.join(", ")})`);
  } else {
    updatePiChip("bad", "unreachable");
  }
}

function setupPi(url) {
  if (!url) {
    updatePiChipFromSignals();
    return;
  }
  piUrl = url.replace(/\/$/, "");

  document.getElementById("pi-camera-section").hidden = false;
  document.getElementById("pi-imu-section").hidden = false;

  const sel = document.getElementById("cam-interval");
  sel.addEventListener("change", () => {
    camIntervalSec = parseInt(sel.value, 10);
    restartCameraTimer();
  });
  $("cam-refresh").addEventListener("click", refreshCamera);
  $("video-start").addEventListener("click", startVideo);
  $("video-stop").addEventListener("click", stopVideo);

  restartCameraTimer();
  refreshCamera();
  refreshVideoStatus();
  if (!videoStatusTimerId) {
    videoStatusTimerId = setInterval(refreshVideoStatus, 3000);
  }

  imuTimerId = setInterval(pollImu, 2000);
  pollImu();
}

function setVideoStatus(text) {
  $("video-status").textContent = text;
}

function setVideoVisible(on) {
  const img = $("video-img");
  if (on) {
    if (!img.src) {
      img.src = `${piUrl}/live.mjpg?t=${Date.now()}`;
    }
    img.hidden = false;
  } else {
    img.hidden = true;
    img.removeAttribute("src");
  }
}

async function startVideo() {
  if (!piUrl) return;
  const seconds = parseInt($("video-seconds").value, 10);
  const size = $("video-size").value || "960x540";
  const [widthStr, heightStr] = size.split("x");
  const width = parseInt(widthStr, 10);
  const height = parseInt(heightStr, 10);
  const fps = parseFloat($("video-fps").value || "8");
  const params = new URLSearchParams({
    seconds: String(seconds),
    width: String(width),
    height: String(height),
    fps: String(fps),
  });
  setVideoStatus("starting...");
  try {
    const r = await fetch(
      `${piUrl}/api/camera/live/start?${params.toString()}`,
      { method: "POST" },
    );
    const data = await r.json();
    if (!r.ok || data.last_error) {
      throw new Error(data.last_error || `HTTP ${r.status}`);
    }
    piVideoRunning = !!data.running;
    setVideoVisible(piVideoRunning);
    if (piVideoRunning) {
      const remain = data.seconds_remaining;
      const ttl = remain == null ? "until stop" : `${Math.ceil(remain)}s left`;
      setVideoStatus(`running (${ttl})`);
    } else {
      setVideoStatus("not running");
    }
  } catch (e) {
    piVideoRunning = false;
    setVideoVisible(false);
    setVideoStatus(`start failed: ${e.message || e}`);
  }
}

async function stopVideo() {
  if (!piUrl) return;
  setVideoStatus("stopping...");
  try {
    await fetch(`${piUrl}/api/camera/live/stop`, { method: "POST" });
  } catch (e) {
    // Even on network error we force-hide locally so UI doesn't look stuck.
  }
  piVideoRunning = false;
  setVideoVisible(false);
  setVideoStatus("stopped");
}

async function refreshVideoStatus() {
  if (!piUrl) return;
  try {
    const r = await fetch(`${piUrl}/api/camera/live/status`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const s = await r.json();
    piVideoRunning = !!s.running;
    if (s.last_error && !s.running) {
      setVideoStatus(`error: ${s.last_error}`);
      setVideoVisible(false);
      return;
    }
    if (s.running) {
      const remain = s.seconds_remaining;
      const ttl = remain == null ? "until stop" : `${Math.ceil(remain)}s left`;
      setVideoStatus(`running (${ttl})`);
      setVideoVisible(true);
    } else {
      setVideoStatus("idle");
      setVideoVisible(false);
    }
  } catch (e) {
    setVideoStatus(`unreachable: ${e.message || e}`);
    setVideoVisible(false);
  }
}

async function refreshCamera() {
  if (!piUrl) return;
  const img = $("cam-img");
  const status = $("cam-status");
  status.textContent = "loading...";
  try {
    // Fetch first so we can surface HTTP errors (404 = no snapshots yet).
    const resp = await fetch(`${piUrl}/latest.jpg?t=${Date.now()}`, {
      cache: "no-store",
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    if (!blob.type.startsWith("image/")) {
      throw new Error("not an image payload");
    }
    if (camObjectUrl) URL.revokeObjectURL(camObjectUrl);
    camObjectUrl = URL.createObjectURL(blob);
    img.src = camObjectUrl;
    img.hidden = false;
    status.textContent = `updated ${new Date().toLocaleTimeString()}`;
    piCameraOk = true;
  } catch (e) {
    img.hidden = true;
    img.removeAttribute("src");
    status.textContent = `camera unavailable (${e.message || e})`;
    piCameraOk = false;
  }
  updatePiChipFromSignals();
}

function restartCameraTimer() {
  if (camTimerId) { clearInterval(camTimerId); camTimerId = null; }
  if (camIntervalSec > 0) {
    camTimerId = setInterval(refreshCamera, camIntervalSec * 1000);
  }
}

async function pollImu() {
  if (!piUrl) return;
  const status = $("imu-status");
  try {
    const r = await fetch(`${piUrl}/api/imu/recent?seconds=60`);
    if (!r.ok) throw new Error(r.statusText || r.status);
    const samples = await r.json();
    drawImuChart(samples);
    piImuOk = true;
    if (samples.length > 0) {
      const last = samples[samples.length - 1];
      const mag = Math.sqrt(last.ax * last.ax + last.ay * last.ay + last.az * last.az);
      status.textContent =
        `${samples.length} samples · |a| = ${mag.toFixed(2)} m/s²`;
    } else {
      status.textContent = "no samples yet";
    }
  } catch (e) {
    piImuOk = false;
    status.textContent = `unreachable: ${e.message || e}`;
  }
  updatePiChipFromSignals();
}

function drawImuChart(samples) {
  const line = document.getElementById("imu-line");
  if (samples.length < 2) {
    line.setAttribute("points", "");
    return;
  }
  const W = 300, H = 80;
  const Y_MAX = 14;  // m/s²; gravity (~9.8) sits visibly mid-chart
  const pts = samples.map((s, i) => {
    const m = Math.sqrt(s.ax * s.ax + s.ay * s.ay + s.az * s.az);
    const x = (i / (samples.length - 1)) * W;
    const y = H - Math.min(1, m / Y_MAX) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  line.setAttribute("points", pts.join(" "));
}

function updateAlert(alert) {
  if (!alert) return;
  if (alert.alert_active) {
    const mins = Math.floor(alert.duration_sec / 60);
    const secs = Math.floor(alert.duration_sec % 60);
    alertBanner.textContent =
      `⚠ Mower stuck — ${alert.state} for ${mins}m ${secs}s. Check it.`;
    alertBanner.hidden = false;
    document.title = "⚠ MOWER STUCK";
  } else {
    alertBanner.hidden = true;
    document.title = "Mower control";
  }
}

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    const data = await r.json();
    ipLabel.textContent = data.ip ?? "";
    updateAlert(data.alert);
    updateMowerChip(false, "waiting for first poll");
    updatePiChipFromSignals();
    setupPi(data.pi_url);
    // Even if the WS pushes updates fine, poll /api/status as a fallback so
    // the UI keeps refreshing if WS fails. Cheap, ~one request every 10 s.
    if (!window._statusPoller) {
      window._statusPoller = setInterval(refreshStatus, 10000);
    }
    if (data.last_state && data.last_state_ts) {
      if (data.last_state.voltage_v !== undefined) {
        batteryV.textContent = `${data.last_state.voltage_v.toFixed(2)} V`;
      }
      if (data.last_state.state !== undefined) {
        stateLabel.textContent = data.last_state.state;
      }
      lastStateTs = data.last_state_ts;
      refreshBatteryAge();
    }
    if (data.last_sample) onSample(data.last_sample);
  } catch (e) {
    flash(`! status: ${e}`, "err");
    updateMowerChip(false, "status endpoint unreachable");
  }
}

async function refreshStatus() {
  try {
    const r = await fetch("/api/status");
    const data = await r.json();
    updateAlert(data.alert);
    if (data.last_state?.voltage_v !== undefined && data.last_state_ts) {
      batteryV.textContent = `${data.last_state.voltage_v.toFixed(2)} V`;
      if (data.last_state.state !== undefined) {
        stateLabel.textContent = data.last_state.state;
      }
      lastStateTs = data.last_state_ts;
      refreshBatteryAge();
    }
    if (data.last_sample) {
      onSample(data.last_sample);
    }
  } catch (e) {
    // Silent; we'll retry on the next interval.
  }
}

bindButtons();
loadStatus();
connectWS();
