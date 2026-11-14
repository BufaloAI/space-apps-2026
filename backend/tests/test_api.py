import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "test-key")
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_missing_api_key_fails_fast(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "")
    from app.main import app

    with pytest.raises(RuntimeError, match="VLLM_API_KEY"):
        with TestClient(app):
            pass


def test_first_user_active_second_queued_then_promoted(client):
    a = client.post("/api/session").json()
    b = client.post("/api/session").json()
    assert a["status"] == "active" and a["job_id"] is None and a["has_analysis"] is False
    assert b["status"] == "queued" and b["position"] == 1
    client.delete(f"/api/session/{a['session_id']}")
    assert client.get(f"/api/session/{b['session_id']}").json()["status"] == "active"
    assert client.get(f"/api/session/{a['session_id']}").json() == {"status": "ended", "reason": "left"}


def test_unknown_session(client):
    assert client.get("/api/session/yok").status_code == 404
    assert client.delete("/api/session/yok").status_code == 404
    assert client.post("/api/session/yok/keepalive").status_code == 409


def test_events_stream_closes_when_active(client):
    a = client.post("/api/session").json()
    with client.stream("GET", f"/api/session/{a['session_id']}/events") as r:
        evs = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert evs[0]["status"] == "active"


def test_keepalive_and_health(client):
    sid = client.post("/api/session").json()["session_id"]
    assert client.post(f"/api/session/{sid}/keepalive").status_code == 200
    assert client.get("/api/health").json() == {"ok": True, "active": True, "queue_length": 0}
    client.delete(f"/api/session/{sid}")
    assert client.post(f"/api/session/{sid}/keepalive").status_code == 409


def test_old_generic_chat_endpoint_is_gone(client):
    sid = client.post("/api/session").json()["session_id"]
    assert client.post("/api/chat", json={"session_id": sid, "message": "x"}).status_code in (404, 405)


def test_leave_via_post_for_send_beacon(client):
    a = client.post("/api/session").json()
    b = client.post("/api/session").json()
    assert client.post(f"/api/session/{a['session_id']}/leave").json() == {"ok": True}
    assert client.get(f"/api/session/{b['session_id']}").json()["status"] == "active"
    assert client.post(f"/api/session/{a['session_id']}/leave").status_code == 404
