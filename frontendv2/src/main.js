import { initIntro } from "./intro.js";
import { createParcelMap } from "./map.js";
import { api, request } from "./api.ts";
const $ = (id) => document.getElementById(id);
const fmt = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 });
const today = new Date();
$("date").value = [
  today.getFullYear(),
  String(today.getMonth() + 1).padStart(2, "0"),
  String(today.getDate()).padStart(2, "0"),
].join("-");
let sid = sessionStorage.getItem("vekt.session"),
  state = "connecting",
  busy = false,
  ready = false;
let hasAnalysis = false,
  jobId = null,
  queueStream = null,
  jobStream = null,
  lastTouch = 0;
let deadline = null,
  syncing = false,
  connecting = false,
  answers = new Map(),
  generation = 0;
const map = createParcelMap(
  (value) => {
    ready = value;
    controls();
  },
  () => touch(),
);
initIntro(map);
function controls() {
  $("draw-field").disabled = busy;
  $("open-field").setAttribute("aria-disabled", String(busy));
  $("analyze").disabled =
    !ready || busy || state === "connecting" || state === "queued";
  $("date").disabled = busy;
  for (const el of $("ask-form").elements)
    el.disabled = busy || state !== "active";
  $("ask-form").hidden = !hasAnalysis;
}
function lock(value) {
  busy = value;
  map.lock(value);
  controls();
}
function status(key, label) {
  state = key;
  $("status").textContent = label;
  $("status").parentElement.dataset.state = key;
  $("reconnect").hidden = !["offline", "ended"].includes(key);
  controls();
}
function notice(message = "", keep = false) {
  $("notice").hidden = !message;
  $("notice").firstElementChild.textContent = message;
  $("keep").hidden = !keep;
}
function errorAt(id, message = "") {
  $(id).textContent = message;
  $(id).hidden = !message;
}
function stage(label) {
  $("stage").textContent = label;
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function clearResults() {
  for (const id of ["layers", "conversation", "result-context"])
    $(id).replaceChildren();
  errorAt("job-error");
  $("result-empty").hidden = false;
  answers.clear();
  hasAnalysis = false;
  stage("Awaiting analysis");
  controls();
}
function detach() {
  queueStream?.close();
  jobStream?.close();
  queueStream = jobStream = null;
}
function forget() {
  generation++;
  detach();
  sid = jobId = null;
  sessionStorage.removeItem("vekt.session");
  sessionStorage.removeItem("vekt.selection");
  deadline = null;
  clearResults();
  lock(false);
}
function setAgents(list) {
  const selected = $("agent").value;
  $("agent").replaceChildren(
    new Option("All analysts", ""),
    ...list.map((a) => new Option(a.name, a.id)),
  );
  if (list.some((a) => a.id === selected)) $("agent").value = selected;
}
function timeLeft(s) {
  deadline =
    s.idle_remaining == null ? null : Date.now() + s.idle_remaining * 1000;
}
function applySession(s) {
  if (s.status === "ended") {
    forget();
    status("ended", "Session ended");
    notice(
      s.reason === "idle"
        ? "Your session expired due to inactivity. Reconnect to continue."
        : "Session ended. Reconnect to continue.",
    );
    return;
  }
  if (s.status === "queued") {
    status("queued", "Queued · " + s.position);
    notice(
      "Queue position: " +
        s.position +
        ". Your session will open when the workspace is available.",
    );
    if (!queueStream) {
      queueStream = new EventSource(api + "/session/" + sid + "/events");
      queueStream.onmessage = (e) => applySession(JSON.parse(e.data));
      queueStream.onerror = () => {
        notice("Reconnecting to queue…");
        void sync();
      };
    }
    return;
  }
  queueStream?.close();
  queueStream = null;
  const previously = state;
  status("active", "Session active");
  timeLeft(s);
  if (
    previously !== "active" ||
    s.idle_remaining >= 60 ||
    s.idle_remaining == null
  )
    notice();
  if (s.has_analysis && !busy && !hasAnalysis) {
    hasAnalysis = true;
    controls();
  }
}
async function connect() {
  if (connecting) return false;
  connecting = true;
  status("connecting", "Connecting");
  notice();
  errorAt("input-error");
  try {
    const list = await request("/agents");
    setAgents(list);
    let s;
    if (sid) {
      try {
        s = await request("/session/" + sid);
      } catch (e) {
        if (e.status !== 404) throw e;
      }
      if (!s || s.status === "ended") {
        forget();
        s = null;
      }
    }
    if (!s) {
      s = await request("/session", "POST");
      sid = s.session_id;
      sessionStorage.setItem("vekt.session", sid);
    }
    applySession(s);
    if (s.job_id) {
      const stored = JSON.parse(
        sessionStorage.getItem("vekt.selection") || "null",
      );
      if (stored) {
        map.restore(stored.ring, stored.example);
        $("date").value = stored.date;
      }
      await restoreJob(s.job_id);
      if (!location.hash) location.hash = "workspace";
    }
    return state === "active";
  } catch {
    detach();
    lock(false);
    status("offline", "Service offline");
    return false;
  } finally {
    connecting = false;
  }
}
async function sync() {
  if (!sid || syncing || connecting) return;
  syncing = true;
  const current = sid;
  try {
    const s = await request("/session/" + current);
    if (sid !== current) return;
    applySession(s);
    if (s.status === "active" && s.job_id && s.job_id !== jobId && !busy)
      await restoreJob(s.job_id);
  } catch (e) {
    if (sid !== current) return;
    if (e.status === 404) applySession({ status: "ended" });
    else if (!busy) status("offline", "Disconnected");
  } finally {
    syncing = false;
  }
}
async function touch(force = false) {
  if (
    !sid ||
    state !== "active" ||
    busy ||
    (!force && Date.now() - lastTouch < 20000)
  )
    return;
  lastTouch = Date.now();
  try {
    await request("/session/" + sid + "/keepalive", "POST");
    await sync();
  } catch {
    await sync();
  }
}
$("keep").onclick = () => touch(true);
$("reconnect").onclick = () => connect();
document.addEventListener("input", () => touch());
document.addEventListener("pointerdown", () => touch(), { passive: true });
setInterval(() => {
  if (sid && state !== "ended") void sync();
}, 15000);
setInterval(() => {
  if (state === "active" && !busy && deadline !== null) {
    const sec = Math.ceil((deadline - Date.now()) / 1000);
    if (sec <= 0) {
      deadline = null;
      void sync();
    } else if (sec < 60)
      notice("Session expires in " + sec + " seconds.", true);
  }
}, 1000);

function renderLayers(layers, errors = []) {
  const container = $("layers");
  container.replaceChildren();
  if (layers.length) {
    const table = element("table");
    table.append(element("caption", "Field measurements"));
    const header = table.createTHead().insertRow();
    for (const label of ["Layer", "Mean", "Range", "Confidence"])
      header.append(element("th", label));
    const body = table.createTBody();
    for (const layer of layers) {
      const row = body.insertRow();
      const name = element("th", layer.label || layer.name);
      name.scope = "row";
      if (layer.note)
        name.append(
          element(
            "small",
            layer.note === "(sahte veri)" ? "Demo data" : layer.note,
          ),
        );
      const unit = layer.unit === "puan" ? "points" : layer.unit;
      row.append(name);
      row.insertCell().textContent = fmt.format(layer.mean) + " " + unit;
      row.insertCell().textContent =
        fmt.format(layer.min) + "–" + fmt.format(layer.max) + " " + unit;
      const label =
        { yüksek: "High", orta: "Moderate", düşük: "Low" }[
          layer.confidence_level
        ] || layer.confidence_level;
      const confidence = element(
        "span",
        label + " (" + fmt.format(layer.confidence * 100) + "%)",
        "level",
      );
      confidence.dataset.level = layer.confidence_level;
      row.insertCell().append(confidence);
    }
    container.append(table);
  }
  container.append(...errors.map((message) => element("p", message, "error")));
}
function round(list, question) {
  const wrap = element("section", null, "round"),
    grid = element("div", null, "agent-grid");
  answers = new Map();
  wrap.append(element("h3", question || "Analyst notes"), grid);
  for (const a of list) {
    const card = element("article", null, "card agent-card"),
      body = element("p", a.text || ""),
      note = element(
        "small",
        a.error || (a.status === "done" ? "Complete" : "Waiting for response…"),
      );
    card.append(
      element("h3", a.name),
      element("small", a.layers.join(" · ")),
      body,
      note,
    );
    grid.append(card);
    answers.set(a.id, { body, note });
  }
  $("conversation").append(wrap);
}
function context(parcel, date) {
  if (!parcel) return;
  $("result-context").textContent =
    "Analyzed field · " +
    fmt.format(parcel.area_ha) +
    " ha · " +
    date +
    " · " +
    parcel.centroid.map((v) => v.toFixed(4)).join(", ") +
    " (longitude, latitude)";
}
function finishJob(failed) {
  jobStream?.close();
  jobStream = null;
  lock(false);
  stage(failed ? "Job failed" : "Complete");
  if (failed) errorAt("job-error", failed);
  void sync();
}
function follow(id, after = -1) {
  jobStream?.close();
  const stream = new EventSource(
    api +
      "/jobs/" +
      id +
      "/events?session_id=" +
      encodeURIComponent(sid) +
      "&after=" +
      after,
  );
  jobStream = stream;
  stream.onmessage = ({ data }) => {
    if (stream !== jobStream) return;
    const e = JSON.parse(data);
    if (e.stage === "ml") {
      if (e.status === "started") {
        context(e.parcel, e.date);
        stage("Measuring field…");
      } else {
        renderLayers(e.layers, e.errors);
        stage("Preparing analysts…");
      }
    } else if (e.stage === "agents" || e.stage === "ask") {
      round(e.agents, e.message);
      stage("Analysts are writing…");
    } else if (e.agent != null) {
      const a = answers.get(e.agent);
      if (!a) return;
      if (e.delta) {
        a.body.append(document.createTextNode(e.delta));
        a.note.textContent = "Writing…";
      }
      if (e.done) a.note.textContent = "Complete";
      if (e.error) {
        a.note.textContent = e.error;
        a.note.className = "error";
      }
    } else if (e.error) finishJob(e.error);
    else if (e.done) {
      hasAnalysis = true;
      finishJob();
    }
  };
  stream.onerror = () => {
    if (stream !== jobStream) return;
    stage("Reconnecting to stream…");
    void sync();
  };
}
async function restoreJob(id) {
  const owner = sid,
    j = await request(
      "/jobs/" + id + "?session_id=" + encodeURIComponent(owner),
    );
  if (owner !== sid) return;
  jobId = id;
  $("conversation").replaceChildren();
  $("result-empty").hidden = true;
  context(j.parcel, j.date);
  if (j.kind === "analysis") renderLayers(j.layers, j.ml_errors);
  if (j.agents.length) round(j.agents, j.question);
  if (j.error) errorAt("job-error", j.error);
  lock(j.status === "running");
  if (busy) {
    stage(j.agents.length ? "Analysts are writing…" : "Measuring field…");
    follow(id, j.last_event_id);
  } else stage(j.status === "done" ? "Complete" : "Job failed");
}
async function submit(kind) {
  if (busy || state === "queued") return;
  const analysis = kind === "analysis",
    err = analysis ? "input-error" : "job-error";
  errorAt(err);
  if (state !== "active" && !(await connect())) {
    errorAt(
      err,
      state === "queued"
        ? "Try again when your session becomes active."
        : "Analysis service unavailable. Start the backend and try again.",
    );
    return;
  }
  if (busy) return;
  let payload = { session_id: sid };
  if (analysis) {
    if (!ready || !$("date").reportValidity()) return;
    payload = { ...payload, date: $("date").value, polygon: map.polygon() };
  } else {
    const message = $("message").value.trim();
    if (!message) {
      errorAt(err, "Enter a question.");
      return;
    }
    payload.message = message;
    if ($("agent").value) payload.agent = $("agent").value;
  }
  const epoch = generation;
  lock(true);
  try {
    const { job_id } = await request("/" + kind, "POST", payload);
    if (epoch !== generation) return;
    jobId = job_id;
    if (analysis) {
      clearResults();
      sessionStorage.setItem(
        "vekt.selection",
        JSON.stringify({
          ring: payload.polygon.coordinates[0].slice(0, -1),
          date: payload.date,
          example: map.isExample(),
        }),
      );
    } else $("message").value = "";
    $("result-empty").hidden = true;
    stage(analysis ? "Measuring field…" : "Preparing analysts…");
    $("results").scrollIntoView({ block: "nearest" });
    follow(jobId);
  } catch (e) {
    if (epoch !== generation) return;
    lock(false);
    errorAt(
      err,
      e.status ? e.message : "Request failed. Check your connection and retry.",
    );
    await sync();
  }
}
$("analyze").onclick = () => submit("analysis");
$("ask-form").onsubmit = (e) => {
  e.preventDefault();
  void submit("ask");
};

// A short grace period lets a reloaded tab reclaim its session before releasing the slot.
const workerURL = import.meta.env.BASE_URL + "session-worker.js";
if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register(workerURL)
    .then(() => navigator.serviceWorker.ready)
    .then((r) => {
      if (sid) r.active?.postMessage({ resume: sid });
    })
    .catch(() => {});
}
window.addEventListener("pagehide", (e) => {
  if (!sid || e.persisted) return;
  const url = new URL(api + "/session/" + sid + "/leave", location.href).href;
  if (navigator.serviceWorker?.controller)
    navigator.serviceWorker.controller.postMessage({ leave: sid, url });
  else navigator.sendBeacon(url);
});
window.addEventListener("pageshow", (e) => {
  if (e.persisted) void sync();
});
void connect();
