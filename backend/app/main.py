"""Backend API: kullanıcı ile vLLM'deki model arasında köprü.

Akış: /api/session ile oturum aç (slot boşsa aktif, doluysa sıraya girer) -> aktif olunca
/api/chat ile sohbet et (cevap SSE ile akar).
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import cors_origins, load_settings
from .llm import LLM
from .sessions import SessionManager

log = logging.getLogger("backend")

# Geçici genel prompt. Ajanlar (tarımcı, sucul, ...) ve ML tool'ları eklenince değişecek.
SYSTEM_PROMPT = (
    "Sen tarım ve arazi riski konusunda yardımcı olan bir asistansın. Türkçe, kısa ve net cevap ver. "
    "Elinde veri yoksa bilmediğini söyle, tahmin yürütme."
)


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=4000)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    allow_headers=["Content-Type"],
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


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
async def leave(session_id: str, request: Request):
    if not request.app.state.sessions.leave(session_id):
        raise HTTPException(404, "Oturum bulunamadı")
    return {"ok": True}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    sessions: SessionManager = request.app.state.sessions
    llm: LLM = request.app.state.llm
    max_msgs: int = request.app.state.settings.history_max_messages

    session = sessions.get_active(req.session_id)
    if session is None:
        st = sessions.status(req.session_id)
        if st["status"] is None:
            raise HTTPException(404, "Oturum bulunamadı")
        raise HTTPException(409, detail=st)  # sırada veya bitmiş
    if session.busy:
        raise HTTPException(409, detail={"status": "busy"})

    session.history.append({"role": "user", "content": req.message})
    if len(session.history) > max_msgs:
        del session.history[:-max_msgs]
    while session.history and session.history[0]["role"] != "user":
        session.history.pop(0)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *session.history]

    async def gen():
        parts: list[str] = []
        with sessions.busy(session):  # cevap üretilirken idle sayacı işlemez
            try:
                async for delta in llm.stream(messages):
                    parts.append(delta)
                    yield _sse({"delta": delta})
                yield _sse({"done": True})
            except asyncio.CancelledError:
                raise
            except Exception:  # model sunucusu hatası: ayrıntıyı kullanıcıya sızdırma
                log.exception("vLLM isteği başarısız")
                yield _sse({"error": "Model sunucusuna ulaşılamadı, lütfen tekrar deneyin."})
            finally:
                if parts:
                    session.history.append({"role": "assistant", "content": "".join(parts)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
