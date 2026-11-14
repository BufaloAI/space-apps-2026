"""Backend API: kullanıcı ile vLLM'deki modeller arasında köprü.

Akış: /api/session ile oturum aç (slot boşsa aktif, doluysa sıraya girer) -> aktif olunca
/api/analysis ile parsel analizi başlat (arka plan işi) -> /api/jobs/{id}/events ile akışı izle
(koparsa kaldığı yerden devam) -> /api/ask ile botlara takip sorusu sor.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .agents import GROUNDING_RULES, AgentConfig, load_agents
from .config import cors_origins, load_settings
from .geo import Parcel, PolygonError, parse_polygon
from .llm import LLM
from .ml import FakeMLProvider, Layer, run_providers
from .sessions import Analysis, Job, Session, SessionManager

log = logging.getLogger("backend")

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(data: dict, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sweeper(sessions: SessionManager) -> None:
    """Arka planda süresi dolan oturumları temizler, sıradakini aktif yapar."""
    while True:
        await asyncio.sleep(5)
        sessions.sweep()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.sessions = SessionManager(settings.idle_timeout_seconds, settings.queue_ttl_seconds)
    app.state.llm = LLM(settings)
    app.state.agents = load_agents(settings.agents_file)
    app.state.ml_providers = [FakeMLProvider(settings.ml_fake_delay_seconds)]
    sweeper = asyncio.create_task(_sweeper(app.state.sessions))
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        await app.state.llm.close()


app = FastAPI(title="Space Apps 2026 Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


# ---------------------------------------------------------------------------
# Oturum
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health(request: Request):
    s: SessionManager = request.app.state.sessions
    return {"ok": True, "active": s.active is not None, "queue_length": len(s.queue)}


@app.post("/api/session")
async def join(request: Request):
    """Oturum aç: slot boşsa 'active', doluysa 'queued' (sıra numarasıyla) döner."""
    sessions: SessionManager = request.app.state.sessions
    s = sessions.join()
    return {"session_id": s.id, **sessions.status(s.id)}


@app.get("/api/session/{session_id}")
async def session_status(session_id: str, request: Request):
    st = request.app.state.sessions.status(session_id)
    if st["status"] is None:
        raise HTTPException(404, "Oturum bulunamadı")
    return st


@app.get("/api/session/{session_id}/events")
async def session_events(session_id: str, request: Request):
    """Sıra durumunu SSE ile akıtır; oturum aktif olunca veya bitince kapanır."""
    sessions: SessionManager = request.app.state.sessions
    if sessions.status(session_id)["status"] is None:
        raise HTTPException(404, "Oturum bulunamadı")

    async def gen():
        last = None
        ticks = 0
        while not await request.is_disconnected():
            st = sessions.status(session_id)
            if st["status"] is None:
                st = {"status": "ended", "reason": "unknown"}
            key = (st["status"], st.get("position"), st.get("reason"))
            if key != last:
                last = key
                yield _sse(st)
            if st["status"] in ("active", "ended"):
                break
            ticks += 1
            if ticks % 15 == 0:
                yield ": ping\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/api/session/{session_id}/keepalive")
async def keepalive(session_id: str, request: Request):
    if not request.app.state.sessions.touch(session_id):
        raise HTTPException(409, "Oturum aktif değil")
    return {"ok": True}


@app.delete("/api/session/{session_id}")
@app.post("/api/session/{session_id}/leave")  # tarayıcı sekme kapanırken yalnızca POST (sendBeacon) atabilir
async def leave(session_id: str, request: Request):
    if not request.app.state.sessions.leave(session_id):
        raise HTTPException(404, "Oturum bulunamadı")
    return {"ok": True}


@app.get("/api/agents")
async def list_agents(request: Request):
    return [a.public() for a in request.app.state.agents]


# ---------------------------------------------------------------------------
# İş (job) altyapısı: analiz ve takip soruları arka planda çalışır
# ---------------------------------------------------------------------------
def _require_active(sessions: SessionManager, session_id: str, *, allow_busy: bool = False) -> Session:
    session = sessions.get_active(session_id)
    if session is None:
        st = sessions.status(session_id)
        if st["status"] is None:
            raise HTTPException(404, "Oturum bulunamadı")
        raise HTTPException(409, detail=st)  # sırada veya bitmiş
    if session.busy and not allow_busy:
        raise HTTPException(409, detail={"status": "busy"})
    return session


def _own_job(sessions: SessionManager, session_id: str, job_id: str) -> Job:
    session = _require_active(sessions, session_id, allow_busy=True)
    if session.job is None or session.job.id != job_id:
        raise HTTPException(404, "İş bulunamadı")
    return session.job


def _start_job(
    sessions: SessionManager, session: Session, kind: str, run: Callable[[Job], Awaitable[None]]
) -> Job:
    job = Job(secrets.token_urlsafe(8), kind)
    session.job = job
    sessions.begin_busy(session)  # görev başlamadan önce meşgul say (ikinci istek araya giremesin)
    job.task = asyncio.create_task(run(job))
    job.task.add_done_callback(lambda _t: sessions.end_busy(session))  # iptalde de çalışır
    return job


@app.get("/api/jobs/{job_id}")
async def job_snapshot(job_id: str, session_id: str, request: Request):
    """İşin anlık durumu (sayfa yenilenince ekranı yeniden kurmak için)."""
    return _own_job(request.app.state.sessions, session_id, job_id).snapshot()


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, session_id: str, request: Request, after: int | None = None):
    """İşin olaylarını SSE ile akıtır. Her olayın `id`'si var; koparsa `Last-Event-ID` başlığı
    (EventSource bunu kendisi yollar) veya `?after=<son alınan id>` ile kaldığı yerden devam eder."""
    job = _own_job(request.app.state.sessions, session_id, job_id)
    last = request.headers.get("last-event-id", "")
    if last.isdigit():
        start = int(last) + 1
    elif after is not None:
        start = max(0, after + 1)
    else:
        start = 0

    async def gen():
        async for event_id, event in job.follow(start):
            yield ": ping\n\n" if event is None else _sse(event, event_id)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ---------------------------------------------------------------------------
# Bot istemleri
# ---------------------------------------------------------------------------
def _system_message(agent: AgentConfig) -> dict:
    return {"role": "system", "content": f"{agent.system_prompt}\n\n{GROUNDING_RULES}"}


def _data_message(agent: AgentConfig, parcel: Parcel, date: dt.date, layers: list[Layer]) -> dict:
    """Botun yalnızca kendi katmanlarını içeren istem. Eksik katman açıkça 'veri yok' denir."""
    by_name = {layer.name: layer for layer in layers}
    lines = [
        f"- {by_name[n].summary()}" if n in by_name else f"- {n}: veri yok (ML bu katmanı üretemedi)"
        for n in agent.layers
    ]
    lon, lat = parcel.centroid
    text = (
        f"Parsel: yaklaşık {parcel.area_ha:.1f} hektar, merkez {lat:.4f} K, {lon:.4f} D. "
        f"Analiz tarihi: {date.isoformat()}.\n"
        "Sana verilen veriler:\n" + "\n".join(lines) +
        "\n\nBu verileri kendi bakış açından, en fazla 5 cümleyle yorumla."
    )
    return {"role": "user", "content": text}


def trim_history(history: list[dict], max_messages: int) -> list[dict]:
    """Uzayan geçmişi kırpar ama ilk ikiyi (veri + ilk yorum) hep korur: bağlamın dayanağı onlar."""
    max_messages = max(4, max_messages)
    if len(history) <= max_messages:
        return history
    tail = history[-(max_messages - 2):]
    while tail and tail[0]["role"] != "user":
        tail.pop(0)
    return [*history[:2], *tail]


async def _run_agents(
    jobs: list[tuple[AgentConfig, list[dict]]], llm: LLM, session: Session, job: Job, max_messages: int
) -> None:
    """Botları paralel çalıştırıp olayları işin günlüğüne yazar. Bir botun hatası diğerini etkilemez."""

    async def worker(agent: AgentConfig, messages: list[dict]) -> None:
        parts: list[str] = []
        try:
            async for delta in llm.stream(messages):
                parts.append(delta)
                job.publish({"agent": agent.id, "delta": delta})
            job.publish({"agent": agent.id, "done": True})
            # yalnızca tamamlanan cevap botun geçmişine girer
            history = [*messages[1:], {"role": "assistant", "content": "".join(parts)}]
            session.agent_history[agent.id] = trim_history(history, max_messages)
        except asyncio.CancelledError:
            raise
        except Exception:  # ayrıntıyı kullanıcıya sızdırma
            log.exception("vLLM isteği başarısız (bot %s)", agent.id)
            job.publish({"agent": agent.id, "error": "Model sunucusuna ulaşılamadı."})

    await asyncio.gather(*(worker(a, m) for a, m in jobs))


# ---------------------------------------------------------------------------
# Parsel analizi: poligon + tarih -> ML -> botların (paralel) yorumu
# ---------------------------------------------------------------------------
class AnalysisRequest(BaseModel):
    session_id: str
    polygon: dict  # GeoJSON Polygon (veya Polygon içeren Feature), [boylam, enlem]
    date: dt.date  # YYYY-MM-DD

    @field_validator("date")
    @classmethod
    def _plausible_date(cls, v: dt.date) -> dt.date:
        if not 2000 <= v.year <= 2100:
            raise ValueError("Tarih 2000-2100 yılları arasında olmalı")
        return v


@app.post("/api/analysis", status_code=202)
async def start_analysis(req: AnalysisRequest, request: Request):
    """Parsel analizini arka planda başlatır; `job_id` döner. İlerlemeyi `/api/jobs/{job_id}/events`
    ile izleyin."""
    st = request.app.state
    session = _require_active(st.sessions, req.session_id)
    try:
        parcel = parse_polygon(req.polygon, max_area_ha=st.settings.max_area_ha)
    except PolygonError as e:
        raise HTTPException(422, str(e)) from None

    async def run(job: Job) -> None:
        try:
            job.publish({
                "stage": "ml", "status": "started", "date": req.date.isoformat(),
                "parcel": {"area_ha": round(parcel.area_ha, 2), "centroid": list(parcel.centroid)},
            })
            layers, errors = await run_providers(st.ml_providers, parcel, req.date)
            session.analysis = Analysis(parcel, req.date, layers)
            job.publish({"stage": "ml", "status": "done", "errors": errors,
                         "layers": [layer.to_dict() for layer in layers]})
            if not layers:
                job.publish({"error": "Analiz sonuç üretemedi, lütfen tekrar deneyin."})
                return
            job.publish({"stage": "agents", "agents": [a.public() for a in st.agents]})
            session.agent_history.clear()
            jobs = [(a, [_system_message(a), _data_message(a, parcel, req.date, layers)]) for a in st.agents]
            await _run_agents(jobs, st.llm, session, job, st.settings.history_max_messages)
            job.publish({"done": True})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Analiz işi beklenmedik şekilde başarısız")
            job.publish({"error": "Analiz sırasında beklenmeyen bir hata oluştu."})
        finally:
            job.finish()

    job = _start_job(st.sessions, session, "analysis", run)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Takip soruları
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=2000)
    agent: str | None = None  # boşsa iki bota (tüm botlara) birden sorulur


@app.post("/api/ask", status_code=202)
async def ask(req: AskRequest, request: Request):
    """Analizden sonra botlara takip sorusu. `agent` verilirse yalnızca o bota; yoksa hepsine."""
    st = request.app.state
    session = _require_active(st.sessions, req.session_id)
    if session.analysis is None:
        raise HTTPException(409, detail={"status": "no_analysis"})  # önce analiz gerekli
    selected: list[AgentConfig] = st.agents
    if req.agent is not None:
        selected = [a for a in st.agents if a.id == req.agent]
        if not selected:
            raise HTTPException(422, "Bilinmeyen bot")
    analysis = session.analysis
    question = {"role": "user", "content": req.message}

    async def run(job: Job) -> None:
        try:
            job.publish({"stage": "ask", "message": req.message, "agents": [a.public() for a in selected]})
            jobs = []
            for a in selected:
                # analizi başarısız olmuş bot için veri mesajı yeniden kurulur
                history = session.agent_history.get(a.id) or [
                    _data_message(a, analysis.parcel, analysis.date, analysis.layers)
                ]
                jobs.append((a, [_system_message(a), *history, question]))
            await _run_agents(jobs, st.llm, session, job, st.settings.history_max_messages)
            job.publish({"done": True})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Soru işi beklenmedik şekilde başarısız")
            job.publish({"error": "Soru yanıtlanırken beklenmeyen bir hata oluştu."})
        finally:
            job.finish()

    job = _start_job(st.sessions, session, "ask", run)
    return {"job_id": job.id}
