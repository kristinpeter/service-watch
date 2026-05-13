# service-watch — Operations Handbook

End-to-end manual for building, deploying, operating, and configuring
service-watch on OpenShift. Read this top-to-bottom on first deploy;
use the table of contents to jump to specific tasks later.

## Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [One-time setup](#3-one-time-setup)
4. [Build the image (Shipwright)](#4-build-the-image-shipwright)
5. [Deploy to OpenShift](#5-deploy-to-openshift)
6. [Validate end-to-end](#6-validate-end-to-end)
7. [Day-2: operate](#7-day-2-operate)
8. [Day-2: configure (add/change services)](#8-day-2-configure-addchange-services)
9. [Day-2: update to a new version](#9-day-2-update-to-a-new-version)
10. [Troubleshooting](#10-troubleshooting)
11. [Decommission](#11-decommission)

---

## 1. Overview

service-watch is a single-replica OpenShift Deployment that probes a configurable list of HTTPS endpoints every 60 seconds and posts actionable alerts to a Webex space when state changes (UP → DOWN or DOWN → UP).

### Architecture at a glance

```
┌─────────────────────────────────────────────────────────────┐
│  Namespace: infra-watch                                     │
│                                                             │
│   Shipwright Build (template)                               │
│     └── BuildRun (manual trigger) ─► amd64 build pod        │
│                                       │                     │
│                                       ▼ push                │
│                                  Harbor registry            │
│                                       │                     │
│                                       ▼ pull (imagePullSecret)│
│   Deployment (1 replica, amd64 nodeSelector)                │
│     ├── ConfigMap (service list)                            │
│     └── Secret: WEBEX_BOT_TOKEN + WEBEX_SPACE_ID            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
          │                          │
          ▼ HTTPS probes             ▼ HTTPS POST
   Monitored Linux VMs        webexapis.com
   (FreeIPA, etc.)
```

### Key design points (for context — see [ARCHITECTURE.md](ARCHITECTURE.md) for the long version)

- **Single replica**, no leader election. Pod restart re-evaluates from scratch — by design.
- **In-memory state** — no PVC, no database.
- **ConfigMap-driven service list**: add a service by editing YAML.
- **amd64-only**: build pod and runtime pod both pin to `kubernetes.io/arch: amd64`.
- **Restricted SCC compatible**: non-root, no privesc, drops all caps, read-only root filesystem.

---

## 2. Prerequisites

### On your workstation

- `oc` CLI (matching cluster version) — `oc version --client`
- `git` (to push the code to GitLab)
- Optionally `shp` CLI (Shipwright client) for nicer build commands — `kubectl krew install shp` or download from https://github.com/shipwright-io/cli/releases

### In the OpenShift cluster

- **Shipwright Operator installed** — verify with:
  ```bash
  oc get csv -n openshift-operators | grep -i shipwright
  ```
  If empty: ask cluster admin to install the "Shipwright Operator" from OperatorHub. Cluster-scoped operator; one-time.

- **At least one amd64 worker node** — verify with:
  ```bash
  oc get nodes -l kubernetes.io/arch=amd64 -o name
  ```

- **Cluster network egress** must permit:
  - Build pod → GitLab repo URL (HTTPS or SSH)
  - Build pod → Harbor (HTTPS push)
  - Runtime pod → Harbor (HTTPS pull)
  - Runtime pod → monitored service URLs
  - Runtime pod → `webexapis.com` (HTTPS, port 443)

### External

- **Webex bot** — created at https://developer.webex.com/my-apps. Note the bot's access token and the `roomId` of the target Webex space (see §3.2).
- **Harbor robot account** with push+pull rights on the target project. Get the credentials from your Harbor admin.

---

## 3. One-time setup

### 3.1 Push the code to GitLab

```bash
cd ~/projects/service-watch
git init && git add -A && git commit -m "Initial commit"
git remote add origin https://gitlab.example.com/infra/service-watch.git
git push -u origin main
```

(If you already have a GitLab project, skip.)

### 3.2 Create the Webex bot and capture IDs

1. Visit https://developer.webex.com/my-apps and click **Create a Bot**.
2. Set display name, username, icon. Save.
3. Copy the bot's **access token** — store securely; this is `WEBEX_BOT_TOKEN`.
4. In the Webex Teams desktop/web app:
   - Create a space (e.g. "Infra Watch").
   - Invite the bot (mention `@<bot-username>` or use the Add People button).
   - Invite yourself + any other on-call humans.
5. Get the `roomId` of the new space:
   ```bash
   curl -s -H "Authorization: Bearer <WEBEX_BOT_TOKEN>" \
     https://webexapis.com/v1/rooms \
     | python -m json.tool | grep -B1 "Infra Watch"
   ```
   The `id` field is `WEBEX_SPACE_ID`.

### 3.3 Create the OpenShift namespace and secrets

```bash
oc login <cluster-url>
oc new-project infra-watch
```

**Webex secret:**
```bash
oc create secret generic service-watch-webex \
  --namespace infra-watch \
  --from-literal=WEBEX_BOT_TOKEN='<paste bot token>' \
  --from-literal=WEBEX_SPACE_ID='<paste space id>'
```

**Harbor credentials secret** (used both by the Shipwright build to push, and by the Deployment to pull):
```bash
oc create secret docker-registry harbor-credentials \
  --namespace infra-watch \
  --docker-server='harbor.example.com' \
  --docker-username='robot$infra-watch+sw' \
  --docker-password='<robot password>' \
  --docker-email='robot@example.com'
```

Replace the four `'...'` values with real ones from your Harbor admin. The username must match the Harbor robot account exactly (Harbor uses `robot$<project>+<name>` format).

### 3.4 (Optional) Git credentials secret

If the GitLab repo is private and needs auth to clone:

```bash
oc create secret generic service-watch-git-credentials \
  --namespace infra-watch \
  --type=kubernetes.io/basic-auth \
  --from-literal=username='<gitlab user or token name>' \
  --from-literal=password='<gitlab personal access token>'

oc annotate secret service-watch-git-credentials \
  --namespace infra-watch \
  tekton.dev/git-0='https://gitlab.example.com'
```

Then in `deploy/openshift/build.yaml`, uncomment the `cloneSecret: service-watch-git-credentials` line.

---

## 4. Build the image (Shipwright)

### 4.1 Edit `deploy/openshift/build.yaml`

Replace placeholders:
- `REPLACE_WITH_GITLAB_REPO_URL` → your GitLab repo HTTPS URL (e.g. `https://gitlab.example.com/infra/service-watch.git`)
- `REPLACE_WITH_HARBOR_HOST` → e.g. `harbor.example.com`
- `REPLACE_WITH_HARBOR_PROJECT` → e.g. `infra-watch`

So `output.image` becomes something like:
```
harbor.example.com/infra-watch/service-watch:latest
```

### 4.2 Apply the Build resource

```bash
oc apply -f deploy/openshift/build.yaml
```

This creates the template. **It does not run a build yet.**

### 4.3 Trigger a build

Each BuildRun is a one-shot. To start one:

```bash
oc create -f deploy/openshift/buildrun.yaml
```

The BuildRun's name is generated (e.g. `service-watch-xyz12`). To track:

```bash
# List BuildRuns:
oc get buildruns -n infra-watch

# Watch progress live:
oc get buildruns -n infra-watch -w

# Tail logs of the most recent BuildRun:
oc logs -f -n infra-watch -l buildrun=<the-buildrun-name> --all-containers
# Or with the shp CLI:
shp build run service-watch --follow -n infra-watch
```

A successful build ends with `Status: True / Reason: Succeeded`. Expect 2–5 min depending on cluster cache.

If it fails, see §10 Troubleshooting.

### 4.4 Verify the image landed in Harbor

In the Harbor UI, navigate to your project → repositories → `service-watch`. You should see a `latest` tag with today's date.

For a versioned tag, edit `build.yaml`'s `output.image` to add a tag like `:0.1.0` and re-apply + re-run.

---

## 5. Deploy to OpenShift

### 5.1 Edit `deploy/openshift/configmap.yaml`

Replace placeholders with real internal URLs:
```yaml
- name: freeipa-web
  url: https://ipa.internal.example.com/        # ← real URL
  expect_text_contains: "Identity Management"
  timeout_seconds: 15
  runbook_url: https://wiki.internal/runbooks/freeipa-web  # ← real runbook (optional)
```

### 5.2 Edit `deploy/openshift/deployment.yaml`

Replace `REPLACE_WITH_HARBOR_HOST/REPLACE_WITH_HARBOR_PROJECT/service-watch:latest` with the same Harbor URL you used in `build.yaml`.

### 5.3 Apply

```bash
oc apply -f deploy/openshift/configmap.yaml
oc apply -f deploy/openshift/deployment.yaml
```

### 5.4 Verify the pod starts

```bash
oc get pods -n infra-watch -l app=service-watch -w
```

Expect status `Running` within ~30 seconds. If it's `ImagePullBackOff` or `CrashLoopBackOff`, see §10.

Then tail the logs:
```bash
oc logs -f deployment/service-watch -n infra-watch
```

You should see:
```
service-watch starting; <N> services
INFO httpx: HTTP Request: GET https://... "HTTP/1.1 200 OK"
```

If the first probe returns non-2xx, you'll get your first DOWN alert in Webex within ~2 probes.

---

## 6. Validate end-to-end

Before trusting the system, prove the alert path works.

### Method A: point at a known-broken URL temporarily

1. Edit the ConfigMap:
   ```bash
   oc edit configmap service-watch-config -n infra-watch
   ```
2. Change one service's URL to a nonsense URL like `https://this-host-does-not-exist.example.com/`.
3. Roll out:
   ```bash
   oc rollout restart deployment/service-watch -n infra-watch
   ```
4. Wait ~3 minutes (one probe + threshold + alert post).
5. **Confirm a DOWN alert arrives in your Webex space.**
6. Edit ConfigMap, restore real URL, restart. Confirm recovery alert.

### Method B: rely on real services

Skip this and wait for the next real outage. Not recommended — you want to know the alert path works *before* you need it.

---

## 7. Day-2: operate

### Tail logs

```bash
oc logs -f deployment/service-watch -n infra-watch
```

Each probe logs an httpx request line. State transitions log explicitly.

### Check pod health

```bash
oc get pods -n infra-watch
oc describe pod -l app=service-watch -n infra-watch
```

### Restart (e.g. after ConfigMap change)

```bash
oc rollout restart deployment/service-watch -n infra-watch
```

(In-memory state is lost; if anything is currently DOWN, you'll get a re-alert after the next probe threshold.)

### Pause monitoring entirely

```bash
oc scale deployment/service-watch -n infra-watch --replicas=0
```

Resume:
```bash
oc scale deployment/service-watch -n infra-watch --replicas=1
```

---

## 8. Day-2: configure (add/change services)

### Add a service

```bash
oc edit configmap service-watch-config -n infra-watch
```

Add an entry under `services:`:
```yaml
- name: gitlab
  url: https://gitlab.internal/users/sign_in
  expect_text_contains: "Sign in"
  timeout_seconds: 10
  runbook_url: https://wiki.internal/runbooks/gitlab
```

Save + exit. Then:
```bash
oc rollout restart deployment/service-watch -n infra-watch
```

### Remove a service

Same `oc edit`, delete the entry, rollout restart.

### Change global settings

Same ConfigMap, fields at the top:
- `check_interval_seconds`: probe cadence (default 60)
- `default_timeout_seconds`: per-probe timeout (default 10)
- `fail_threshold`: consecutive failures before DOWN (default 2)
- `repeat_interval_seconds`: 0 = off (default), or e.g. 3600 for 1h "still down" reminders

### Enable "still down" repeat reminders

Set `repeat_interval_seconds: 3600` in the ConfigMap, rollout restart. After that, any DOWN service re-alerts every hour until it recovers.

### Rotate the Webex token

```bash
oc delete secret service-watch-webex -n infra-watch
oc create secret generic service-watch-webex \
  --namespace infra-watch \
  --from-literal=WEBEX_BOT_TOKEN='<new token>' \
  --from-literal=WEBEX_SPACE_ID='<same or new space id>'
oc rollout restart deployment/service-watch -n infra-watch
```

---

## 9. Day-2: update to a new version

When you push new code to GitLab:

```bash
# 1. Trigger a new build
oc create -f deploy/openshift/buildrun.yaml

# 2. Wait for it to succeed
oc get buildruns -n infra-watch -w

# 3. If you use a versioned tag (recommended for production):
#    a. Edit build.yaml output.image to bump tag (e.g. :0.2.0)
#    b. Edit deployment.yaml image to the same tag
#    c. oc apply -f both
# If using :latest:
oc rollout restart deployment/service-watch -n infra-watch
# (forces image pull since imagePullPolicy: Always)
```

**Strong recommendation**: use versioned tags (`:0.1.0`, `:0.2.0`, …). `latest` is OK for v0 but makes rollback painful.

### Rolling back

```bash
oc set image deployment/service-watch \
  -n infra-watch \
  service-watch=harbor.example.com/infra-watch/service-watch:0.1.0
```

---

## 10. Troubleshooting

### `ImagePullBackOff`

Pod can't pull from Harbor. Causes (in likelihood order):
1. **`harbor-credentials` secret missing or wrong.** Verify:
   ```bash
   oc get secret harbor-credentials -n infra-watch -o yaml
   oc get pod -l app=service-watch -n infra-watch -o yaml | grep -A2 imagePullSecrets
   ```
2. **Wrong image URL** in `deployment.yaml`. Compare with what's actually in Harbor.
3. **Robot account lacks pull rights** on the target Harbor project. Ask Harbor admin.
4. **Network**: cluster can't reach Harbor. Test from a debug pod:
   ```bash
   oc run debug --rm -it --image=curlimages/curl --restart=Never -n infra-watch -- curl -kv https://harbor.example.com/v2/
   ```

### Build fails: `unable to push image: unauthorized`

Same as above but for push. The `harbor-credentials` secret needs **push** rights on the project, not just pull. Get a robot account with both.

### Build fails: `unable to clone git repo: authentication required`

GitLab repo is private and you haven't set up `service-watch-git-credentials`. See §3.4.

### Build pod stuck `Pending`

Probably no amd64 node available. Check:
```bash
oc get nodes -l kubernetes.io/arch=amd64
oc describe buildrun <name> -n infra-watch | tail -30
```
If there are no amd64 nodes, you have a cluster-architecture problem the cluster admin needs to resolve.

### Pod runs but no probes happen

```bash
oc logs deployment/service-watch -n infra-watch | tail -20
```
- If you see `config file not found` → ConfigMap not mounted; check `volumeMounts` + `volumes` in deployment.yaml.
- If you see `WEBEX_BOT_TOKEN and WEBEX_SPACE_ID must be set` → the secret is missing or wrongly named.

### Probes happen but no alerts arrive in Webex

1. Check service-watch logs for `HTTP Request: POST https://webexapis.com/v1/messages "HTTP/1.1 ..."`. If the response is 4xx → bad bot token or bot not in the space.
2. Confirm the bot is a member of the target space (re-add via `@<bot-username>`).
3. Confirm `WEBEX_SPACE_ID` matches the space's `id` field (not its name).

### CrashLoopBackOff on startup

```bash
oc logs deployment/service-watch -n infra-watch --previous
```
- `ValidationError: ...` → bad YAML in ConfigMap. Validate locally:
  ```bash
  python -c "import yaml; yaml.safe_load(open('config.yaml'))"
  ```
- `ConnectError`/permission/SCC → an OpenShift SCC issue. Check pod's securityContext matches the namespace's allowed SCC range.

### "I want to see what alert it WOULD have sent without sending"

Not yet a feature. Workaround: run the test suite locally, the `test_notifier.py` tests render every alert kind and you can read the rendered markdown in test output.

---

## 11. Decommission

If you ever want to remove service-watch entirely:

```bash
oc delete -f deploy/openshift/deployment.yaml
oc delete -f deploy/openshift/configmap.yaml
oc delete secret service-watch-webex -n infra-watch
oc delete secret harbor-credentials -n infra-watch
oc delete -f deploy/openshift/build.yaml
# Build runs auto-cleanup once their TTL expires; or delete explicitly:
oc delete buildruns -n infra-watch -l build.shipwright.io/name=service-watch
# Optional: delete the namespace if nothing else lives there
oc delete project infra-watch
```

In Harbor: delete the `service-watch` repository under the project, then delete the robot account if no longer needed.

In Webex: delete the bot at https://developer.webex.com/my-apps (or just leave it — bots are cheap).
