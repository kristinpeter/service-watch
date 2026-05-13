# service-watch — design

**Status:** draft v0 (2026-05-13)
**Owner:** Kristin Peter
**Where it runs (production):** OpenShift cluster, separate from this `ai` workstation
**Where the code is developed:** here (this repo), with placeholders for all work-specific config

## Problem

Several Linux-VM-hosted services (starting with FreeIPA's Apache web UI) silently die. Currently the only signal is "I tried to open the page and it didn't load." Need active monitoring + actionable alerts to a Webex space.

## Scope — v1

**In scope:**
- HTTPS health probes against a configurable list of services
- State tracking (UP / DOWN) with first-failure alerting
- Dedupe: no repeat alerts while in DOWN state
- Recovery notification when state returns to UP
- Webex bot posts to a dedicated space with actionable triage info
- Runs as an OpenShift Deployment with ConfigMap-driven service list
- Adding a new service = edit ConfigMap + rollout restart

**Out of scope (v2+):**
- Auto-remediation (running fix commands)
- `ipa-healthcheck` integration (requires agent on FreeIPA host)
- Non-HTTPS checks (TCP port, SSH command exec, etc.)
- Web UI / dashboard
- Persistent metrics (Prometheus export)
- On-call rotation, escalation, paging integrations

## Architecture

Single-process service. Loop, simple state in memory (good enough; state lost on restart is acceptable — first re-check after restart will re-alert on anything still DOWN).

```
┌─────────────────────────────────────────────────────────┐
│  OpenShift namespace: infra-watch                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  service-watch pod (Deployment, 1 replica)       │   │
│  │                                                   │   │
│  │   loop every CHECK_INTERVAL (default 60s):        │   │
│  │     for each service in config:                   │   │
│  │       result = probe(service)                     │   │
│  │       state_change = transition(service, result)  │   │
│  │       if state_change: notify(state_change)       │   │
│  │                                                   │   │
│  └──────────────────────────────────────────────────┘   │
│           │                          │                   │
│           ▼                          ▼                   │
│   ConfigMap                    Secret                    │
│   (service list)               (WEBEX_BOT_TOKEN)         │
│                                                          │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼ (HTTPS POST)
            ┌─────────────────────────┐
            │  Webex Messages API     │
            │  → posts to space        │
            └─────────────────────────┘
                        │
                        ▼
                  user sees alert
```

Probes from the OpenShift pod **out** to the Linux VMs (internal network). No agent needed on monitored hosts.

## Components

### 1. Health checker

For each service in config, performs:
- **HTTPS GET** with `verify=True` (TLS validation on by default)
- Follow redirects up to N hops
- Timeout: configurable per-service, default 10s
- Accept: 2xx (and optionally 3xx if `follow_redirects: true` and final is 2xx)
- Optional `expect_text_contains`: response body must contain this string (e.g., "Identity Management" for FreeIPA UI) — catches "Apache is up but serving wrong content" failure mode

A service is DOWN if N consecutive probes fail. `N=2` default — avoids alert on a single transient blip.

### 2. State store

In-memory dict per service:

```
{
  state: "UP" | "DOWN",
  since: <timestamp of last state transition>,
  last_alerted_at: <timestamp of most recent alert sent for this state>,
  fail_count: <consecutive failures>,
  recent_attempts: [<ring buffer of last 3 (timestamp, result) pairs>]
}
```

`recent_attempts` is appended on every probe; used to populate the "Last 3 attempts" section of the alert payload.

Persistence: none. Pod restart re-evaluates from scratch.
- Pro: simple, no PVC, no DB
- Con: any DOWN services re-alert on restart (acceptable; restart is rare and the re-alert is actually useful)

### 3. Notifier (Webex)

- Library: `webexteamssdk` (Python, official) or raw `requests` against `https://webexapis.com/v1/messages`
- Posts markdown messages to a configured `WEBEX_SPACE_ID`
- Auth: bot token from `WEBEX_BOT_TOKEN` secret

**Alert payload (DOWN):**

```
🔴 **FreeIPA web — DOWN**
URL: [https://ipa.example.com/](https://ipa.example.com/)
Failed: 2 consecutive probes
First seen: 2026-05-13 18:42:11 UTC

**Last error:** HTTP 503 Service Unavailable
**Response body (first 200 chars):**
> <html><body><h1>Service Unavailable</h1>The server is temporarily…

**Last 3 attempts:**
- 18:42:11 UTC — 503
- 18:43:11 UTC — 503
- 18:44:11 UTC — connection refused

📖 **Runbook:** [https://wiki.example.com/runbooks/freeipa-web](https://wiki.example.com/runbooks/freeipa-web)

(Fallback triage if no runbook configured for the service:
1. `ssh ipa-host`
2. `sudo systemctl status httpd` — check apache
3. `sudo journalctl -u httpd --since "10 minutes ago"` — recent errors
4. `sudo systemctl restart httpd` — restart if needed
5. `sudo ipa-healthcheck --output-type human` — deeper diagnostic)
```

Notes on rendering:
- All URLs use markdown link syntax `[label](url)` so Webex renders them clickable.
- If the service config has `runbook_url`, the runbook line is shown prominently and the embedded fallback triage is omitted.
- If no `runbook_url`, the fallback triage list is included.
- Response body snippet is truncated to 200 chars and rendered as a markdown blockquote.

**Recovery payload (UP):**

```
✅ **FreeIPA web — recovered**
URL: https://ipa.example.com/
Was down for: 12m 04s
```

Markdown formatting works in Webex; the bot post can include code blocks and bold.

### 4. Config

`ConfigMap` mounted at `/etc/service-watch/config.yaml`:

```yaml
check_interval_seconds: 60
default_timeout_seconds: 10
fail_threshold: 2

# Repeat alerts while a service remains DOWN.
# Off by default. Set to a positive integer to enable (recommended: 3600 = 1h).
# Use case: makes sure long-running outages stay visible if the first alert
# gets buried in Webex chatter.
repeat_interval_seconds: 0

services:
  - name: freeipa-web
    url: https://ipa.example.com/
    expect_text_contains: "Identity Management"
    timeout_seconds: 15
    runbook_url: https://wiki.example.com/runbooks/freeipa-web   # optional
  - name: gitlab-pages
    url: https://pages.example.com/
    # no runbook_url — alert will include fallback triage steps
  # add more by editing this ConfigMap and rolling out
```

`Secret`: holds `WEBEX_BOT_TOKEN` and `WEBEX_SPACE_ID`.

### 5. Implementation language

**Python.** Reasons:
- Fast iteration during development
- `requests` and `webexteamssdk` are mature
- Easy to read for future you / handover
- Small footprint in Alpine-based container (~50 MB image)

Alternative: Go (single static binary, smaller image) — better for production scale but overkill for v1 (single replica, ~10 services).

## OpenShift deployment

Manifest set (in a separate work repo, not in this design doc):
- `Namespace: infra-watch`
- `Deployment` (1 replica, restartPolicy Always)
- `ConfigMap` (service list)
- `Secret` (Webex creds)
- `NetworkPolicy` allowing egress to monitored hosts + webexapis.com
- No Service / Route needed (pod doesn't accept traffic)
- Resource requests: `cpu: 50m, memory: 64Mi` (probably overkill for this workload)

Build: build container image, push to the internal Harbor registry, pull from there. CI on GitLab probably.

## Operations

- **Logs**: stdout/stderr, captured by OpenShift logging stack. Each probe logged (one line per probe at INFO, more verbose on failure).
- **Adding a service**: edit ConfigMap, `oc rollout restart deployment/service-watch`. Could add hot-reload (watch ConfigMap mtime) in v2 if reload-needs-restart becomes annoying.
- **Testing locally**: `docker-compose.yml` runs the service against a fake "sometimes-fails" HTTP server + mock Webex endpoint (just logs the message). No need for real Webex or real services during dev.
- **First-deploy validation**: deploy, intentionally point one service config at a known-broken URL, confirm alert arrives.

## What we build here (`ai` account)

- The Python service (generic, no environment-specific values in code)
- `Dockerfile` and `docker-compose.yml` for local testing
- Kubernetes/OpenShift manifest templates with placeholders (e.g., `${WEBEX_SPACE_ID}`, `${IMAGE_REGISTRY}`)
- Unit tests (probe success/failure, state transitions, alert formatting)
- README explaining how to configure for any environment

## What gets configured at work (`work` account/machine)

- Real Webex bot token, space ID, internal service URLs
- Image registry credentials
- OpenShift kubeconfig context
- GitLab repo for the work-environment version of this code

No work credentials, URLs, or tokens ever touch this `ai` workstation.

## Decisions (resolved 2026-05-13)

- **Probe frequency**: 60s.
- **"Still down" repeat**: configurable via `repeat_interval_seconds`, default 0 (off). Standard production tools (Alertmanager etc.) typically default to 1-4h repeats; for a small-scale single-watcher setup, first-alert + recovery is usually enough. Flip it on if a missed outage ever bites.
- **Multi-replica**: no. One replica avoids the "two pods both alert" problem. Leader election if HA ever needed (v3+).
- **Alert format**: markdown. Webex adaptive cards (richer UI) deferred — not worth complexity for v1.
- **Runbook links**: per-service optional `runbook_url`. When present, alert prominently links to it. When absent, embedded fallback triage steps shown.

## Decision log

- 2026-05-13: external HTTPS probe (not `ipa-healthcheck` agent) chosen for v1 simplicity. Alert payload references `ipa-healthcheck` as triage step instead.
- 2026-05-13: Python over Go for v1 — iteration speed over production polish.
- 2026-05-13: In-memory state over external store — pod restarts are rare; re-alert on restart is acceptable behavior.
- 2026-05-13: Repeat-alert interval added as opt-in config (`repeat_interval_seconds`, default 0). Standard production pattern; default off for small-scale single-watcher setup.
- 2026-05-13: Per-service `runbook_url` field added. When present, alert prominently links to runbook and omits the generic fallback triage. When absent, embedded triage is shown.
- 2026-05-13: Alert payload enriched with last-3-attempts ring buffer and response body snippet (first 200 chars) for actionable diagnostic context.
