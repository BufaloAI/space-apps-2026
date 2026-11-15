import {createParcelMap} from '../map.js';
import {api,request} from './api.ts';
import {$,t,lang} from './ui.js';
const phrases={
  "Oturum kapandı":"Session ended","Oturum aktif":"Session active","Bağlanıyor":"Connecting","Servis çevrimdışı":"Service offline","Bağlantı kesildi":"Disconnected",
  "Analiz bekleniyor":"Awaiting analysis","Yazıyor…":"Writing…","Tamamlandı":"Complete","İş tamamlanamadı":"Job failed","Katmanlar hesaplanıyor…":"Computing layers…",
  "Uzmanlar hazırlanıyor…":"Preparing experts…","Uzmanlar yazıyor…":"Experts are writing…","Akışa yeniden bağlanılıyor…":"Reconnecting to stream…",
  "Uzman yorumları":"Expert commentary","Yanıt bekleniyor…":"Waiting for response…","Sıra bağlantısı yenileniyor…":"Reconnecting to queue…",
  "Bir soru yazın.":"Enter a question.","Sıranız geldiğinde tekrar deneyin.":"Try again when your session becomes active.",
  "Analiz servisine ulaşılamadı. Backend’i çalıştırıp tekrar deneyin.":"Analysis service unavailable. Start the backend and try again.",
  "İstek gönderilemedi. Bağlantıyı kontrol edip tekrar deneyin.":"Request failed. Check your connection and retry.",
  "Hareketsizlik süresi doldu. Yeni bir oturum açabilirsiniz.":"Your session expired due to inactivity. Reconnect to continue.",
  "Oturum sona erdi. Yeniden bağlanabilirsiniz.":"Session ended. Reconnect to continue."
};
function translate(s){return lang==='en'?(phrases[s]||s):s;}
export function initField(onLocation){
const fmt=new Intl.NumberFormat(lang,{maximumFractionDigits:3});
$("date").value="2025-12-31";
$("status").textContent=t("İstek bekleniyor","On demand");
let sid = sessionStorage.getItem("atlas.session"),
  state = "idle",
  busy = false,
  ready = false;
let hasAnalysis = false,
  agents = [],
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
  () => touch(), onLocation,
);
function controls() {
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
  $("status").textContent = translate(label);
  $("status").parentElement.dataset.state = key;
  $("reconnect").hidden = !["offline", "ended"].includes(key);
  controls();
}
function notice(message = "", keep = false) {
  $("notice").hidden = !message;
  $("notice").firstElementChild.textContent = translate(message);
  $("keep").hidden = !keep;
}
function errorAt(id, message = "") {
  $(id).textContent = translate(message);
  $(id).hidden = !message;
}
function stage(label) {
  $("stage").textContent = translate(label);
}
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = translate(text);
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
  stage("Analiz bekleniyor");
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
  sessionStorage.removeItem("atlas.session");
  sessionStorage.removeItem("atlas.selection");
  deadline = null;
  clearResults();
  lock(false);
}
function setAgents(list) {
  agents = list;
  const selected = $("agent").value;
  $("agent").replaceChildren(
    new Option(t("Tüm uzmanlar", "All experts"), ""),
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
    status("ended", "Oturum kapandı");
    notice(
      s.reason === "idle"
        ? "Hareketsizlik süresi doldu. Yeni bir oturum açabilirsiniz."
        : "Oturum sona erdi. Yeniden bağlanabilirsiniz.",
    );
    return;
  }
  if (s.status === "queued") {
    status("queued", t("Sırada · ", "Queued · ") + s.position);
    notice(
      t("Sıranız: ", "Queue position: ") +
        s.position +
        t(". Çalışma alanı boşaldığında oturumunuz otomatik açılacak.", ". Your session will open when the workspace is available."),
    );
    if (!queueStream) {
      queueStream = new EventSource(api + "/session/" + sid + "/events");
      queueStream.onmessage = (e) => applySession(JSON.parse(e.data));
      queueStream.onerror = () => {
        notice("Sıra bağlantısı yenileniyor…");
        void sync();
      };
    }
    return;
  }
  queueStream?.close();
  queueStream = null;
  const previously = state;
  status("active", "Oturum aktif");
  timeLeft(s);
  if (
    previously !== "active" ||
    s.idle_remaining >= 60 ||
    s.idle_remaining == null
  )
    notice();
  if (s.has_analysis && !busy) {
    hasAnalysis = true;
    controls();
  }
}
async function connect() {
  if (connecting) return false;
  connecting = true;
  status("connecting", "Bağlanıyor");
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
      sessionStorage.setItem("atlas.session", sid);
    }
    applySession(s);
    if (s.job_id) {
      const stored = JSON.parse(
        sessionStorage.getItem("atlas.selection") || "null",
      );
      if (stored) {
        map.restore(stored.ring, stored.example);
        $("date").value = stored.date;
      }
      await restoreJob(s.job_id);
    }
    return state === "active";
  } catch {
    detach();
    lock(false);
    status("offline", "Servis çevrimdışı");
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
    else if (!busy) status("offline", "Bağlantı kesildi");
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
      notice(t("Oturum " + sec + " saniye içinde kapanacak.", "Session expires in " + sec + " seconds."), true);
  }
}, 1000);

function renderLayers(layers, errors = []) {
  const cards = layers.map((l) => {
    const card = element("article", null, "card result-card");
    const confidence = {yüksek:"High",orta:"Medium",düşük:"Low"};
    const level = element("span", t(l.confidence_level + " eminlik", (confidence[l.confidence_level] || l.confidence_level) + " confidence"), "level");
    level.dataset.level = l.confidence_level;
    card.append(
      element("h3", l.label || l.name),
      level,
      element("strong", fmt.format(l.mean) + " " + l.unit),
      element(
        "small",
        t("Ortalama · Aralık ", "Mean · Range ") +
          fmt.format(l.min) +
          " – " +
          fmt.format(l.max) +
          " " +
          l.unit,
      ),
      element("small", t("Eminlik ","Confidence ") + fmt.format(l.confidence * 100) + "%"),
    );
    if (l.note) card.append(element("small", l.note.includes("sahte") ? t("Demo veri · örnek ML sağlayıcısı","Demo data · sample ML provider") : l.note));
    return card;
  });
  $("layers").replaceChildren(
    ...cards,
    ...errors.map((t) => element("p", t, "error")),
  );
}
function round(list, question) {
  const wrap = element("section", null, "round"),
    grid = element("div", null, "agent-grid");
  answers = new Map();
  wrap.append(element("h3", question || "Uzman yorumları"), grid);
  for (const a of list) {
    const card = element("article", null, "card agent-card"),
      body = element("p", a.text || ""),
      note = element(
        "small",
        a.error || (a.status === "done" ? "Tamamlandı" : "Yanıt bekleniyor…"),
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
    t("Analiz edilen parsel · ", "Analyzed field · ") +
    fmt.format(parcel.area_ha) +
    " ha · " +
    date +
    " · " +
    parcel.centroid.map((v) => v.toFixed(4)).join(", ") +
    t(" (boylam, enlem)", " (longitude, latitude)");
}
function finishJob(failed) {
  jobStream?.close();
  jobStream = null;
  lock(false);
  stage(failed ? "İş tamamlanamadı" : "Tamamlandı");
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
        stage("Katmanlar hesaplanıyor…");
      } else {
        renderLayers(e.layers, e.errors);
        stage("Uzmanlar hazırlanıyor…");
      }
    } else if (e.stage === "agents" || e.stage === "ask") {
      round(e.agents, e.message);
      stage("Uzmanlar yazıyor…");
    } else if (e.agent != null) {
      const a = answers.get(e.agent);
      if (!a) return;
      if (e.delta) {
        a.body.append(document.createTextNode(e.delta));
        a.note.textContent = t("Yazıyor…","Writing…");
      }
      if (e.done) a.note.textContent = t("Tamamlandı","Complete");
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
    stage("Akışa yeniden bağlanılıyor…");
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
  $("result-empty").hidden = true;
  context(j.parcel, j.date);
  if (j.kind === "analysis") renderLayers(j.layers, j.ml_errors);
  if (j.agents.length) round(j.agents, j.question);
  if (j.error) errorAt("job-error", j.error);
  lock(j.status === "running");
  if (busy) {
    stage(j.agents.length ? "Uzmanlar yazıyor…" : "Katmanlar hesaplanıyor…");
    follow(id, j.last_event_id);
  } else stage(j.status === "done" ? "Tamamlandı" : "İş tamamlanamadı");
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
        ? "Sıranız geldiğinde tekrar deneyin."
        : "Analiz servisine ulaşılamadı. Backend’i çalıştırıp tekrar deneyin.",
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
      errorAt(err, "Bir soru yazın.");
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
        "atlas.selection",
        JSON.stringify({
          ring: payload.polygon.coordinates[0].slice(0, -1),
          date: payload.date,
          example: map.isExample(),
        }),
      );
    } else $("message").value = "";
    $("result-empty").hidden = true;
    stage(analysis ? "Katmanlar hesaplanıyor…" : "Uzmanlar hazırlanıyor…");
    $("results").scrollIntoView({ block: "nearest" });
    follow(jobId);
  } catch (e) {
    if (epoch !== generation) return;
    lock(false);
    errorAt(
      err,
      e.status
        ? e.message
        : "İstek gönderilemedi. Bağlantıyı kontrol edip tekrar deneyin.",
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
if (sid) void connect();

return map;
}
