"""Idle shutdown: no heartbeat for `idle_shutdown_minutes` and no job running (spec §4.3)."""

from __future__ import annotations

from dental_image_organizer.api.lifecycle import IdleWatch

MINUTES = 30


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_expires_after_the_timeout_without_a_beat() -> None:
    clock = FakeClock()
    idle = IdleWatch(lambda: MINUTES, clock)

    clock.advance(MINUTES * 60 - 1)
    assert not idle.expired(jobs_active=False)
    clock.advance(1)
    assert idle.expired(jobs_active=False)


def test_a_beat_resets_the_timer() -> None:
    clock = FakeClock()
    idle = IdleWatch(lambda: MINUTES, clock)

    clock.advance(MINUTES * 60 - 60)
    idle.beat()
    clock.advance(MINUTES * 60 - 60)
    assert not idle.expired(jobs_active=False)
    clock.advance(60)
    assert idle.expired(jobs_active=False)


def test_never_expires_while_a_job_runs() -> None:
    clock = FakeClock()
    idle = IdleWatch(lambda: MINUTES, clock)

    clock.advance(24 * 3600)
    assert not idle.expired(jobs_active=True)
    assert idle.expired(jobs_active=False)


def test_zero_minutes_never_expires() -> None:
    clock = FakeClock()
    minutes = [0]
    idle = IdleWatch(lambda: minutes[0], clock)

    clock.advance(24 * 3600)
    assert not idle.expired(jobs_active=False)
    # Read on every call, so a settings change applies without a restart.
    minutes[0] = MINUTES
    assert idle.expired(jobs_active=False)
