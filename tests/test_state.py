"""Tests for the state machine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from service_watch.state import StateMachineImpl
from service_watch.types import HealthState, ProbeResult, ServiceConfig, ServiceState


@pytest.fixture
def service() -> ServiceConfig:
    return ServiceConfig(name="svc", url="https://example.com/")


@pytest.fixture
def sm() -> StateMachineImpl:
    return StateMachineImpl()


def test_up_to_up_on_success(service, sm):
    state = ServiceState()
    result = ProbeResult.success(200)
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=0)
    assert transition is None
    assert state.state == HealthState.UP
    assert state.fail_count == 0


def test_up_below_threshold_no_transition(service, sm):
    state = ServiceState()
    result = ProbeResult.failure(error="boom")
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=0)
    assert transition is None
    assert state.state == HealthState.UP
    assert state.fail_count == 1


def test_up_to_down_at_threshold(service, sm):
    state = ServiceState(fail_count=1)
    result = ProbeResult.failure(error="boom")
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=0)
    assert transition is not None
    assert transition.kind == "down"
    assert transition.state_before == HealthState.UP
    assert transition.state_after == HealthState.DOWN
    assert state.state == HealthState.DOWN


def test_down_to_up_recovery_sets_duration(service, sm):
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    state = ServiceState(state=HealthState.DOWN, since=past, last_alerted_at=past, fail_count=5)
    result = ProbeResult.success(200)
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=0)
    assert transition is not None
    assert transition.kind == "recovery"
    assert transition.duration_seconds is not None
    assert 119 < transition.duration_seconds < 121
    assert state.state == HealthState.UP
    assert state.fail_count == 0


def test_down_repeat_disabled_no_transition(service, sm):
    state = ServiceState(
        state=HealthState.DOWN,
        last_alerted_at=datetime.now(timezone.utc),
        fail_count=5,
    )
    result = ProbeResult.failure(error="still down")
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=0)
    assert transition is None
    assert state.fail_count == 6


def test_down_repeat_fires_when_elapsed_exceeds_interval(service, sm):
    past = datetime.now(timezone.utc) - timedelta(seconds=3700)
    state = ServiceState(state=HealthState.DOWN, since=past, last_alerted_at=past, fail_count=5)
    result = ProbeResult.failure(error="still down")
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=3600)
    assert transition is not None
    assert transition.kind == "repeat"
    assert transition.state_before == HealthState.DOWN
    assert transition.state_after == HealthState.DOWN


def test_down_repeat_silent_when_not_yet_elapsed(service, sm):
    recent = datetime.now(timezone.utc) - timedelta(seconds=60)
    state = ServiceState(state=HealthState.DOWN, last_alerted_at=recent, fail_count=5)
    result = ProbeResult.failure(error="still down")
    transition = sm.apply(service, state, result, fail_threshold=2, repeat_interval_seconds=3600)
    assert transition is None


def test_recent_attempts_ring_buffer(service, sm):
    state = ServiceState()
    for _ in range(5):
        sm.apply(service, state, ProbeResult.success(200), 2, 0)
    assert len(state.recent_attempts) == 3
