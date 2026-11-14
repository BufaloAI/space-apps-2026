import asyncio

from app.sessions import Job


async def collect(job, start=0, heartbeat=None, limit=None):
    out = []
    async for item in job.follow(start, heartbeat):
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def test_follow_replays_recorded_then_live_events_and_ends_on_finish():
    async def scenario():
        job = Job("j", "analysis")
        job.publish({"n": 0})
        job.publish({"n": 1})

        async def producer():
            await asyncio.sleep(0.02)
            job.publish({"n": 2})
            await asyncio.sleep(0.02)
            job.publish({"done": True})
            job.finish()

        asyncio.create_task(producer())
        return await asyncio.wait_for(collect(job), 2)

    got = asyncio.run(scenario())
    assert [i for i, _ in got] == [0, 1, 2, 3]
    assert got[-1][1] == {"done": True}


def test_follow_resumes_from_offset():
    async def scenario():
        job = Job("j", "ask")
        for n in range(5):
            job.publish({"n": n})
        job.finish()
        return await collect(job, start=3), await collect(job, start=99), await collect(job, start=-5, limit=1)

    tail, beyond, neg = asyncio.run(scenario())
    assert [e["n"] for _, e in tail] == [3, 4]
    assert beyond == []
    assert neg[0][0] == 0  # negatif başlangıç 0'a çekilir


def test_idle_follow_sends_heartbeats():
    async def scenario():
        job = Job("j", "analysis")
        got = await collect(job, heartbeat=0.02, limit=2)
        job.finish()
        return got

    assert asyncio.run(scenario()) == [(None, None), (None, None)]


def test_multiple_followers_each_get_every_event():
    async def scenario():
        job = Job("j", "analysis")
        t1 = asyncio.create_task(collect(job))
        t2 = asyncio.create_task(collect(job))
        await asyncio.sleep(0.01)
        job.publish({"a": 1})
        job.publish({"a": 2})
        job.finish()
        return await t1, await t2

    r1, r2 = asyncio.run(scenario())
    assert r1 == r2 and [e["a"] for _, e in r1] == [1, 2]


def test_cancel_wakes_followers_even_if_task_never_started():
    async def scenario():
        job = Job("j", "analysis")

        async def never():
            await asyncio.sleep(3600)

        job.task = asyncio.create_task(never())
        waiter = asyncio.create_task(collect(job))
        await asyncio.sleep(0)  # takipçi beklemeye başlasın
        job.cancel()
        await asyncio.wait_for(waiter, 1)
        await asyncio.gather(job.task, return_exceptions=True)
        return job

    job = asyncio.run(scenario())
    assert job.status == "cancelled" and job.task.cancelled()


def test_status_values():
    j = Job("j", "analysis")
    assert j.status == "running"
    j.publish({"agent": "a", "done": True})  # botun done'ı işin done'ı sayılmaz
    j.finish()
    assert j.status == "error"
    k = Job("k", "analysis")
    k.publish({"done": True})
    k.finish()
    assert k.status == "done"
    e = Job("e", "analysis")
    e.publish({"error": "x"})
    e.finish()
    assert e.status == "error"


def test_snapshot_reduces_events_to_state():
    j = Job("j", "analysis")
    j.publish({"stage": "ml", "status": "started", "date": "2026-05-01", "parcel": {"area_ha": 1.0}})
    j.publish({"stage": "ml", "status": "done", "errors": ["x"], "layers": [{"name": "yagis"}]})
    j.publish({"stage": "agents", "agents": [{"id": "a", "name": "A", "layers": ["yagis"]},
                                              {"id": "b", "name": "B", "layers": ["nem"]}]})
    j.publish({"agent": "a", "delta": "Mer"})
    j.publish({"agent": "a", "delta": "haba"})
    j.publish({"agent": "a", "done": True})
    j.publish({"agent": "b", "error": "hata"})
    s = j.snapshot()
    assert s["status"] == "running" and s["last_event_id"] == 6
    assert s["parcel"] == {"area_ha": 1.0} and s["date"] == "2026-05-01"
    assert s["layers"] == [{"name": "yagis"}] and s["ml_errors"] == ["x"]
    a, b = s["agents"]
    assert (a["text"], a["status"]) == ("Merhaba", "done")
    assert (b["text"], b["status"], b["error"]) == ("", "error", "hata")
    j.publish({"done": True})
    j.finish()
    assert j.snapshot()["status"] == "done"
