from app.sessions import SessionManager


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def make(idle=300, ttl=30):
    c = Clock()
    return SessionManager(idle_timeout=idle, queue_ttl=ttl, clock=c), c


def test_first_user_is_active_second_is_queued():
    m, _ = make()
    a, b = m.join(), m.join()
    assert m.status(a.id)["status"] == "active"
    assert m.status(b.id) == {"status": "queued", "position": 1}


def test_only_active_session_can_chat():
    m, _ = make()
    a, b = m.join(), m.join()
    assert m.get_active(a.id) is a
    assert m.get_active(b.id) is None


def test_leave_promotes_next_in_fifo_order():
    m, _ = make()
    a, b, c = m.join(), m.join(), m.join()
    assert m.status(c.id)["position"] == 2
    assert m.leave(a.id)
    assert m.status(b.id)["status"] == "active"
    assert m.status(c.id)["position"] == 1
    assert m.status(a.id) == {"status": "ended", "reason": "left"}


def test_idle_timeout_releases_slot_and_promotes():
    m, clock = make(idle=300)
    a, b = m.join(), m.join()
    for _ in range(14):  # b her 20 sn'de bir sırada olduğunu bildirir (toplam 280 sn)
        clock.advance(20)
        assert m.status(b.id)["status"] == "queued"
    clock.advance(19)  # 299 sn: a hâlâ süresi içinde
    assert m.status(b.id)["status"] == "queued"
    assert m.status(a.id)["status"] == "active"
    clock.advance(2)  # 301 sn: a'nın süresi doldu
    assert m.status(b.id)["status"] == "active"
    assert m.status(a.id) == {"status": "ended", "reason": "idle"}


def test_keepalive_resets_idle_timer():
    m, clock = make(idle=300)
    a = m.join()
    clock.advance(250)
    assert m.touch(a.id)
    clock.advance(250)
    m.sweep()
    assert m.status(a.id)["status"] == "active"


def test_busy_session_is_not_timed_out_and_timer_restarts_after():
    m, clock = make(idle=300)
    a = m.join()
    with m.busy(a):
        clock.advance(1000)  # model/ML çalışıyor
        m.sweep()
        assert m.status(a.id)["status"] == "active"
        assert m.status(a.id)["idle_remaining"] is None
    m.sweep()
    assert m.status(a.id)["status"] == "active"  # sayaç busy bitince yeniden başladı
    clock.advance(301)
    m.sweep()
    assert m.status(a.id)["status"] == "ended"


def test_queued_user_who_disappears_is_dropped():
    m, clock = make(ttl=30)
    a, b, c = m.join(), m.join(), m.join()
    clock.advance(31)
    m.status(c.id)  # sadece c görünür kalıyor
    m.sweep()
    assert m.status(b.id) == {"status": "ended", "reason": "queue_timeout"}
    assert m.status(c.id) == {"status": "queued", "position": 1}


def test_polling_keeps_queued_user_alive():
    m, clock = make(ttl=30)
    m.join()
    b = m.join()
    for _ in range(5):
        clock.advance(20)
        assert m.status(b.id)["status"] == "queued"


def test_history_is_cleared_when_session_ends():
    m, _ = make()
    a = m.join()
    a.history.append({"role": "user", "content": "merhaba"})
    m.leave(a.id)
    assert a.history == []


def test_unknown_session():
    m, _ = make()
    assert m.status("yok") == {"status": None}
    assert m.leave("yok") is False
    assert m.touch("yok") is False
