# Realtalk — Build Spec: Web Embed

> A separate delivery channel, not a successor to the v1.x / v2.x layer specs.
> Goal: let visitors play Realtalk inside the conle.ai marketing site without
> installing anything, while preserving the authentic TUI experience.
>
> **Approach**: run the existing `realtalk` CLI inside a pseudo-terminal on a
> GCP Cloud Run container, stream PTY I/O over a WebSocket, render it in
> xterm.js inside a React component on conle.ai. Zero changes to Realtalk's
> core layers.
>
> **Status** (post eng-review 2026-04-16): server runtime is Python
> (single-runtime container, aligned with rest of stack). Realtalk v2.0
> gameplay confirmed playable. Ops hardening (global spend cap, kill
> switch, recording) deferred to Phase 4.
>
> ## Requirements (authoritative)
>
> **Functional**
> - The TUI must be accessible from a Chrome browser visiting a Vite/React
>   page. The browser is the client; the Cloud Run process is the server.
> - Two-way communication: keystrokes flow browser → server, terminal
>   output flows server → browser, in real time.
> - The user interacts with the game inside the page (no popouts, no
>   separate windows).
>
> **Non-functional**
> - Minimal, simple implementation. No feature beyond what's required to
>   satisfy the functional contract ships in v1.
> - The client/server interface is first-class: every message type and
>   HTTP endpoint has a schema, a documented meaning, and a test.
> - Types are shared: one source of truth (Python/Pydantic) generates the
>   TypeScript types the React client consumes.

---

## Why this shape

- **No core rewrite.** Realtalk's layered architecture (Layer 0–6) stays
  untouched. The web embed is a new *frontend* — a PTY + WebSocket wrapper
  around the existing `realtalk` binary.
- **Cloud Run fits.** Supports long-lived WebSockets, scales to zero, has a
  generous free tier, and the user has GCP credits.
- **VibeTunnel as reference, not dependency.** We borrow its PTY-forwarding
  pattern and asciinema session format, but not its Lit/ghostty-web frontend
  (won't embed cleanly in Vite/React).

---

## Interface Contract (the source of truth)

This is the full surface between client and server. Anything not on this
list is not part of v1.

### HTTP endpoints

All HTTP responses are `application/json`. All errors use the shape
`{"error": "<code>", "message": "<human readable>"}`.

#### `POST /session`

Mint a short-lived token for opening a WebSocket. No auth beyond origin
check in v1.

Request body:
```json
{}
```

Response 200:
```json
{
  "session_id": "s_01HZ...",
  "token": "eyJ...",
  "ws_url": "wss://realtalk.conle.ai/ws?token=eyJ...",
  "expires_in_s": 300
}
```

Error responses:
- `403 forbidden_origin` — Origin header not in allowlist
- `429 rate_limited` — too many sessions from this IP (5/hour, 1 concurrent)
- `503 at_capacity` — all server instances busy; retry later

#### `GET /healthz`

Liveness probe. Returns `200 {"status": "ok"}` or `503`.

### WebSocket: `/ws?token=<token>`

Subprotocol: plain (no subprotocol header). All frames are **text** frames
containing a single JSON object. Binary framing is out of scope for v1.

**Client → Server messages:**

| `type` | Fields | Meaning |
|--------|--------|---------|
| `input` | `data: string` | Raw keystrokes (UTF-8). Sent on every xterm.js `onData` event. |
| `resize` | `cols: int`, `rows: int` | Terminal dimensions. Sent on mount and on every `ResizeObserver` fire. `cols` 20–300, `rows` 10–100. |

**Server → Client messages:**

| `type` | Fields | Meaning |
|--------|--------|---------|
| `ready` | `cols: int`, `rows: int` | PTY has been spawned. Sent once, first. |
| `output` | `data: string` | Terminal bytes from the PTY (UTF-8 string, may contain ANSI escapes). |
| `exit` | `code: int` | The `realtalk` process exited with this code. Server will close the socket next. |
| `error` | `code: string`, `message: string` | A server-side error. See codes below. |

**Error codes sent in `error` frames:**

| Code | Meaning |
|------|---------|
| `invalid_token` | Token missing, expired, or tampered |
| `invalid_frame` | Malformed JSON or unknown `type` |
| `session_timeout` | 30-min hard cap or 5-min idle cap hit |
| `pty_spawn_failed` | Server couldn't start the game process |
| `internal` | Anything else — server logs have details |

**Close codes** (WebSocket close frame):
- `1000` normal (game exited)
- `1008` policy violation (invalid token, bad origin)
- `1011` internal error
- `4000` session timeout

### Type definitions (shared source of truth)

All wire types are defined once as Pydantic models in
`realtalk/web/server/realtalk_web/protocol.py`:

```python
class InputFrame(BaseModel):
    type: Literal["input"]
    data: str

class ResizeFrame(BaseModel):
    type: Literal["resize"]
    cols: int = Field(ge=20, le=300)
    rows: int = Field(ge=10, le=100)

ClientFrame = Annotated[
    Union[InputFrame, ResizeFrame],
    Field(discriminator="type"),
]

class ReadyFrame(BaseModel):
    type: Literal["ready"]
    cols: int
    rows: int

class OutputFrame(BaseModel):
    type: Literal["output"]
    data: str

class ExitFrame(BaseModel):
    type: Literal["exit"]
    code: int

class ErrorFrame(BaseModel):
    type: Literal["error"]
    code: str
    message: str

ServerFrame = Annotated[
    Union[ReadyFrame, OutputFrame, ExitFrame, ErrorFrame],
    Field(discriminator="type"),
]
```

TypeScript types for the client are **generated** from these Pydantic
models via `datamodel-code-generator` (or `pydantic2ts`) at build time
and written to `conle/src/lib/realtalk-protocol.ts`. This is the one
artifact that keeps both sides in sync. A make target enforces it:

```bash
make protocol       # regenerates TS from Python, fails CI if diff
```

Adding a message type means: edit `protocol.py`, run `make protocol`,
commit both. There is no other way to change the wire format.

### Contract tests

`tests/test_protocol.py` (server) asserts:
- Every example in this spec round-trips through Pydantic parsing
- Invalid frames (bad type, out-of-range resize) are rejected
- The generated TypeScript file in `conle/src/lib/realtalk-protocol.ts`
  is up to date (CI fails if it drifts from `protocol.py`)

`conle/src/lib/__tests__/realtalk-protocol.test.ts` asserts:
- The generated types compile
- A mock WebSocket exchange using these types works end-to-end

---

## Architecture

```
┌──────────────────────────────┐   ┌────────────────────────────────┐
│ conle.ai (Vercel)            │   │ realtalk-api  (Cloud Run)      │
│  React + Vite                │   │  FastAPI, stateless            │
│                              │──►│  POST /session, GET /healthz   │
│  <RealtalkTerminal />        │   │  concurrency=80, min=1, max=3  │
│    xterm.js                  │   │  reads capacity counter,       │
│    WebSocket client          │   │  returns 503 at_capacity cleanly│
│    (uses generated TS types) │   └────────────────────────────────┘
│                              │                 │ reads/writes
│                              │                 ▼ (Firestore counter)
│                              │   ┌────────────────────────────────┐
│                              │   │ realtalk-ws   (Cloud Run)      │
│                              │──►│  FastAPI + ptyprocess          │
│                              │wss│  GET /ws (streams PTY)         │
│                              │   │  concurrency=1, min=1, max=10  │
│                              │   │  spawns `realtalk` in PTY;     │
│                              │   │  increments/decrements counter │
└──────────────────────────────┘   └────────────────────────────────┘
              ▲                                  ▲
              │                                  │
              └──── protocol.py → generated .ts ─┘
                    (single source of truth for all wire types;
                     Secret Manager: LLM keys mounted on realtalk-ws)
```

**Why two services**: with a single service at `concurrency=1`, `POST
/session` and `/ws` share the same per-instance budget. When all 10 slots
are holding PTYs, the mint endpoint is also blocked — visitors get a
generic Cloud Run timeout instead of the `503 at_capacity` the spec
promises. Splitting puts the mint path on a cheap, high-concurrency
service that stays reachable under load, so the frontend can always tell
the user "game full."

**Why Python, not Node, for the PTY server**: keeps the stack single-runtime
(one Dockerfile, one dep manager, one ecosystem). `ptyprocess` is mature
and the PTY glue is ~100 lines regardless of language. The server is thin
glue — no game logic. Trade accepted: fewer copy-paste examples than Node's
`node-pty`, but the code is small enough that it doesn't matter.

---

## Repo layout

Two repos, one new directory each.

### `realtalk/` — new `web/` directory
```
realtalk/
  web/
    server/
      pyproject.toml
      realtalk_web/
        __init__.py
        api_main.py        # realtalk-api service: /session, /healthz
        ws_main.py         # realtalk-ws service: /ws upgrade
        session.py         # PTY lifecycle, asciinema streaming
        auth.py            # origin check, rate limit, short-lived session token
        capacity.py        # Firestore-backed concurrent-session counter
        budget.py          # global daily LLM spend cap + kill switch (Phase 4)
        recording.py       # asciinema v2 frame writer, GCS uploader (Phase 4)
      tests/
        test_session.py
        test_auth.py
        test_capacity.py
        test_budget.py
      Dockerfile.api       # lightweight image for realtalk-api
      Dockerfile.ws        # image for realtalk-ws (installs `realtalk` via uv)
      .dockerignore
    infra/                 # Terraform (HCP Terraform backend)
      main.tf
      variables.tf
      outputs.tf
      versions.tf
      artifact_registry.tf
      cloud_run.tf
      firestore.tf
      gcs.tf
      iam.tf
      secrets.tf
      domain.tf            # optional: Cloud Run domain mapping
      terraform.tfvars.example
    scripts/
      deploy.sh            # one-command build + push + apply
    Makefile               # wraps deploy.sh, tests, local-run
    README.md              # deploy instructions
```

### `conle/` — new component + page
```
conle/
  src/
    components/
      RealtalkTerminal.tsx # xterm.js wrapper
    pages/
      play.tsx             # /play route, hosts the terminal
```

---

## What this spec builds

### Phase 1 — Backend: PTY WebSocket server (realtalk/web/server)

Minimal set for v1. Each file has a single, stated responsibility.

| File | Responsibility | Est. LOC |
|------|----------------|----------|
| `protocol.py` | Pydantic models for every wire message (source of truth) | 60 |
| `api_main.py` | **realtalk-api** FastAPI app: `POST /session`, `GET /healthz`; reads capacity counter, mints tokens | 80 |
| `ws_main.py` | **realtalk-ws** FastAPI app: `/ws` upgrade; increments/decrements capacity counter around each session | 60 |
| `session.py` | `Session` class: spawn PTY, pipe I/O, idle + hard timeouts | 120 |
| `auth.py` | Origin allowlist, signed token (HMAC), per-IP rate limit | 70 |
| `capacity.py` | Firestore-backed concurrent-session counter (increment, decrement, read) | 50 |
| `Dockerfile.api` / `Dockerfile.ws` | Python 3.12 images. `Dockerfile.ws` also installs `realtalk` via `uv` | 25 |

**Not in Phase 1** (see Phase 4):
- Recording to asciinema/GCS
- Global LLM spend cap + kill switch
- Firestore, GCS buckets
- Admin tooling

**Protocol** (WebSocket, JSON frames):

```python
# client → server
{"type": "input", "data": str}                 # keystrokes
{"type": "resize", "cols": int, "rows": int}

# server → client
{"type": "output", "data": str}                # terminal bytes (UTF-8)
{"type": "exit", "code": int}
{"type": "error", "message": str}
```

Binary framing is a future optimization; JSON keeps debugging trivial.

**Session lifecycle**:
1. Client opens `wss://realtalk-web-<hash>.run.app/ws?token=...`
2. Server validates token + origin, spawns `realtalk` in a PTY
3. Pipes PTY stdout → `output` frames; `input` frames → PTY stdin
4. On PTY exit, sends `exit`, closes socket
5. Hard cap: 30 min per session, 80 cols × 24 rows default, idle timeout 5 min

**Recording**: deferred to Phase 4. v1 does not persist session I/O
anywhere. If replay becomes desirable, the design is: stream asciinema v2
frames directly to a GCS object per session (not via `/tmp`, which is
tmpfs and would compete with the session's RAM budget).

### Phase 2 — Frontend: React component (conle/src/components)

| File | Responsibility | Est. LOC |
|------|----------------|----------|
| `RealtalkTerminal.tsx` | xterm.js mount, WebSocket wiring, resize observer | 150 |
| `pages/play.tsx` | Page layout, intro copy, the terminal | 60 |

**Dependencies to add**:
- `@xterm/xterm`
- `@xterm/addon-fit`
- `@xterm/addon-web-links`

**Behavior**:
- On mount: fetch a session token from Cloud Run, open WebSocket
- Pipe xterm.js `onData` → `input` frames
- Pipe `output` frames → `term.write()`
- On `ResizeObserver`: call fit addon, send `resize` frame
- On disconnect: show "Session ended — [play again]" overlay

**Styling**: match DESIGN.md — parchment bg (#f5f0e8), JetBrains Mono,
borderless. The terminal itself renders Realtalk's own colors, so the page
chrome stays minimal.

### Phase 3 — Deploy (Terraform + HCP)

All GCP infrastructure is codified in `realtalk/web/infra/`. State lives in
HCP Terraform (org: `conle`; project ID kept in local tfvars, not committed). No
ClickOps — every resource change goes through `terraform plan` + apply.

**HCP Terraform workspace**:
- Name: `realtalk-web` (under the `conle` HCP project — project ID kept in
  `terraform.tfvars`, not committed)
- Execution mode: **CLI-driven** (local runs, state in HCP). This lets us
  trigger applies from `scripts/deploy.sh` without wiring VCS integration
  on day one. Switch to VCS-driven later when we want PR-gated infra.
- Terraform version: pinned in `versions.tf` (1.9.x).

**GCP project**: **pre-existing**, not created by Terraform. The target
project is created once via `gcloud projects create` with billing linked.
Terraform manages only resources *inside* the project. This keeps the
required IAM scope narrow (project-level SA, no org-level keys) at the
cost of one manual step that happens once per environment. The specific
project ID lives in `terraform.tfvars` (git-ignored).

**Terraform backend** (`main.tf`):
```hcl
terraform {
  cloud {
    organization = "conle"
    workspaces { name = "realtalk-web" }
  }
}
```

**Required variables** — `terraform.tfvars` is git-ignored. Only the
template `terraform.tfvars.example` is committed, with placeholders:

```hcl
# terraform.tfvars.example (committed)
gcp_project_id      = "your-gcp-project-id"
gcp_region          = "us-central1"
service_name        = "realtalk-web"
image_tag           = "latest"                # overridden by deploy.sh
allowed_origins     = ["https://conle.ai"]
daily_spend_cap_usd = 20
custom_domain       = ""                      # e.g. "realtalk.conle.ai"; empty = skip
```

Real values (including the actual project ID) live only in
`terraform.tfvars` locally and as HCP workspace variables. No GCP org ID
is needed anywhere in Terraform — the project-scoped SA doesn't require it.

**Secrets** (managed outside Terraform state to avoid accidental logging):
- Create once in GCP Console or via `gcloud secrets create`:
  `anthropic-api-key`, `session-token-signing-key`
- Terraform references them by name in `secrets.tf` via
  `google_secret_manager_secret` data sources, then grants the Cloud Run
  service account `roles/secretmanager.secretAccessor`.

**Resources provisioned**:

| File | Resources |
|------|-----------|
| `artifact_registry.tf` | `google_artifact_registry_repository` (Docker) |
| `cloud_run.tf` | Two `google_cloud_run_v2_service` resources: `realtalk-api` (concurrency=80, min=1, max=3) and `realtalk-ws` (concurrency=1, min=1, max=10, HTTP/2 enabled for WebSockets). IAM invoker = `allUsers` on both. |
| `firestore.tf` | Firestore in Native mode with a `capacity` collection used by `capacity.py` as the shared concurrent-session counter (single doc, transactional increment/decrement). |
| `iam.tf` | Service account `realtalk-web-run@...`, roles: `secretmanager.secretAccessor`, `logging.logWriter` |
| `secrets.tf` | Data sources + IAM bindings (no secret values in state) |
| `domain.tf` | `google_cloud_run_domain_mapping` (conditional on `var.custom_domain`) |

Firestore is provisioned in v1 for the capacity counter (cheap, on free
tier). Phase 4 extends it with additional collections for the kill switch
and daily spend counter. `gcs.tf` (recording bucket) is Phase 4 only.

**APIs to enable** (managed by Terraform via `google_project_service`):
`run.googleapis.com`, `artifactregistry.googleapis.com`,
`secretmanager.googleapis.com`. (Cloud Build not needed — we build
locally and push the image directly.)

### CLI deploy command

A single command from `realtalk/web/`:

```bash
make deploy            # build → push → terraform apply
make deploy-plan       # terraform plan only (safe, no changes)
make deploy-destroy    # tear everything down (confirm prompt)
make logs              # tail Cloud Run logs
make kill-switch-on    # flip Firestore kill switch
make kill-switch-off   # restore service
```

`scripts/deploy.sh` does, in order:
1. Verify `gcloud auth` + `terraform login` (HCP) + on `main` branch
2. Build both images in parallel:
   - `docker buildx build -f Dockerfile.api --platform linux/amd64 -t $IMAGE_API .`
   - `docker buildx build -f Dockerfile.ws  --platform linux/amd64 -t $IMAGE_WS  .`
3. Push both to Artifact Registry (`realtalk-api:$SHA`, `realtalk-ws:$SHA`)
4. `terraform apply -var="image_tag=$SHA"` (HCP runs remotely but local CLI
   streams output) — updates both Cloud Run services to the same SHA
5. Health check: `curl $API_URL/healthz` until 200 or 60s timeout
6. Print both public URLs (api + ws) and the asciinema bucket

Image tag is the git short SHA — every deploy is traceable to a commit,
and rollback is `make deploy IMAGE_TAG=<prev_sha>`.

### CI/CD

GitHub Actions workflow `.github/workflows/web-deploy.yml`:
- Trigger: push to `main` touching `realtalk/web/**`
- Steps: lint → test → `make deploy` (using a GCP service account key
  stored in GitHub Actions secrets and an HCP token)
- Separate workflow `web-plan.yml` runs `terraform plan` on PRs and
  comments the plan on the PR.

### Bootstrap (one-time, before first `make deploy`)

Assumes the target GCP project already exists with billing linked. All
remaining steps are documented in `realtalk/web/README.md`:

1. **Create a project-scoped deploy service account**:
   ```bash
   PROJECT=<your-gcp-project-id>
   gcloud iam service-accounts create realtalk-deploy \
     --project=$PROJECT --display-name="Realtalk Terraform deployer"
   for role in \
     roles/run.admin \
     roles/artifactregistry.admin \
     roles/secretmanager.admin \
     roles/datastore.owner \
     roles/storage.admin \
     roles/iam.serviceAccountAdmin \
     roles/iam.serviceAccountUser \
     roles/serviceusage.serviceUsageAdmin; do
     gcloud projects add-iam-policy-binding $PROJECT \
       --member="serviceAccount:realtalk-deploy@$PROJECT.iam.gserviceaccount.com" \
       --role="$role" --condition=None
   done
   gcloud iam service-accounts keys create /tmp/realtalk-deploy.json \
     --iam-account=realtalk-deploy@$PROJECT.iam.gserviceaccount.com
   ```
2. **Create HCP Terraform workspace** `realtalk-web` under the `conle`
   HCP project, execution mode CLI-driven. Paste the SA key JSON from
   `/tmp/realtalk-deploy.json` into workspace var `GOOGLE_CREDENTIALS`
   (sensitive, env var). Delete the local copy: `shred -u /tmp/realtalk-deploy.json`.
3. **Copy tfvars**: `cp terraform.tfvars.example terraform.tfvars`, fill
   in the real project ID and any overrides.
4. **Plan**: `cd realtalk/web && make deploy-plan` — inspect what will be
   created. First run will enable ~5 APIs (slow).
5. **First apply**: `make deploy`. Builds image, pushes to Artifact
   Registry, applies Terraform.
6. **Create secrets** (after first apply, so the project APIs are on):
   ```bash
   echo -n "$ANTHROPIC_API_KEY" | gcloud secrets create anthropic-api-key \
     --project=$PROJECT --data-file=-
   openssl rand -hex 32 | gcloud secrets create session-token-signing-key \
     --project=$PROJECT --data-file=-
   ```
7. **Redeploy**: `make deploy` — Cloud Run picks up the new secret refs.

First deploy takes ~3–5 min (API enablement is slow). Subsequent deploys
are ~60s (image push + Cloud Run revision rollout).

**Why secrets are created post-apply**: Terraform binds IAM to the secret
*names*, not values. Creating secrets via `gcloud` keeps plaintext out of
`terraform plan` output and out of HCP state forever.

---

### Cloud Run runtime config (applied by Terraform)

Two services, both in `us-central1`, both with secrets via Secret Manager
and ingress=all (origin-checked in `auth.py`):

**`realtalk-api`** (stateless token mint):
- Concurrency: 80 per instance
- Min: 1, Max: 3 (cheap JSON endpoint; a single instance handles hundreds of QPS)
- Memory: 256 MiB, CPU: 1
- Request timeout: 30s
- Role: validate origin + rate limit, read Firestore capacity counter, return
  `503 at_capacity` if the game pool is full, else mint signed token.

**`realtalk-ws`** (PTY host):
- Concurrency: **1** per instance (each session holds a PTY + Python process)
- Min: 1, Max: 10 (hard ceiling — defines the concurrent-game pool)
- Memory: 512 MiB, CPU: 1
- HTTP/2 enabled (required for Cloud Run WebSockets)
- Request timeout: 3600s (max)
- Role: validate token, increment Firestore capacity counter, spawn PTY,
  stream I/O, decrement counter on close.

**Capacity counter**: a single Firestore document (e.g.
`capacity/global`) with field `active_sessions`. `realtalk-ws` performs
transactional `+1` on upgrade, `-1` on close (including timeout / crash
cleanup via a finally block). `realtalk-api` reads the counter and
compares to `max_sessions=10` before minting. Firestore is chosen over
Memorystore (Redis) to stay on the free tier and avoid a VPC connector;
~1s read latency is fine for a capacity gate. A stale read can
over-admit by 1–2 sessions under burst, which is acceptable — the 11th
session upgrade will still succeed briefly but the next mint will
correctly refuse.

**Overflow UX**: when `active_sessions >= 10`, `POST /session` returns
`503 {"error": "at_capacity", "retry_after_s": 30}`. Because this lands
on `realtalk-api` (concurrency=80), it is always reachable — never a
timeout — so the frontend can reliably show "game full — try again in a
minute" instead of a broken socket.

**Capacity at ~300 concurrent visitors**: 10 play simultaneously, ~290
see `at_capacity`. Effective throughput is ~100–200 plays/hour at
`max=10` given the 30-min hard cap and typical mid-game churn. Burst of
300 page loads hits `realtalk-api` at ~300 QPS, which a single instance
absorbs easily; scale-out from 1→10 `realtalk-ws` instances takes ~20s
cold-start, during which overflow visitors simply see `at_capacity`
immediately (no queue, no wait indicator in v1).

**Anthropic rate limits**: 10 concurrent games × ~5 LLM turns/min ≈ 50
req/min. That's at the ceiling of Tier 1 Anthropic quota — confirm Tier
2+ before public launch or lower `max_sessions` accordingly.

**Kill switch & spend cap**: deferred to Phase 4. v1 relies on rate
limiting (5 sessions/hour/IP, 1 concurrent) and the hard instance cap
(max=10) to bound blast radius. If we see abuse during soft launch,
Phase 4 adds a Firestore-backed kill switch and daily spend cap.

Until Phase 4 ships, the operational kill switch is
`gcloud run services update realtalk-web --no-traffic` — instant,
reversible, one command.

**Custom domain**: with two services we need two hostnames (or a path-based
rewrite at the edge). Default: `api.realtalk.conle.ai` →  `realtalk-api`
and `ws.realtalk.conle.ai` → `realtalk-ws`, both via
`google_cloud_run_domain_mapping` in `domain.tf`. Alternative under
consideration: a single `realtalk.conle.ai` with Vercel rewriting
`/session` → api and `/ws` → ws; hides the split but adds a hop. DNS
records added in whatever DNS provider `conle.ai` uses — not managed by
this Terraform to avoid coupling.

---

## Security & abuse

- **Origin check**: WebSocket upgrade rejects non-`conle.ai` origins. Note
  that Origin is trivially spoofable by non-browser clients — this is a
  first layer, not a security boundary.
- **Token mint**: conle frontend hits `POST /session` (HTTP) which mints a
  short-lived signed token (5 min, HMAC over `{session_id, issued_at,
  nonce}`). WebSocket upgrade requires the token as a query param. The
  token is NOT bound to client IP — mobile NAT/carrier IP rotation would
  invalidate legitimate sessions mid-play.
- **Rate limit**: 1 concurrent session per IP, 5 sessions/hour. In-memory
  bucket is fine at this scale (one instance handles one session; the cap
  only matters at the `POST /session` endpoint, which always lands on an
  arbitrary instance — so use Firestore for the counter, not in-memory).
- **API key isolation**: LLM keys live in Cloud Run Secret Manager, never
  reach the browser.
- **Process sandbox**: `realtalk` runs as non-root in the container. No
  filesystem writes outside `/tmp`.
- **Global spend cap + kill switch**: see "Deploy" above. Hard daily cap on
  LLM cost; instant global disable via Firestore flag.

---

## Non-goals (v1)

- Persistent accounts / cross-device session resume
- Session replay UI on conle (recordings are written but not surfaced)
- Mobile on-screen keyboard polish
- Multiple concurrent games per user
- Fancy loading states beyond "connecting…"

---

## Acceptance criteria

- [ ] Visit `conle.ai/play`, see a terminal, play a full Realtalk session
      end-to-end in Chrome + Safari (desktop)
- [ ] Resize the browser window; the terminal reflows correctly
- [ ] Close the tab mid-session; server cleans up PTY + Python process
      within 10s (verified via Cloud Run logs)
- [ ] Origin check: WebSocket from `localhost` (non-dev) or other origin
      is rejected with 403
- [ ] First prompt renders ≤ 2s from page load (min-instances=1, no cold start)
- [ ] Overflow: 11th concurrent session receives `503 at_capacity`, not a
      broken socket
- [ ] Kill switch: toggling the Firestore flag blocks new sessions within
      60s; in-flight sessions finish normally
- [ ] Spend cap: simulated overage returns `503 budget_exhausted` and
      auto-clears at UTC midnight
- [ ] One session holds ≤ 300 MiB RSS
- [ ] `pytest` and `mypy` still pass in `realtalk/` (no core changes)
- [ ] Cloud Run monthly cost stays within free tier at 100 sessions/day

---

## Test plan

**Backend (`realtalk/web/server/tests/`)** — pytest, follows Realtalk's
existing test style.

`test_session.py`:
- PTY spawn + first output frame within 1s
- `input` frame → PTY stdin (send "hello", assert echo in `output` frame)
- `resize` frame → PTY SIGWINCH (verify via `stty size` inside PTY)
- WebSocket close → PTY process reaped within 10s (`ps` check)
- Idle timeout (5 min) closes session
- Hard timeout (30 min) closes session
- PTY exit → `exit` frame sent, then socket closed

`test_auth.py`:
- `POST /session` with allowed origin → 200 + token
- `POST /session` with disallowed origin → 403
- `POST /session` beyond rate limit → 429
- WebSocket upgrade with valid token → accept
- WebSocket upgrade with expired token (>5 min) → 401
- WebSocket upgrade with tampered token (bad HMAC) → 401

`test_protocol.py`:
- Every ClientFrame example round-trips through Pydantic
- Every ServerFrame example round-trips through Pydantic
- Invalid frames (wrong `type`, out-of-range `cols`/`rows`, missing fields)
  raise `ValidationError`
- The generated TypeScript file `conle/src/lib/realtalk-protocol.ts` is
  up to date with `protocol.py` (runs `make protocol --check`, asserts
  no diff)

**Frontend (`conle/src/components/`)** — vitest + @testing-library/react.
- `<RealtalkTerminal />` mounts xterm.js and opens WebSocket on render
- `output` frames call `term.write`
- `onData` from xterm.js sends `input` frames
- `ResizeObserver` triggers fit + `resize` frame
- Disconnect shows "Session ended" overlay with replay button

**E2E smoke (Playwright on conle)** — one test, runs against a locally
spawned Python server:
- Visit `/play`, wait for `$ ` prompt, type "hello", see response, close tab,
  verify server logs PTY cleanup within 10s

**Regression for Realtalk core**: none. Backend wraps the unchanged
`realtalk` binary; existing `pytest` suite still passes untouched.

**Interface contract enforcement**: `make protocol` must be idempotent
(running it twice produces no change). CI runs `make protocol --check`
and fails if the generated TS file is stale. This is the single guard
against client/server drift.

---

## Phased delivery

1. **Phase 1 (backend)** — get `wss://localhost:8080/ws` streaming a real
   Realtalk session to a CLI WebSocket client. No frontend yet.
2. **Phase 2 (frontend)** — build `<RealtalkTerminal />` against the local
   server. Iterate on resize + reconnect UX.
3. **Phase 3 (deploy)** — Terraform + HCP workspace, Dockerfile, `make
   deploy`, domain, origin lock.
4. **Phase 4 (polish)** — GCS recording streaming, basic analytics,
   kill-switch admin page, error overlays, mobile read-only mode.

Ship Phase 1–3 before touching Phase 4.

---

## NOT in scope (v1)

Deferred with rationale:
- **Persistent accounts / resume**: out. Web embed is a demo, not a product.
- **Session replay UI**: recordings stream to GCS but no viewer. Cheap to
  add later once the `.cast` pipeline is proven.
- **Binary WebSocket framing**: JSON is fine at 1 player per instance.
- **Per-session LLM spend cap**: only global cap in v1. Per-session cap
  needs proxying litellm calls, which doubles the blast radius of changes.
- **Admin dashboard for kill switch**: toggle via `gcloud firestore` CLI.
  A UI is a separate ~day of work.
- **Multi-region deploy**: single region (us-central1) is fine for a demo.
- **Mobile on-screen keyboard polish**: listed as non-goal.
- **iframe embed into third-party sites**: CORS + WebSocket origin locked
  to conle.ai.

## Open questions

- **Domain**: `realtalk.conle.ai` (subdomain) vs `conle.ai/api/rt` (Vercel
  rewrite proxy)? Subdomain is simpler; rewrite hides infra but adds a hop.
- **Recording retention**: keep `.cast` files forever for highlight reels,
  or 7-day TTL? Defer until Phase 4.
- **Anti-bot gate**: fully anonymous v1, or require a "start game" click
  that mints the token to deter drive-by scrapers? Leaning toward the click
  gate — it's one line of frontend code and cuts most bot traffic.
- **Daily spend cap value**: $20/day is a placeholder. Size it against
  expected traffic × per-session LLM cost once we measure a few real
  sessions locally.
