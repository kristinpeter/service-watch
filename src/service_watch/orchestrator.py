"""The main loop. Glues probe + state + notifier together.

This is the orchestration core. The probe, state, and notifier modules are
intentionally pure-ish so that this module is the only one with side effects
on a per-tick basis (network calls, time progression, alerting).
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from .types import (
    AppConfig,
    ProbeResult,
    ServiceConfig,
    ServiceState,
    StateTransition,
)

log = logging.getLogger(__name__)


class Prober(Protocol):
    """Implemented by probe.py. Defined here so orchestrator depends on the interface, not the impl."""

    def probe(self, service: ServiceConfig, default_timeout_seconds: int) -> ProbeResult: ...


class Notifier(Protocol):
    """Implemented by notifier.py."""

    def notify(self, transition: StateTransition) -> None: ...


class StateMachine(Protocol):
    """Implemented by state.py. Returns a StateTransition iff an alert should fire."""

    def apply(
        self,
        service: ServiceConfig,
        current: ServiceState,
        result: ProbeResult,
        fail_threshold: int,
        repeat_interval_seconds: int,
    ) -> StateTransition | None: ...


class Orchestrator:
    def __init__(
        self,
        config: AppConfig,
        prober: Prober,
        state_machine: StateMachine,
        notifier: Notifier,
        *,
        sleep: callable = time.sleep,  # injectable for tests
    ) -> None:
        self.config = config
        self.prober = prober
        self.state_machine = state_machine
        self.notifier = notifier
        self.sleep = sleep
        self.state: dict[str, ServiceState] = {s.name: ServiceState() for s in config.services}

    def run_forever(self) -> None:
        """Main loop. Exits only on signal."""
        log.info("service-watch starting; %d services", len(self.config.services))
        while True:
            self.tick()
            self.sleep(self.config.check_interval_seconds)

    def tick(self) -> None:
        """One pass over all services. Public for testability."""
        for service in self.config.services:
            try:
                result = self.prober.probe(service, self.config.default_timeout_seconds)
            except Exception:
                log.exception("probe blew up for %s", service.name)
                continue

            current = self.state[service.name]
            transition = self.state_machine.apply(
                service,
                current,
                result,
                fail_threshold=self.config.fail_threshold,
                repeat_interval_seconds=self.config.repeat_interval_seconds,
            )
            # state.py mutates `current` in place via apply(); we keep our ref.
            if transition is not None:
                try:
                    self.notifier.notify(transition)
                except Exception:
                    log.exception("notifier failed for %s", service.name)
