"""Core types — the contracts that every other module hangs off of.

If you change anything here, expect downstream churn in probe.py, state.py,
notifier.py, and the tests. Keep this file small and stable.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Deque, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class HealthState(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class ServiceConfig(BaseModel):
    """One monitored service, loaded from YAML config."""

    name: str = Field(..., min_length=1)
    url: HttpUrl
    expect_text_contains: Optional[str] = None
    timeout_seconds: Optional[int] = Field(None, gt=0)
    runbook_url: Optional[HttpUrl] = None


class AppConfig(BaseModel):
    """Top-level config loaded from YAML."""

    check_interval_seconds: int = Field(60, gt=0)
    default_timeout_seconds: int = Field(10, gt=0)
    fail_threshold: int = Field(2, gt=0)
    repeat_interval_seconds: int = Field(0, ge=0)  # 0 = off
    services: list[ServiceConfig] = Field(default_factory=list)


class ProbeResult(BaseModel):
    """The outcome of a single probe attempt."""

    timestamp: datetime
    ok: bool
    status_code: Optional[int] = None
    body_snippet: Optional[str] = None  # first 200 chars on failure
    error: Optional[str] = None  # e.g. "connection refused", "timeout"

    @classmethod
    def success(cls, status_code: int) -> "ProbeResult":
        return cls(timestamp=datetime.now(timezone.utc), ok=True, status_code=status_code)

    @classmethod
    def failure(
        cls,
        *,
        status_code: Optional[int] = None,
        body_snippet: Optional[str] = None,
        error: Optional[str] = None,
    ) -> "ProbeResult":
        return cls(
            timestamp=datetime.now(timezone.utc),
            ok=False,
            status_code=status_code,
            body_snippet=body_snippet,
            error=error,
        )


class ServiceState(BaseModel):
    """Tracked state for one service. Lives in memory; lost on restart (by design)."""

    state: HealthState = HealthState.UP
    since: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_alerted_at: Optional[datetime] = None
    fail_count: int = 0
    # Ring buffer of recent probe results. We render the last 3 in alerts.
    recent_attempts: Deque[ProbeResult] = Field(default_factory=lambda: deque(maxlen=3))

    model_config = {"arbitrary_types_allowed": True}


class StateTransition(BaseModel):
    """What the state machine emits when state changes (or repeat fires).

    The orchestrator turns one of these into an AlertPayload + sends via the notifier.
    """

    service: ServiceConfig
    kind: Literal["down", "recovery", "repeat"]
    state_before: HealthState
    state_after: HealthState
    triggering_result: ProbeResult
    state: ServiceState  # snapshot at transition time (POST-mutation)
    duration_seconds: Optional[float] = None  # set on recovery: how long was DOWN


class AlertPayload(BaseModel):
    """The rendered Webex message. notifier.py turns a StateTransition into this."""

    markdown: str
