"""AlertTracker fires once per stuck episode, resets on state change."""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncio

from mower.alerts import AlertTracker

# We use asyncio.run directly rather than pull in pytest-asyncio for one
# test module. Keeps the dev-dep list minimal.


def _run(coro):
    return asyncio.run(coro)


def test_fires_once_after_threshold():
    fires: list[str] = []

    async def fake(msg: str) -> None:
        fires.append(msg)

    async def scenario():
        t = AlertTracker(threshold_seconds=180.0, notifiers=[fake])
        base = datetime(2026, 6, 29, 22, 0, 0)
        await t.observe("idle_off_dock", 27.0, now=base)
        await t.observe("idle_off_dock", 27.0,
                        now=base + timedelta(seconds=120))
        await t.observe("idle_off_dock", 27.0,
                        now=base + timedelta(seconds=181))
        await t.observe("idle_off_dock", 27.0,
                        now=base + timedelta(seconds=240))

    _run(scenario())
    assert len(fires) == 1
    assert "idle_off_dock" in fires[0]


def test_resets_on_state_change():
    fires: list[str] = []

    async def fake(msg: str) -> None:
        fires.append(msg)

    async def scenario():
        t = AlertTracker(threshold_seconds=60.0, notifiers=[fake])
        base = datetime(2026, 6, 29, 22, 0, 0)
        await t.observe("idle_off_dock", 27.0, now=base)
        await t.observe("idle_off_dock", 27.0,
                        now=base + timedelta(seconds=70))  # fires
        await t.observe("charging", 27.5,
                        now=base + timedelta(seconds=120))  # clears
        await t.observe("idle_off_dock", 27.0,
                        now=base + timedelta(seconds=200))  # new episode
        await t.observe("idle_off_dock", 27.0,
                        now=base + timedelta(seconds=270))  # fires again

    _run(scenario())
    assert len(fires) == 2


def test_status_reflects_alert_active():
    async def scenario():
        t = AlertTracker(threshold_seconds=60.0)
        base = datetime(2026, 6, 29, 22, 0, 0)
        await t.observe("idle_off_dock", 27.0, now=base)
        status_pre = t.status(now=base + timedelta(seconds=30))
        status_post = t.status(now=base + timedelta(seconds=61))
        return status_pre, status_post

    pre, post = _run(scenario())
    assert pre.alert_active is False
    assert post.alert_active is True
    assert post.state == "idle_off_dock"


def test_non_stuck_states_never_fire():
    fires: list[str] = []

    async def fake(msg: str) -> None:
        fires.append(msg)

    async def scenario():
        t = AlertTracker(threshold_seconds=10.0, notifiers=[fake])
        base = datetime(2026, 6, 29, 22, 0, 0)
        await t.observe("mowing", 24.0, now=base)
        await t.observe("mowing", 24.0, now=base + timedelta(seconds=300))
        await t.observe("charging", 26.0, now=base + timedelta(seconds=600))
        await t.observe("docked_full", 27.5, now=base + timedelta(seconds=900))

    _run(scenario())
    assert fires == []
