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

function onSample(s) {
  if (s.codename === "ERROR") {
    flash(`error: ${s.fields?.error ?? "?"}`, "err");
    return;
  }
  latestCodename.textContent = s.codename;
  latestHex.textContent = s.binary_hex ?? "(no binary)";
  lastTs.textContent = s.ts ?? "";
  // Feed line: timestamp + codename + first 16 hex chars
  const head = (s.binary_hex ?? "").slice(0, 32);
  flash(`${s.codename} ${head}`);
}

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    const data = await r.json();
    ipLabel.textContent = data.ip ?? "";
    if (data.last_sample) onSample(data.last_sample);
  } catch (e) {
    flash(`! status: ${e}`, "err");
  }
}

bindButtons();
loadStatus();
connectWS();
