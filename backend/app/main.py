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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from typing import Literal

from .agents import GROUNDING_RULES, GROUNDING_RULES_EN, AgentConfig, load_agents
from .climate import ClimateError, ClimateStore
from .config import cors_origins, load_settings
from .geo import Parcel, PolygonError, parse_polygon
from .llm import LLM
from .ml import Layer, MLUserError, UserMessage, XGBoostMLProvider, build_providers, run_providers
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
    app.state.climate = ClimateStore(settings.climate_cache_dir)
    app.state.ml_providers = build_providers(
        settings.ml_provider, fake_delay=settings.ml_fake_delay_seconds, model_dir=settings.ml_model_dir,
        cache_dir=settings.ml_cache_dir, ndvi_timeout=settings.ml_ndvi_timeout_seconds,
        ndvi_retries=settings.ml_ndvi_retries,
    )
    log.info("ML sağlayıcısı: %s", ", ".join(p.name for p in app.state.ml_providers))
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
Lang = Literal["tr", "en"]
_EN_REPLY = "\n\nAnswer in English."
# Türkçe uzun prompt + Türkçe veri, sondaki tek cümlelik talimatı bastırabiliyor: İngilizcede dil talimatı başa da konur.
_EN_FIRST = ("LANGUAGE: The user reads English. Write your whole answer in English, even though the instructions "
             "and data below are in Turkish. Translate crop names and terms.\n\n")


def _system_message(agent: AgentConfig, lang: str = "tr") -> dict:
    if lang == "en":
        return {"role": "system", "content": f"{_EN_FIRST}{agent.system_prompt}\n\n{GROUNDING_RULES_EN}"}
    return {"role": "system", "content": f"{agent.system_prompt}\n\n{GROUNDING_RULES}"}


def _layer_lines(agent: AgentConfig, layers: list[Layer], lang: str = "tr") -> list[str]:
    by_name = {layer.name: layer for layer in layers}
    en = lang == "en"

    def line(n: str) -> str:
        return f"- {by_name[n].summary_en() if en else by_name[n].summary()}"

    missing = "no data (the ML could not produce this layer)" if en else "veri yok (ML bu katmanı üretemedi)"
    lines = [line(n) if n in by_name else f"- {n}: {missing}" for n in agent.layers]
    lines += [line(n) for n in agent.optional_layers if n in by_name]
    return lines


def _ml_notes(layers: list[Layer], lang: str = "tr") -> list[str]:
    """Gerçek ML'in uyarıları (veri gecikmesi, eksik kaynak) ve bilgi boşlukları; sahte ML'de boş."""
    meta = next((lay.details["_meta"] for lay in layers if lay.details and "_meta" in lay.details), None)
    if not meta:
        return []
    if lang == "en":
        return [*(meta.get("warnings_en") or meta.get("warnings", [])),
                *(meta.get("information_gaps_en") or meta.get("information_gaps", []))[:4]]
    return [*meta.get("warnings", []), *meta.get("information_gaps", [])[:4]]


def _data_message(agent: AgentConfig, parcel: Parcel, date: dt.date, layers: list[Layer], lang: str = "tr") -> dict:
    """Botun yalnızca kendi katmanlarını içeren istem. Eksik katman açıkça 'veri yok' denir."""
    lines = _layer_lines(agent, layers, lang)
    notes = _ml_notes(layers, lang)
    lon, lat = parcel.centroid
    if lang == "en":
        text = (
            f"Parcel: about {parcel.area_ha:.1f} hectares, centre {lat:.4f} N, {lon:.4f} E. "
            f"Analysis date: {date.isoformat()}.\nYour data:\n" + "\n".join(lines) +
            ("\nNotes (data limits):\n" + "\n".join(f"- {n}" for n in notes) if notes else "") +
            f"\n\nInterpret this data from your own perspective in at most {agent.max_sentences} sentences, in English."
        )
        return {"role": "user", "content": text}
    text = (
        f"Parsel: yaklaşık {parcel.area_ha:.1f} hektar, merkez {lat:.4f} K, {lon:.4f} D. "
        f"Analiz tarihi: {date.isoformat()}.\n"
        "Sana verilen veriler:\n" + "\n".join(lines) +
        ("\nNotlar (veri sınırları):\n" + "\n".join(f"- {n}" for n in notes) if notes else "") +
        f"\n\nBu verileri kendi bakış açından, en fazla {agent.max_sentences} cümleyle yorumla."
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
PRIORITY_KEYS = {"water", "soil_health", "income", "erosion", "forage"}


class FarmProfile(BaseModel):
    """Çiftçinin bildiği tarla bilgileri (hepsi isteğe bağlı); rotasyon puanına ve güvenine girer."""

    irrigation: bool | None = None
    soil_om_pct: float | None = Field(default=None, ge=0, le=100)
    slope_pct: float | None = Field(default=None, ge=0, le=100)
    soil_texture: str | None = Field(default=None, max_length=40)

    def as_dict(self) -> dict:
        d = self.model_dump(exclude_none=True)
        if "irrigation" in d:
            d["irrigation"] = float(d["irrigation"])
        return d


class CustomCrop(BaseModel):
    """Çiftçinin eklediği ürün (ya da aynı kimlikle katalog ürününü kendi değerleriyle değiştirmesi).
    0-1 alanlar crops.toml ile aynı anlamdadır; ekonomi alanları çiftçinin kendi hesabıdır (isteğe bağlı)."""

    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=60)
    family: str = Field(default="other", max_length=30)
    season: str = Field(default="warm", pattern=r"^(warm|cool|perennial|none)$")
    water_need: float = Field(0.5, ge=0, le=1)
    drought_tol: float = Field(0.5, ge=0, le=1)
    heat_tol: float = Field(0.5, ge=0, le=1)
    frost_tol: float = Field(0.5, ge=0, le=1)
    residue_cover: float = Field(0.5, ge=0, le=1)
    om: float = Field(0.5, ge=0, le=1)
    market: float = Field(0.5, ge=0, le=1)
    forage: float = Field(0.3, ge=0, le=1)
    n_need: float = Field(0.5, ge=0, le=1)
    wet_tol: float = Field(0.7, ge=0, le=1)
    gdd_need: float = Field(0, ge=0, le=5000)
    n_fix: bool = False
    cover_crop: bool = False
    yield_t_ha: float | None = Field(default=None, ge=0, le=500)
    price_per_t: float | None = Field(default=None, ge=0, le=1e9)
    cost_per_ha: float | None = Field(default=None, ge=0, le=1e9)


class CustomRotation(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    seq: str = Field(min_length=1, max_length=300)  # "corn+cereal_rye>soybean>custom_lentil"


class RotationOptions(BaseModel):
    existing_rotation: str | None = Field(default=None, max_length=200)  # örn. "corn>soybean"
    chosen_rotation: str | None = Field(default=None, max_length=200)  # çiftçinin değerlendirmek istediği
    priorities: dict[str, float] | None = None  # örn. {"water": 2, "income": 1}
    profile: FarmProfile | None = None
    custom_crops: list[CustomCrop] | None = Field(default=None, max_length=30)
    custom_rotations: list[CustomRotation] | None = Field(default=None, max_length=30)

    @field_validator("priorities")
    @classmethod
    def _check_priorities(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        bad = set(v) - PRIORITY_KEYS
        if bad:
            raise ValueError(f"Bilinmeyen öncelik: {sorted(bad)}. Geçerli: {sorted(PRIORITY_KEYS)}")
        if any(not 0 <= x <= 5 for x in v.values()):
            raise ValueError("Öncelik ağırlıkları 0-5 arasında olmalı")
        return v

    def as_options(self) -> dict:
        return {"existing_rotation": self.existing_rotation, "chosen_rotation": self.chosen_rotation,
                "priorities": self.priorities, "profile": self.profile.as_dict() if self.profile else None,
                "custom_crops": [c.model_dump(exclude_none=True) for c in self.custom_crops]
                if self.custom_crops is not None else None,
                "custom_rotations": [r.model_dump() for r in self.custom_rotations]
                if self.custom_rotations is not None else None}


class AnalysisRequest(RotationOptions):
    session_id: str
    lang: Lang = "tr"  # botların cevap dili (veri ve katmanlar her zaman iki dilli gelir)
    polygon: dict  # GeoJSON Polygon (veya Polygon içeren Feature), [boylam, enlem]
    # Kayıt için. Gerçek ML her zaman bugüne kadarki güncel veriyi kullanır.
    date: dt.date = Field(default_factory=dt.date.today)

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
            options = {**req.as_options(), "lang": req.lang}  # yeniden puanlamada bota giden veri aynı dilde
            layers, errors = await run_providers(
                st.ml_providers, parcel, req.date, options=options,
                progress=lambda p: job.publish({"stage": "ml", "status": "progress", "progress": p}),
            )
            session.analysis = Analysis(parcel, req.date, layers, options)
            session.pending_updates.clear()
            job.publish({"stage": "ml", "status": "done", "errors": errors,
                         "layers": [layer.to_dict() for layer in layers]})
            if not layers:
                user = [e for e in errors if isinstance(e, UserMessage)]
                msg = user[0] if user else "Analiz sonuç üretemedi, lütfen tekrar deneyin."
                job.publish({"error": msg})
                return
            job.publish({"stage": "agents", "agents": [a.public() for a in st.agents]})
            session.agent_history.clear()
            def data(a):
                return _data_message(a, parcel, req.date, layers, req.lang)

            jobs = [(a, [_system_message(a, req.lang), data(a)]) for a in st.agents]
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
    lang: Lang = "tr"


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
    question = {"role": "user", "content": req.message + (_EN_REPLY if req.lang == "en" else "")}

    async def run(job: Job) -> None:
        try:
            job.publish({"stage": "ask", "message": req.message, "agents": [a.public() for a in selected]})
            jobs = []
            for a in selected:
                # analizi başarısız olmuş bot için veri mesajı yeniden kurulur
                history = session.agent_history.get(a.id) or [
                    _data_message(a, analysis.parcel, analysis.date, analysis.layers, req.lang)
                ]
                q = question
                update = session.pending_updates.pop(a.id, None)
                if update and session.agent_history.get(a.id):  # yeniden kurulan veri mesajı zaten güncel
                    q = {"role": "user", "content": f"[Güncellenmiş veri]\n{update}\n\n[Soru]\n{question['content']}"}
                jobs.append((a, [_system_message(a, req.lang), *history, q]))
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


# ---------------------------------------------------------------------------
# Rotasyon: katalog ve çiftçinin kendi seçimiyle yeniden puanlama (veri yeniden çekilmez)
# ---------------------------------------------------------------------------
def _xgb_provider(request: Request) -> XGBoostMLProvider:
    p = next((p for p in request.app.state.ml_providers if isinstance(p, XGBoostMLProvider)), None)
    if p is None:
        raise HTTPException(503, "Gerçek ML sağlayıcısı etkin değil (ML_PROVIDER=xgboost).")
    return p


@app.get("/api/crops/suggestions")
async def crop_suggestions(session_id: str, request: Request, exclude: str = ""):
    """Son analizin iklimine uygun ve çiftçinin listesinde olmayan ürünler (kütüphane + katalog). `exclude`:
    virgülle ayrılmış ürün kimlikleri (çiftçinin zaten eklediği). Anında; LLM/veri çekmez."""
    st = request.app.state
    provider = _xgb_provider(request)
    session = _require_active(st.sessions, session_id, allow_busy=True)
    if session.analysis is None:
        raise HTTPException(409, detail={"status": "no_analysis"})
    ids = {x.strip() for x in exclude.split(",") if x.strip()}
    try:
        return provider.suggest_crops(session.analysis.parcel, ids, session.analysis.options.get("profile"))
    except MLUserError as e:
        raise HTTPException(409, str(e)) from None


@app.get("/api/rotations/catalog")
async def rotation_catalog(request: Request):
    """Form için ürünler, hazır rotasyonlar ve öncelik anahtarları."""
    return _xgb_provider(request).catalog()


class RescoreRequest(RotationOptions):
    session_id: str


@app.post("/api/rotations")
async def rescore_rotations(req: RescoreRequest, request: Request):
    """Son analizin verisiyle rotasyonları yeniden puanlar (milisaniyeler, LLM çağrılmaz).
    Rotasyon katmanlarını gören botlar, bir sonraki takip sorusunda güncel veriyi görür."""
    st = request.app.state
    provider = _xgb_provider(request)
    session = _require_active(st.sessions, req.session_id)
    if session.analysis is None:
        raise HTTPException(409, detail={"status": "no_analysis"})
    analysis = session.analysis
    merged = {**analysis.options, **{k: v for k, v in req.as_options().items() if v is not None}}
    try:
        new_layers = provider.rescore(analysis.parcel, merged.get("existing_rotation"), merged.get("chosen_rotation"),
                                      merged.get("priorities"), merged.get("profile"),
                                      merged.get("custom_crops"), merged.get("custom_rotations"))
    except MLUserError as e:
        raise HTTPException(422, str(e)) from None
    analysis.layers = [lay for lay in analysis.layers if not lay.name.startswith("rotasyon_")] + new_layers
    analysis.options = merged
    st.sessions.touch(session.id)  # kullanıcı etkileşimi: boşta kalma sayacını sıfırla
    names = {lay.name for lay in new_layers}
    for a in st.agents:
        if names & {*a.layers, *a.optional_layers} or any(n.startswith("rotasyon_") for n in a.layers):
            session.pending_updates[a.id] = "\n".join(_layer_lines(a, analysis.layers, merged.get("lang", "tr")))
    return {"layers": [lay.to_dict() for lay in new_layers], "options": merged}


# ---------------------------------------------------------------------------
# Bölge iklim verisi (NASA POWER, önbellekli). Oturum gerektirmez; LLM/ML slotunu kullanmaz.
# ---------------------------------------------------------------------------
@app.get("/api/climate/daily")
async def climate_daily(lat: float, lon: float, year: int, request: Request):
    """Nokta (tarla merkezi) için bir yılın günlük iklim serisi. Geçmiş yıllar kalıcı, bu yıl günlük önbellek."""
    store: ClimateStore = request.app.state.climate
    try:
        return await store.daily(lat, lon, year)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except ClimateError as e:
        raise HTTPException(502, str(e)) from None


@app.get("/api/climate/outlook")
async def climate_outlook(lat: float, lon: float, request: Request):
    """Önümüzdeki 4 hafta için hava görünümü (noktanın 15 yıllık NASA POWER geçmişinden). Günlük önbellek."""
    store: ClimateStore = request.app.state.climate
    try:
        return await store.outlook(lat, lon)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except ClimateError as e:
        raise HTTPException(502, str(e)) from None



# ---------------------------------------------------------------------------
# Derlenmiş site (isteğe bağlı): SITE_DIR=bufaloai-site/dist verilirse site ve API aynı porttan sunulur
# (tek süreç, CORS yok, tünel/barındırma için). /api yolları yukarıda tanımlı olduğu için önce onlar eşleşir.
# ---------------------------------------------------------------------------
def _mount_site() -> None:
    import os
    from pathlib import Path

    site = os.environ.get("SITE_DIR", "").strip()
    if not site:
        return
    path = Path(site)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    if not (path / "index.html").exists():
        log.warning("SITE_DIR=%s içinde index.html yok; site sunulmuyor (önce npm run build)", path)
        return
    app.mount("/", StaticFiles(directory=path, html=True), name="site")
    log.info("Site sunuluyor: %s", path)


_mount_site()
