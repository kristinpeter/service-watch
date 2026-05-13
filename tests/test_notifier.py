"""Tests for the alert formatter. Webex HTTP transport is not tested here (would need a fake)."""

from __future__ import annotations

from datetime import datetime, timezone

from service_watch.notifier import format_alert
from service_watch.types import (
    HealthState,
    ProbeResult,
    ServiceConfig,
    ServiceState,
    StateTransition,
)


def _make_transition(
    *,
    kind: str,
    state_before: HealthState,
    state_after: HealthState,
    result: ProbeResult,
    service: ServiceConfig | None = None,
    duration_seconds: float | None = None,
    fail_count: int = 2,
    since: datetime | None = None,
) -> StateTransition:
    service = service or ServiceConfig(name="freeipa-web", url="https://ipa.example.com/")
    state = ServiceState(
        state=state_after,
        since=since or datetime.now(timezone.utc),
        fail_count=fail_count,
    )
    state.recent_attempts.append(result)
    return StateTransition(
        service=service,
        kind=kind,  # type: ignore[arg-type]
        state_before=state_before,
        state_after=state_after,
        triggering_result=result,
        state=state,
        duration_seconds=duration_seconds,
    )


def test_down_alert_includes_header_url_and_error():
    result = ProbeResult.failure(status_code=503, body_snippet="<html>503</html>", error="HTTP 503")
    t = _make_transition(
        kind="down", state_before=HealthState.UP, state_after=HealthState.DOWN, result=result
    )
    md = format_alert(t)
    assert "🔴" in md
    assert "freeipa-web" in md
    assert "DOWN" in md
    assert "https://ipa.example.com/" in md
    assert "HTTP 503" in md
    assert "Response body" in md
    assert "<html>503</html>" in md
    assert "Last 3 attempts" in md


def test_down_alert_with_runbook_omits_fallback_triage():
    service = ServiceConfig(
        name="svc",
        url="https://x.example.com/",
        runbook_url="https://wiki.example.com/runbooks/svc",
    )
    result = ProbeResult.failure(error="connection refused")
    t = _make_transition(
        kind="down",
        state_before=HealthState.UP,
        state_after=HealthState.DOWN,
        result=result,
        service=service,
    )
    md = format_alert(t)
    assert "📖 **Runbook:**" in md
    assert "https://wiki.example.com/runbooks/svc" in md
    assert "Suggested triage" not in md


def test_down_alert_without_runbook_includes_fallback_triage():
    result = ProbeResult.failure(error="connection refused")
    t = _make_transition(
        kind="down", state_before=HealthState.UP, state_after=HealthState.DOWN, result=result
    )
    md = format_alert(t)
    assert "Suggested triage" in md
    assert "ipa-healthcheck" in md
    assert "Runbook" not in md


def test_recovery_alert_includes_duration():
    result = ProbeResult.success(200)
    t = _make_transition(
        kind="recovery",
        state_before=HealthState.DOWN,
        state_after=HealthState.UP,
        result=result,
        duration_seconds=724.0,
    )
    md = format_alert(t)
    assert "✅" in md
    assert "recovered" in md
    assert "12m 4s" in md  # 724s = 12m 4s


def test_repeat_alert_marked_as_reminder():
    result = ProbeResult.failure(error="still down")
    t = _make_transition(
        kind="repeat", state_before=HealthState.DOWN, state_after=HealthState.DOWN, result=result
    )
    md = format_alert(t)
    assert "STILL DOWN" in md
    assert "reminder" in md
    assert "Down since" in md
