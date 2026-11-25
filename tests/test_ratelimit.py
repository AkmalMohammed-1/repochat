from api.core.ratelimit import RateLimiter


def test_sliding_window():
    rl = RateLimiter()
    assert all(rl.check("chat", "1.2.3.4", limit=3) for _ in range(3))
    assert not rl.check("chat", "1.2.3.4", limit=3)
    assert rl.check("chat", "5.6.7.8", limit=3)  # per IP
    assert rl.check("index", "1.2.3.4", limit=3)  # per bucket
    assert rl.check("chat", "1.2.3.4", limit=3, window=0.0)  # old hits expire
