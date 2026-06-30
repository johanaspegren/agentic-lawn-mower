"""State-transition alerting.

Watches the decoded state stream and fires a single notification when the
mower has been in a "needs help" state (currently just ``idle_off_dock``)
for longer than a configurable threshold.

Designed to be transport-agnostic — :class:`AlertTracker` takes a list of
*notifier* callables. :func:`webhook_notifier` builds one for Slack or
Discord (auto-detected from the URL). Add more notifiers later (ntfy,
Pushover, email, etc.) without touching the tracker.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

# A notifier is async so we don't block the asyncio loop on the HTTP call.
Notifier = Callable[[str], Awaitable[None]]

#: States that count as "the mower needs help" if they persist.
STUCK_STATES = frozenset({"idle_off_dock"})


@dataclass
class AlertStatus:
    """Snapshot of the tracker, suitable for /api/status."""
    state: str | None = None
    state_since_ts: str | None = None
    duration_sec: float = 0.0
    alert_active: bool = False
    last_alert_ts: str | None = None
    last_alert_message: str | None = None


class AlertTracker:
    """Track state transitions and fire alerts on stuck-state persistence."""

    def __init__(self, threshold_seconds: float = 180.0,
                 stuck_states: frozenset[str] = STUCK_STATES,
                 notifiers: list[Notifier] | None = None):
        self.threshold_seconds = threshold_seconds
        self.stuck_states = stuck_states
        self.notifiers = notifiers or []
        self._current_state: str | None = None
        self._state_entered_at: datetime | None = None
        self._alert_fired = False
        self._last_alert_ts: datetime | None = None
        self._last_alert_message: str | None = None

    async def observe(self, state: str | None,
                      voltage_v: float | None = None,
                      now: datetime | None = None) -> AlertStatus:
        """Process a state observation. May fire notifiers as a side-effect."""
        now = now or datetime.now()
        if state is None:
            return self.status(now=now)

        if state != self._current_state:
            self._current_state = state
            self._state_entered_at = now
            self._alert_fired = False

        if (
            state in self.stuck_states
            and self._state_entered_at is not None
            and (now - self._state_entered_at).total_seconds() >= self.threshold_seconds
            and not self._alert_fired
        ):
            duration = (now - self._state_entered_at).total_seconds()
            mins, secs = divmod(int(duration), 60)
            msg = (
                f":lawn_mower: Mower is stuck — `{state}` for "
                f"{mins}m {secs}s"
                + (f", battery {voltage_v:.2f} V" if voltage_v is not None else "")
            )
            self._alert_fired = True
            self._last_alert_ts = now
            self._last_alert_message = msg
            await self._fire(msg)

        return self.status(now=now)

    def status(self, *, now: datetime | None = None) -> AlertStatus:
        now = now or datetime.now()
        duration = (
            (now - self._state_entered_at).total_seconds()
            if self._state_entered_at is not None else 0.0
        )
        return AlertStatus(
            state=self._current_state,
            state_since_ts=(
                self._state_entered_at.isoformat(timespec="seconds")
                if self._state_entered_at is not None else None
            ),
            duration_sec=duration,
            alert_active=(
                self._current_state in self.stuck_states
                and duration >= self.threshold_seconds
            ),
            last_alert_ts=(
                self._last_alert_ts.isoformat(timespec="seconds")
                if self._last_alert_ts is not None else None
            ),
            last_alert_message=self._last_alert_message,
        )

    async def _fire(self, msg: str) -> None:
        for n in self.notifiers:
            try:
                await n(msg)
            except Exception as e:
                print(f"[alerts] notifier failed: {e}", file=sys.stderr)


# --- notifiers ---------------------------------------------------------------


def webhook_notifier(url: str) -> Notifier:
    """Build a notifier that POSTs to a Slack or Discord incoming webhook.

    Slack expects ``{"text": "..."}``, Discord expects ``{"content": "..."}``.
    The function picks the right shape based on the URL host.
    """
    is_discord = "discord.com" in url or "discordapp.com" in url
    key = "content" if is_discord else "text"

    def _post_sync(message: str) -> None:
        body = json.dumps({key: message}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            # Slack returns 200/"ok"; Discord returns 204. Accept either.
            if resp.status not in (200, 204):
                raise RuntimeError(f"webhook returned {resp.status}: "
                                   f"{resp.read()[:200]!r}")

    async def notify(message: str) -> None:
        # urllib is sync — push it to a worker thread.
        await asyncio.to_thread(_post_sync, message)

    return notify
