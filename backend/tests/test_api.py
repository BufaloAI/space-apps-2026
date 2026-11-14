import json

import pytest
from fastapi.testclient import TestClient


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def stream(self, messages):
        self.calls.append(messages)
        for part in ["Mer", "haba", "!"]:
            yield part

    async def close(self):
        pass


class BrokenLLM(FakeLLM):
    async def stream(self, messages):
        raise RuntimeError("gizli iç hata: 10.0.0.5 bağlantı reddedildi")
        yield  # pragma: no cover


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    from app.main import app

    with TestClient(app) as c:
        c.app.state.llm = FakeLLM()
        yield c


def events(response):
    out = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def chat(client, sid, msg="selam"):
    with client.stream("POST", "/api/chat", json={"session_id": sid, "message": msg}) as r:
        return r.status_code, events(r)


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "")
    from app.main import app

    with pytest.raises(RuntimeError, match="VLLM_API_KEY"):
        with TestClient(app):
            pass


def test_join_chat_stream_and_history(client):
    sid = client.post("/api/session").json()["session_id"]
    code, evs = chat(client, sid)
    assert code == 200
    assert "".join(e["delta"] for e in evs if "delta" in e) == "Merhaba!"
    assert evs[-1] == {"done": True}
    chat(client, sid, "ikinci")
    msgs = client.app.state.llm.calls[-1]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]


def test_second_user_is_queued_and_cannot_chat(client):
    a = client.post("/api/session").json()
    b = client.post("/api/session").json()
    assert a["status"] == "active"
    assert b["status"] == "queued" and b["position"] == 1
    r = client.post("/api/chat", json={"session_id": b["session_id"], "message": "selam"})
    assert r.status_code == 409 and r.json()["detail"]["status"] == "queued"
    client.delete(f"/api/session/{a['session_id']}")
    assert client.get(f"/api/session/{b['session_id']}").json()["status"] == "active"
    assert chat(client, b["session_id"])[0] == 200


def test_unknown_session_and_validation(client):
    assert client.get("/api/session/yok").status_code == 404
    assert client.post("/api/chat", json={"session_id": "yok", "message": "x"}).status_code == 404
    sid = client.post("/api/session").json()["session_id"]
    assert client.post("/api/chat", json={"session_id": sid, "message": ""}).status_code == 422
    assert client.post("/api/chat", json={"session_id": sid, "message": "a" * 4001}).status_code == 422


def test_llm_error_does_not_leak_details(client):
    client.app.state.llm = BrokenLLM()
    sid = client.post("/api/session").json()["session_id"]
    code, evs = chat(client, sid)
    assert code == 200
    assert evs == [{"error": "Model sunucusuna ulaşılamadı, lütfen tekrar deneyin."}]
    assert "10.0.0.5" not in json.dumps(evs)
    # hata sonrası oturum bozulmaz
    client.app.state.llm = FakeLLM()
    assert chat(client, sid)[1][-1] == {"done": True}


def test_events_stream_closes_when_active(client):
    a = client.post("/api/session").json()
    with client.stream("GET", f"/api/session/{a['session_id']}/events") as r:
        evs = events(r)
    assert evs[0]["status"] == "active"


def test_keepalive_and_health(client):
    sid = client.post("/api/session").json()["session_id"]
    assert client.post(f"/api/session/{sid}/keepalive").status_code == 200
    h = client.get("/api/health").json()
    assert h == {"ok": True, "active": True, "queue_length": 0}
    client.delete(f"/api/session/{sid}")
    assert client.post(f"/api/session/{sid}/keepalive").status_code == 409
