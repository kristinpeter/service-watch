"""State machine: maps (current state, new ProbeResult) → optional StateTransition."""

from __future__ import annotations

from datetime import datetime, timezone

from .types import HealthState, ProbeResult, ServiceConfig, ServiceState, StateTransition


class StateMachineImpl:
    def apply(
        self,
        service: ServiceConfig,
        current: ServiceState,
        result: ProbeResult,
        fail_threshold: int,
        repeat_interval_seconds: int,
    ) -> StateTransition | None:
        current.recent_attempts.append(result)
        now = datetime.now(timezone.utc)

        if result.ok:
            if current.state == HealthState.UP:
                current.fail_count = 0
                return None
            state_before = current.state
            down_since = current.since  # capture BEFORE we overwrite it
            current.state = HealthState.UP
            current.since = now
            current.last_alerted_at = now
            current.fail_count = 0
            return StateTransition(
                service=service,
                kind="recovery",
                state_before=state_before,
                state_after=HealthState.UP,
                triggering_result=result,
                state=current.model_copy(),
                duration_seconds=(now - down_since).total_seconds(),
            )

        current.fail_count += 1
        if current.state == HealthState.UP:
            if current.fail_count >= fail_threshold:
                state_before = current.state
                current.state = HealthState.DOWN
                current.since = now
                current.last_alerted_at = now
                return StateTransition(
                    service=service,
                    kind="down",
                    state_before=state_before,
                    state_after=HealthState.DOWN,
                    triggering_result=result,
                    state=current.model_copy(),
                )
            return None

        if repeat_interval_seconds > 0 and current.last_alerted_at is not None:
            elapsed = (now - current.last_alerted_at).total_seconds()
            if elapsed >= repeat_interval_seconds:
                current.last_alerted_at = now
                return StateTransition(
                    service=service,
                    kind="repeat",
                    state_before=HealthState.DOWN,
                    state_after=HealthState.DOWN,
                    triggering_result=result,
                    state=current.model_copy(),
                )
        return None
