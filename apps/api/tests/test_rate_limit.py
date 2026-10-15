from app.rate_limit import LoginLimiter


def test_limit_is_per_actor_and_recovers_after_window():
    limiter = LoginLimiter('private-test-key', limit=2, window=10)
    assert limiter.allow('actor-a',0)
    assert limiter.allow('actor-a',1)
    assert not limiter.allow('actor-a',2)
    assert limiter.allow('actor-b',2)
    assert limiter.allow('actor-a',10)
    assert not limiter.allow('actor-a',10)
    assert 'actor-a' not in limiter.entries


def test_capacity_blocks_growth_without_evicting_active_limits():
    limiter=LoginLimiter('private-test-key',limit=1,window=10,capacity=2)
    assert limiter.allow('a',0)
    assert limiter.allow('b',0)
    assert not limiter.allow('c',1)
    assert not limiter.allow('a',1)
    assert len(limiter.entries)==2
    assert limiter.allow('c',11)
