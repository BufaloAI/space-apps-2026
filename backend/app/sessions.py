"""Tek kullanıcılı oturum slotu + FIFO bekleme sırası + arka plan işleri (job).

Kurallar (architecture.md):
- Aynı anda yalnızca 1 aktif oturum.
- Slot doluyken gelenler sıraya girer; slot boşalınca sıradaki otomatik aktif olur.
- Aktif kullanıcı `idle_timeout` saniye boşta kalırsa slot boşalır. Bir iş (analiz/soru) çalışırken
  (busy) sayaç işlemez.
- Sırada bekleyen, `queue_ttl` saniye hiç görünmezse (durum sorgusu/SSE yoksa) sıradan düşer.
- Oturum bitince çalışan iş iptal edilir, geçmiş ve analiz silinir.

Durum bellekte tutulur; backend TEK süreç (tek worker) çalışmalıdır.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum

from .geo import Parcel
from .ml import Layer


class Status(str, Enum):
    ACTIVE = "active"
    QUEUED = "queued"
    ENDED = "ended"


class Job:
    """Arka planda çalışan bir iş (parsel analizi veya takip sorusu).

    Olaylar sırayla bir günlüğe (events) yazılır; istemci koparsa `follow(start)` ile kaldığı
    yerden (olay numarasından) devam eder. Günlük oturumla birlikte silinir.
    """

    HEARTBEAT_SECONDS = 15.0

    def __init__(self, job_id: str, kind: str) -> None:
        self.id = job_id
        self.kind = kind  # "analysis" | "ask"
        self.events: list[dict] = []
        self.finished = False
        self.cancelled = False
        self.task: asyncio.Task | None = None
        self._changed = asyncio.Event()

    # --- üretici tarafı ------------------------------------------------
    def publish(self, event: dict) -> None:
        self.events.append(event)
        self._wake()

    def finish(self) -> None:
        self.finished = True
        self._wake()

    def cancel(self) -> None:
        self.cancelled = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.finish()  # başlamadan iptal edilen görev finally çalıştırmaz; bekleyenleri uyandır

    def _wake(self) -> None:
        old, self._changed = self._changed, asyncio.Event()
        old.set()

    # --- tüketici tarafı -----------------------------------------------
    async def follow(
        self, start: int = 0, heartbeat: float | None = None
    ) -> AsyncIterator[tuple[int | None, dict | None]]:
        """Olayları (numara, olay) olarak verir: önce kayıtlı olanlar, sonra canlı olanlar.
        Boşta kalırsa `heartbeat` saniyede bir (None, None) verir (bağlantıyı canlı tutmak için)."""
        hb = self.HEARTBEAT_SECONDS if heartbeat is None else heartbeat
        i = max(0, start)
        while True:
            while i < len(self.events):
                yield i, self.events[i]
                i += 1
            if self.finished:
                return
            waiter = self._changed  # await'ten önce yakala; arada olay kaçmaz (tek iş parçacığı)
            try:
                await asyncio.wait_for(waiter.wait(), hb)
            except asyncio.TimeoutError:
                yield None, None

    @property
    def status(self) -> str:
        if not self.finished:
            return "running"
        if self.cancelled:
            return "cancelled"
        last = self.events[-1] if self.events else {}
        return "done" if last.get("done") is True and "agent" not in last else "error"

    def snapshot(self) -> dict:
        """Olay günlüğünden anlık durum (sayfa yenilenince ekranı yeniden kurmak için)."""
        st: dict = {
            "job_id": self.id, "kind": self.kind, "status": self.status,
            "last_event_id": len(self.events) - 1,
            "parcel": None, "date": None, "layers": [], "ml_errors": [],
            "question": None, "agents": {}, "error": None,
        }
        for e in self.events:
            stage = e.get("stage")
            if stage == "ml":
                if e["status"] == "started":
                    st["parcel"], st["date"] = e["parcel"], e["date"]
                else:
                    st["layers"], st["ml_errors"] = e["layers"], e["errors"]
            elif stage in ("agents", "ask"):
                if stage == "ask":
                    st["question"] = e["message"]
                for a in e["agents"]:
                    st["agents"][a["id"]] = {**a, "text": "", "status": "running"}
            elif "agent" in e:
                a = st["agents"].setdefault(
                    e["agent"], {"id": e["agent"], "name": e["agent"], "layers": [], "text": "", "status": "running"}
                )
                if "delta" in e:
                    a["text"] += e["delta"]
                elif e.get("done"):
                    a["status"] = "done"
                elif "error" in e:
                    a["status"], a["error"] = "error", e["error"]
            elif "error" in e:
                st["error"] = e["error"]
        st["agents"] = list(st["agents"].values())
        return st


@dataclass
class Analysis:
    """Son parsel analizinin sonucu (takip soruları ve yeniden istem için)."""

    parcel: Parcel
    date: dt.date
    layers: list[Layer]


@dataclass
class Session:
    id: str
    last_seen: float
    busy: int = 0
    job: Job | None = None  # en son iş (analiz veya soru)
    analysis: Analysis | None = None
    agent_history: dict[str, list[dict]] = field(default_factory=dict)  # bot id -> mesajlar (system hariç)


class SessionManager:
    def __init__(
        self,
        idle_timeout: float = 300.0,
        queue_ttl: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        ended_memory: int = 100,
    ) -> None:
        self.idle_timeout = idle_timeout
        self.queue_ttl = queue_ttl
        self._clock = clock
        self._ended_memory = ended_memory
        self.active: Session | None = None
        self.queue: list[Session] = []
        self._ended: OrderedDict[str, str] = OrderedDict()  # id -> neden

    # --- giriş / çıkış -------------------------------------------------
    def join(self) -> Session:
        self.sweep()
        s = Session(id=secrets.token_urlsafe(16), last_seen=self._clock())
        if self.active is None and not self.queue:
            self.active = s
        else:
            self.queue.append(s)
        return s

    def leave(self, session_id: str) -> bool:
        s = self._find(session_id)
        if s is None:
            return False
        self._end(s, "left")
        self._promote()
        return True

    # --- sorgu ---------------------------------------------------------
    def status(self, session_id: str) -> dict:
        """Oturumun durumunu döndürür. Sıradaki oturum için bu istek 'hâlâ orada' sayılır."""
        s = self._find(session_id)
        if s is not None and s is not self.active:
            s.last_seen = self._clock()  # istek geldi = sıradaki kullanıcı hâlâ orada
        self.sweep()
        s = self._find(session_id)
        if s is None:
            reason = self._ended.get(session_id)
            if reason is None:
                return {"status": None}
            return {"status": Status.ENDED.value, "reason": reason}
        if s is self.active:
            return {
                "status": Status.ACTIVE.value,
                "idle_remaining": self._idle_remaining(s),
                "job_id": s.job.id if s.job else None,
                "has_analysis": s.analysis is not None,
            }
        return {"status": Status.QUEUED.value, "position": self.queue.index(s) + 1}

    def get_active(self, session_id: str) -> Session | None:
        """Yalnızca aktif oturumu döndürür."""
        self.sweep()
        if self.active is not None and self.active.id == session_id:
            return self.active
        return None

    def touch(self, session_id: str) -> bool:
        """Aktif oturumu canlı tut (keepalive)."""
        s = self.get_active(session_id)
        if s is None:
            return False
        s.last_seen = self._clock()
        return True

    # --- meşgul işareti (iş çalışırken idle sayacı işlemez) -------------
    def begin_busy(self, session: Session) -> None:
        session.busy += 1

    def end_busy(self, session: Session) -> None:
        session.busy = max(0, session.busy - 1)
        session.last_seen = self._clock()  # sayaç iş bitince yeniden başlar

    @contextmanager
    def busy(self, session: Session):
        self.begin_busy(session)
        try:
            yield
        finally:
            self.end_busy(session)

    # --- bakım ---------------------------------------------------------
    def sweep(self) -> None:
        now = self._clock()
        if self.active and self.active.busy == 0 and now - self.active.last_seen > self.idle_timeout:
            self._end(self.active, "idle")
        for s in list(self.queue):
            if now - s.last_seen > self.queue_ttl:
                self._end(s, "queue_timeout")
        self._promote()

    # --- iç yardımcılar ------------------------------------------------
    def _idle_remaining(self, s: Session) -> float | None:
        if s.busy:
            return None
        return max(0.0, self.idle_timeout - (self._clock() - s.last_seen))

    def _find(self, session_id: str) -> Session | None:
        if self.active is not None and self.active.id == session_id:
            return self.active
        return next((s for s in self.queue if s.id == session_id), None)

    def _end(self, s: Session, reason: str) -> None:
        if self.active is s:
            self.active = None
        elif s in self.queue:
            self.queue.remove(s)
        self._ended[s.id] = reason
        while len(self._ended) > self._ended_memory:
            self._ended.popitem(last=False)
        if s.job is not None:  # çalışan işi durdur; geçmiş ve analiz oturumla birlikte silinir
            s.job.cancel()
            s.job = None
        s.agent_history.clear()
        s.analysis = None

    def _promote(self) -> None:
        if self.active is None and self.queue:
            self.active = self.queue.pop(0)
            self.active.last_seen = self._clock()
