"""Webex notifier: formats a StateTransition into markdown and posts it."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from .types import ProbeResult, StateTransition

# Override via env for local testing against a mock receiver.
WEBEX_API_URL = os.environ.get("WEBEX_API_URL", "https://webexapis.com/v1/messages")

FALLBACK_TRIAGE = (
    "**Suggested triage** (no runbook configured for this service):\n"
    "1. `ssh <host>`\n"
    "2. `sudo systemctl status httpd` — check the service\n"
    "3. `sudo journalctl -u httpd --since \"10 minutes ago\"` — recent errors\n"
    "4. `sudo systemctl restart httpd` — restart if needed\n"
    "5. For FreeIPA hosts: `sudo ipa-healthcheck --output-type human`"
)


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_duration(total_seconds: float) -> str:
    seconds = int(total_seconds)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _describe_failure(result: ProbeResult) -> str:
    if result.error:
        return result.error
    if result.status_code is not None:
        return f"HTTP {result.status_code}"
    return "(no error detail)"


def _render_recent_attempts(transition: StateTransition) -> str:
    if not transition.state.recent_attempts:
        return "- (none recorded)"
    lines = []
    for r in transition.state.recent_attempts:
        ts = r.timestamp.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
        if r.ok:
            lines.append(f"- {ts} — {r.status_code} OK")
        else:
            lines.append(f"- {ts} — {_describe_failure(r)}")
    return "\n".join(lines)


def _render_runbook_or_fallback(transition: StateTransition) -> str:
    if transition.service.runbook_url is not None:
        url = str(transition.service.runbook_url)
        return f"📖 **Runbook:** [{url}]({url})"
    return FALLBACK_TRIAGE


def format_alert(transition: StateTransition) -> str:
    """Render a StateTransition into a Webex-flavored markdown message."""
    service = transition.service
    url = str(service.url)
    result = transition.triggering_result

    if transition.kind == "recovery":
        duration = (
            _fmt_duration(transition.duration_seconds)
            if transition.duration_seconds is not None
            else "unknown"
        )
        return (
            f"✅ **{service.name} — recovered**\n"
            f"URL: [{url}]({url})\n"
            f"Was down for: {duration}\n"
            f"Recovered at: {_fmt_ts(result.timestamp)}"
        )

    # DOWN or REPEAT — same payload shape, different header
    if transition.kind == "down":
        header = f"🔴 **{service.name} — DOWN**"
        timing_line = f"First seen: {_fmt_ts(transition.state.since)}"
    else:  # repeat
        header = f"🔴 **{service.name} — STILL DOWN** (reminder)"
        timing_line = f"Down since: {_fmt_ts(transition.state.since)}"

    body_block = (
        f"\n\n**Response body (first 200 chars):**\n> {result.body_snippet.strip()[:200]}"
        if result.body_snippet
        else ""
    )

    return (
        f"{header}\n"
        f"URL: [{url}]({url})\n"
        f"Failed: {transition.state.fail_count} consecutive probes\n"
        f"{timing_line}\n"
        f"\n**Last error:** {_describe_failure(result)}"
        f"{body_block}"
        f"\n\n**Last 3 attempts:**\n{_render_recent_attempts(transition)}"
        f"\n\n{_render_runbook_or_fallback(transition)}"
    )


class WebexNotifier:
    def __init__(self, token: str, space_id: str) -> None:
        self._token = token
        self._space_id = space_id

    def notify(self, transition: StateTransition) -> None:
        markdown = format_alert(transition)
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                WEBEX_API_URL,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"roomId": self._space_id, "markdown": markdown},
            )
            resp.raise_for_status()
