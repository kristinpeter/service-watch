# service-watch

[![CI](https://github.com/kristinpeter/service-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/kristinpeter/service-watch/actions/workflows/ci.yml)

HTTPS health-check monitor that posts actionable alerts to a Webex space.
Runs on OpenShift as a single Deployment; monitors external services
(typically Linux VMs running things like FreeIPA, GitLab, etc.) via HTTPS
probes.

## Features

- HTTPS probes with TLS validation, configurable timeout, and optional response-body substring checks.
- Per-service config in a ConfigMap; add a service by editing YAML + rollout restart.
- Webex alerts with markdown formatting: error, body snippet, last 3 attempts, runbook link (if configured) or generic triage steps.
- Dedupe (one alert per state transition); opt-in periodic re-alert while DOWN.
- Recovery notification on DOWN → UP.
- amd64-only (build and runtime, both pinned via `kubernetes.io/arch: amd64`).
- OpenShift restricted-v2 SCC compatible (non-root, no privesc, drops all caps, read-only rootfs).

## Documentation

- **[docs/HANDBOOK.md](docs/HANDBOOK.md)** — full operations manual: prerequisites, one-time setup, build (Shipwright + Harbor), deploy, operate, configure, update, troubleshoot, decommission. **Read this when deploying.**
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — design rationale, scope decisions, and decision log.

## Quick orientation

```
service-watch/
├── docs/
│   ├── HANDBOOK.md          ← canonical deployment + ops guide
│   └── ARCHITECTURE.md      ← design rationale + decisions
├── src/service_watch/       ← 8 Python modules (~400 LOC)
├── tests/                   ← 17 pytest tests
├── deploy/openshift/        ← Shipwright Build, BuildRun, Deployment, ConfigMap, Secret template
├── deploy/local/            ← test config for local dev
├── Dockerfile               ← amd64-targeted, non-root, OpenShift SCC compatible
├── docker-compose.yml       ← local E2E test rig with mocks
├── pyproject.toml
└── config.example.yaml
```

## Local development

Python 3.11+ and [uv](https://github.com/astral-sh/uv):

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

PYTHONPATH=src pytest -v             # run tests
ruff check src/ tests/               # lint
```

## Local end-to-end test

Spins up a mock target + mock Webex receiver + service-watch on this Mac.
Lets you simulate an outage and verify the alert flow without touching real
infra. See `docker-compose.yml` for the containerized version; plain
Python alternative documented in HANDBOOK.

## Deploying to OpenShift

See **[docs/HANDBOOK.md](docs/HANDBOOK.md)** for the full step-by-step.

High-level summary (this is a *summary*; do not deploy from this list — read the handbook):

1. Push code to GitLab.
2. Create Webex bot + space; capture token and space ID.
3. `oc new-project infra-watch`
4. Create secrets: `service-watch-webex` (Webex creds) and `harbor-credentials` (Harbor robot creds).
5. Edit Harbor + GitLab placeholders in `deploy/openshift/build.yaml` and `deployment.yaml`.
6. `oc apply -f deploy/openshift/build.yaml`
7. `oc create -f deploy/openshift/buildrun.yaml` — wait for build to succeed.
8. `oc apply -f deploy/openshift/configmap.yaml`
9. `oc apply -f deploy/openshift/deployment.yaml`
10. Validate by pointing one service URL at a known-broken endpoint and confirming a DOWN alert arrives in Webex.

## License

MIT — see [LICENSE](LICENSE).
