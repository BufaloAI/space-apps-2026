import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import trim_history

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[29.0, 41.0], [29.01, 41.0], [29.01, 41.01], [29.0, 41.01], [29.0, 41.0]]],
}


class RecordingLLM:
    """Her bota kendi system prompt'una göre ayırt edilebilir cevap verir."""

    def __init__(self, fail_for=None):
        self.calls = []
        self.fail_for = fail_for

    @staticmethod
    def who(messages):
        return "A" if "Yağış Analisti" in messages[0]["content"] else "B"

    async def stream(self, messages):
        self.calls.append(messages)
        if self.who(messages) == self.fail_for:
            raise RuntimeError("gizli iç hata 10.0.0.5")
        n = sum(1 for m in messages if m["role"] == "assistant") + 1  # kaçıncı cevap
        for part in (f"{self.who(messages)}{n}-bir ", f"{self.who(messages)}{n}-iki"):
            yield part

    async def close(self):
        pass


class HangingLLM(RecordingLLM):
    async def stream(self, messages):
        self.calls.append(messages)
        await asyncio.sleep(3600)
        yield ""


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    monkeypatch.setenv("ML_FAKE_DELAY_SECONDS", "0")
    from app.main import app

    with TestClient(app) as c:
        c.app.state.llm = RecordingLLM()
        yield c


def new_session(client):
    return client.post("/api/session").json()["session_id"]


def post_analysis(client, sid, polygon=SQUARE, date="2026-05-01"):
    return client.post("/api/analysis", json={"session_id": sid, "polygon": polygon, "date": date})


def read_events(client, job_id, sid, params=None, headers=None):
    """SSE'yi iş bitene kadar okur -> [(id, olay), ...]"""
    out, cur = [], None
    with client.stream("GET", f"/api/jobs/{job_id}/events",
                       params={"session_id": sid, **(params or {})}, headers=headers) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("id: "):
                cur = int(line[4:])
            elif line.startswith("data: "):
                out.append((cur, json.loads(line[6:])))
    return out


def analyze(client, sid, **kw):
    r = post_analysis(client, sid, **kw)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    return job_id, read_events(client, job_id, sid)


def ask(client, sid, message="peki ya yarın?", agent=None):
    body = {"session_id": sid, "message": message, **({"agent": agent} if agent else {})}
    r = client.post("/api/ask", json=body)
    assert r.status_code == 202, r.text
    return r.json()["job_id"], read_events(client, r.json()["job_id"], sid)


def wait_until(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def texts(events):
    out = {}
    for _, e in events:
        if "delta" in e:
            out[e["agent"]] = out.get(e["agent"], "") + e["delta"]
    return out


# --- analiz --------------------------------------------------------------
def test_agents_endpoint(client):
    agents = client.get("/api/agents").json()
    assert [a["id"] for a in agents] == ["a", "b"]
    assert "system_prompt" not in agents[0]


def test_full_analysis_flow(client):
    sid = new_session(client)
    _, evs = analyze(client, sid)
    events = [e for _, e in evs]
    stages = [e for e in events if "stage" in e]
    assert stages[0]["stage"] == "ml" and stages[0]["status"] == "started"
    assert 92 < stages[0]["parcel"]["area_ha"] < 94  # enlem 41: 840 m x 1105 m
    assert {layer["name"] for layer in stages[1]["layers"]} == {"yagis", "toprak", "nem"}
    assert stages[2]["stage"] == "agents"
    assert texts(evs) == {"a": "A1-bir A1-iki", "b": "B1-bir B1-iki"}
    assert {e["agent"] for e in events if e.get("done") and "agent" in e} == {"a", "b"}
    assert events[-1] == {"done": True}


def test_each_agent_sees_only_its_own_layers(client):
    sid = new_session(client)
    analyze(client, sid)
    by = {RecordingLLM.who(m): m[1]["content"] for m in client.app.state.llm.calls}
    assert "Yağış" in by["A"] and "Toprak" not in by["A"] and "nem" not in by["A"].lower()
    assert "Toprak verimlilik" in by["B"] and "Toprak nemi" in by["B"] and "Yağış" not in by["B"]
    assert "uydurma" in client.app.state.llm.calls[0][0]["content"]
    assert "eminlik" in by["A"] and ("yüksek" in by["A"] or "orta" in by["A"] or "düşük" in by["A"])


def test_event_ids_and_resume_after_disconnect(client):
    sid = new_session(client)
    job_id, full = analyze(client, sid)
    assert [i for i, _ in full] == list(range(len(full)))
    k = 4
    tail = read_events(client, job_id, sid, params={"after": k})
    assert tail == full[k + 1:]
    via_header = read_events(client, job_id, sid, headers={"Last-Event-ID": str(k)})
    assert via_header == full[k + 1:]
    assert read_events(client, job_id, sid, params={"after": len(full) + 10}) == []


def test_snapshot_rebuilds_the_screen_after_reload(client):
    sid = new_session(client)
    job_id, evs = analyze(client, sid)
    snap = client.get(f"/api/jobs/{job_id}", params={"session_id": sid}).json()
    assert snap["status"] == "done" and snap["kind"] == "analysis" and snap["error"] is None
    assert snap["last_event_id"] == len(evs) - 1
    assert len(snap["layers"]) == 3 and snap["parcel"]["area_ha"] > 0 and snap["date"] == "2026-05-01"
    assert {a["id"]: a["text"] for a in snap["agents"]} == {"a": "A1-bir A1-iki", "b": "B1-bir B1-iki"}
    assert all(a["status"] == "done" for a in snap["agents"])
    st = client.get(f"/api/session/{sid}").json()
    assert st["job_id"] == job_id and st["has_analysis"] is True  # yenilenen sayfa işi buradan bulur


def test_one_agent_failing_does_not_break_the_other(client):
    client.app.state.llm = RecordingLLM(fail_for="A")
    sid = new_session(client)
    job_id, evs = analyze(client, sid)
    events = [e for _, e in evs]
    assert {"agent": "a", "error": "Model sunucusuna ulaşılamadı."} in events
    assert "10.0.0.5" not in json.dumps(events)
    assert {"agent": "b", "done": True} in events and events[-1] == {"done": True}
    snap = client.get(f"/api/jobs/{job_id}", params={"session_id": sid}).json()
    assert {a["id"]: a["status"] for a in snap["agents"]} == {"a": "error", "b": "done"}


@pytest.mark.parametrize("polygon", [
    {"type": "Point", "coordinates": [1, 2]},
    {"type": "Polygon", "coordinates": [[[0, 0], [0.01, 0.01], [0.01, 0], [0, 0.01], [0, 0]]]},
])
def test_bad_polygon_gets_422_and_starts_nothing(client, polygon):
    sid = new_session(client)
    r = post_analysis(client, sid, polygon=polygon)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert client.app.state.llm.calls == []
    assert client.app.state.sessions.active.job is None and client.app.state.sessions.active.busy == 0


@pytest.mark.parametrize("date", ["2026-13-45", "yarin", "1999-01-01", "2101-01-01"])
def test_bad_date_gets_422(client, date):
    assert post_analysis(client, new_session(client), date=date).status_code == 422


def test_queued_and_unknown_sessions_cannot_analyze(client):
    new_session(client)
    queued = new_session(client)
    r = post_analysis(client, queued)
    assert r.status_code == 409 and r.json()["detail"]["status"] == "queued"
    assert post_analysis(client, "yok").status_code == 404


def test_ml_total_failure_reports_error_and_frees_the_session(client):
    class Boom:
        name = "boom"

        async def analyze(self, parcel, date):
            raise RuntimeError("x")

    client.app.state.ml_providers = [Boom()]
    sid = new_session(client)
    job_id, evs = analyze(client, sid)
    assert evs[-1][1] == {"error": "Analiz sonuç üretemedi, lütfen tekrar deneyin."}
    assert client.app.state.llm.calls == []
    assert client.get(f"/api/jobs/{job_id}", params={"session_id": sid}).json()["status"] == "error"
    assert wait_until(lambda: client.app.state.sessions.active.busy == 0)


# --- iş sahipliği / meşguliyet / iptal ------------------------------------
def test_job_belongs_to_its_session(client):
    sid = new_session(client)
    other = new_session(client)  # sırada
    job_id, _ = analyze(client, sid)
    assert client.get(f"/api/jobs/{job_id}", params={"session_id": other}).status_code == 409
    assert client.get(f"/api/jobs/baska", params={"session_id": sid}).status_code == 404
    assert client.get(f"/api/jobs/{job_id}/events", params={"session_id": "yok"}).status_code == 404


def test_running_job_blocks_new_requests_and_session_end_cancels_it(client):
    client.app.state.llm = HangingLLM()
    sid = new_session(client)
    r = post_analysis(client, sid)
    assert r.status_code == 202
    session = client.app.state.sessions.active
    job = session.job
    assert session.busy == 1  # görev başlamadan meşgul sayılır
    assert post_analysis(client, sid).status_code == 409
    assert client.post("/api/ask", json={"session_id": sid, "message": "x"}).status_code == 409
    assert client.get(f"/api/session/{sid}").json()["idle_remaining"] is None  # idle sayacı işlemiyor
    assert client.get(f"/api/jobs/{job.id}", params={"session_id": sid}).json()["status"] == "running"
    client.delete(f"/api/session/{sid}")
    assert wait_until(lambda: job.task.done())
    assert job.task.cancelled() and job.status == "cancelled"


# --- takip soruları -------------------------------------------------------
def test_ask_before_analysis_is_rejected(client):
    sid = new_session(client)
    r = client.post("/api/ask", json={"session_id": sid, "message": "merhaba"})
    assert r.status_code == 409 and r.json()["detail"] == {"status": "no_analysis"}


def test_ask_goes_to_both_agents_with_their_own_history(client):
    sid = new_session(client)
    analyze(client, sid)
    before = len(client.app.state.llm.calls)
    job_id, evs = ask(client, sid, "Ekim için sizce ne zaman uygun?")
    assert texts(evs) == {"a": "A2-bir A2-iki", "b": "B2-bir B2-iki"}
    assert evs[0][1]["stage"] == "ask" and evs[0][1]["message"] == "Ekim için sizce ne zaman uygun?"
    calls = client.app.state.llm.calls[before:]
    assert len(calls) == 2
    for m in calls:
        assert [x["role"] for x in m] == ["system", "user", "assistant", "user"]
        assert m[3]["content"] == "Ekim için sizce ne zaman uygun?"
        assert "Parsel:" in m[1]["content"]  # ilk mesaj botun verisi
    a_call = next(m for m in calls if RecordingLLM.who(m) == "A")
    assert "Toprak" not in json.dumps(a_call, ensure_ascii=False)  # bot başka botun verisini görmez
    snap = client.get(f"/api/jobs/{job_id}", params={"session_id": sid}).json()
    assert snap["kind"] == "ask" and snap["question"] == "Ekim için sizce ne zaman uygun?"
    assert snap["status"] == "done"


def test_ask_can_target_a_single_agent_and_history_accumulates(client):
    sid = new_session(client)
    analyze(client, sid)
    before = len(client.app.state.llm.calls)
    _, evs = ask(client, sid, "yalnız sen cevapla", agent="a")
    assert texts(evs) == {"a": "A2-bir A2-iki"}
    assert len(client.app.state.llm.calls) == before + 1
    ask(client, sid, "bir daha", agent="a")
    last = client.app.state.llm.calls[-1]
    assert [m["role"] for m in last] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert len(client.app.state.sessions.active.agent_history["a"]) == 6
    assert len(client.app.state.sessions.active.agent_history["b"]) == 2  # b'ye sorulmadı


def test_ask_validation(client):
    sid = new_session(client)
    analyze(client, sid)
    assert client.post("/api/ask", json={"session_id": sid, "message": "x", "agent": "yok"}).status_code == 422
    assert client.post("/api/ask", json={"session_id": sid, "message": ""}).status_code == 422
    assert client.post("/api/ask", json={"session_id": sid, "message": "a" * 2001}).status_code == 422
    assert client.post("/api/ask", json={"session_id": "yok", "message": "x"}).status_code == 404


def test_ask_rebuilds_data_for_an_agent_whose_analysis_failed(client):
    client.app.state.llm = RecordingLLM(fail_for="A")
    sid = new_session(client)
    analyze(client, sid)
    assert "a" not in client.app.state.sessions.active.agent_history  # başarısız cevap geçmişe girmedi
    client.app.state.llm = RecordingLLM()  # model düzeldi
    _, evs = ask(client, sid, "tekrar dene", agent="a")
    assert texts(evs) == {"a": "A1-bir A1-iki"}
    msgs = client.app.state.llm.calls[-1]
    assert [m["role"] for m in msgs] == ["system", "user", "user"]
    assert "Parsel:" in msgs[1]["content"] and msgs[2]["content"] == "tekrar dene"


def test_new_analysis_replaces_previous_state(client):
    sid = new_session(client)
    first, _ = analyze(client, sid)
    ask(client, sid, "soru")
    second, _ = analyze(client, sid, date="2026-06-01")
    assert second != first
    assert client.get(f"/api/jobs/{first}", params={"session_id": sid}).status_code == 404
    hist = client.app.state.sessions.active.agent_history
    assert all(len(h) == 2 for h in hist.values())  # eski sohbet sıfırlandı


# --- geçmiş kırpma --------------------------------------------------------
def make_history(n_pairs):
    h = [{"role": "user", "content": "VERİ"}, {"role": "assistant", "content": "İLK"}]
    for i in range(n_pairs):
        h += [{"role": "user", "content": f"s{i}"}, {"role": "assistant", "content": f"c{i}"}]
    return h


def test_trim_keeps_data_and_first_answer_and_alternation():
    h = make_history(10)  # 22 mesaj
    t = trim_history(h, 8)
    assert t[:2] == h[:2]  # veri + ilk yorum hep kalır
    assert len(t) <= 8 and t[2]["role"] == "user"
    assert t[-2:] == h[-2:]  # en yeni mesajlar kalır
    assert all(a["role"] != b["role"] for a, b in zip(t, t[1:]))
    assert trim_history(make_history(2), 8) == make_history(2)  # kısa geçmişe dokunulmaz
    assert len(trim_history(h, 1)) >= 4  # alt sınır
