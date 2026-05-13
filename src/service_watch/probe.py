"""HTTPS probe — turns one (ServiceConfig, timeout) into one ProbeResult."""

from __future__ import annotations

import httpx

from .types import ProbeResult, ServiceConfig


class HttpProber:
    def probe(self, service: ServiceConfig, default_timeout_seconds: int) -> ProbeResult:
        timeout = (
            service.timeout_seconds
            if service.timeout_seconds is not None
            else default_timeout_seconds
        )

        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, verify=True) as client:
                resp = client.get(str(service.url))

            if 200 <= resp.status_code < 300:
                if service.expect_text_contains is None or service.expect_text_contains in resp.text:
                    return ProbeResult.success(resp.status_code)
                return ProbeResult.failure(
                    status_code=resp.status_code,
                    body_snippet=resp.text[:200],
                    error=f"expected text {service.expect_text_contains!r} not found in body",
                )

            return ProbeResult.failure(
                status_code=resp.status_code,
                body_snippet=resp.text[:200],
                error=f"HTTP {resp.status_code}",
            )

        except httpx.TimeoutException:
            return ProbeResult.failure(error=f"timeout after {timeout}s")
        except httpx.ConnectError:
            return ProbeResult.failure(error="connection refused or unreachable")
        except httpx.HTTPError as e:
            return ProbeResult.failure(error=f"http error: {type(e).__name__}: {e}")
        except Exception as e:
            return ProbeResult.failure(error=f"unexpected: {type(e).__name__}: {e}")
