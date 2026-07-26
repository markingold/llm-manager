# LLM Manager Foundation Review

## Metadata

- Date: 2026-07-26
- Repository: `/srv/2bananas/projects/llm-manager`
- Branch and initial status: `main`, clean, tracking `github/main`
- Initial/deployed checkout revision: `426ccd6addb4b11d14d4adcd88e7b8090c81329d`
- Host runtime: Python 3.10.12, FastAPI 0.118.0, Pydantic 2.11.10, Uvicorn 0.37.0, systemd 249
- Inspected services: `llm-manager-api`, `llm-a`, `llm-b`, `llm-c`, `llm-embed`, and `llm-tgw-webui`
- Other inspected boundaries: active Apache virtual hosts and LLM Manager proxy declarations, SQLite schema/permissions, model aliases, listeners, backend probes, Git history, and representative consumer adapters
- Constraints: no consumer, Apache, standards, or global-environment changes; no paid provider calls; no model-content scan; environment values and credentials were not printed

This review distinguishes the initial observed state from the post-change verification
state. The latter is recorded in **Verification record**.

## Current architecture

### API

`llm_manager/server.py` is a FastAPI control plane on loopback port 8101. It owns
model inventory, transactional model lifecycle, engine control, provider governance,
routing, evaluations, conversions, persisted jobs, and the dashboard API. The
project-checkout unit calls `python -m llm_manager.server`.

### Router and provider adapters

The router exposes normalized chat, completion, and embedding endpoints. It resolves
task/project policy, a candidate lane chain, curated models, capability constraints,
rate/cost controls, and fallbacks. Provider adapters isolate local OpenAI-compatible
engines, OpenRouter, and OpenAI. Decision records retain normalized attempts,
provider/model selection, usage, and fallback metadata.

### Local engine lanes

The managed lanes are:

- `chat` / `llm-a` / port 8500
- `intent` / `llm-b` / port 8501
- `small` / `llm-c` / port 8502
- `embed` / `llm-embed` / port 8503

The first three slots are general text lanes with independent backend selection.
The embedding slot is a vLLM pooling lane. A separate TGW WebUI/API service uses
ports 7860/8510 for operator testing and, by explicit operator decision, remains
publicly bound without authentication.

### Backend registry

`llm_manager/backend_registry.py` is the authoritative backend matrix for TGW,
TabbyAPI, vLLM, and direct llama.cpp. It records supported model kinds/tasks,
readiness endpoints and shapes, served-model identities, runtime probes, and the
pinned Tabby revision. `run/engine_launcher.py` selects a persisted per-slot
backend and dispatches to a backend-specific launcher.

### Runtime persistence

`run/state/runtime.db` is a versioned SQLite database. It stores JSON runtime
sections plus managed-job identities. Numbered migrations are recorded in
`schema_migrations`; configuration documents have their own schema migration
path. The database was mode 0600 and the state directory mode 0700 at inspection.
High-churn provider state, queues, usage, evaluations, conversions, and jobs are
durable. Catalog data and engine/model inventory are rebuildable; model weights
remain outside application backups under `/srv/2bananas/engines`.

### systemd relationship

The API invokes only configured LLM Manager units. Engine units call the stable
launcher wrapper. Before this pass, the packaged instance template was tracked,
but installed legacy-named host units `llm-a/b/c` and `llm-tgw-webui` had no exact
checked-in source and used `Restart=always`. The embedding and project-checkout
API units did have repository counterparts.

### Consumers

Verified representative contracts include:

- Book Analysis: `/router/chat`, optional explicit strategy/model, three client retries
- RPG: `/router/health`, `/providers/policies/test`, `/router/chat`, request IDs,
  JSON schema, service tiers
- Life OS: `/router/chat`, local-first policy, graceful `None` on manager errors
- Creekwatch: `/router/health` and `/router/chat`, explicit request ID, 429/5xx
  classified as retryable
- Creative Studio: `/router/chat`, routing metadata, optional direct-provider
  failover when LLM Manager fails
- Code Indexer and Smart Assistant: a mixture of current router/evaluation
  integrations and documented direct local compatibility paths

These consumers rely on the existing response shape and HTTP statuses. New
degradation metadata must therefore be additive.

### Apache boundary

The API binds to `127.0.0.1:8101`. Apache exposes `/llm-manager-api/` in the lab
virtual host. Duplicate available conf fragments also describe the same proxy;
the active vhost is the externally visible authority. Apache was inspected but
not modified. Direct engine port 8500 also appears in inactive/development vhost
material, which should remain an explicit operator decision rather than an
accidental production contract.

## Verified deployed state

Initial state captured before any service action:

| Unit/lane | Purpose | Enabled / active | Effective command (abbreviated) | Backend | Alias | Bind/health | Restarts | Repository source | Current problem |
|---|---|---|---|---|---|---|---:|---|---|
| `llm-manager-api` | Control plane/router | enabled / active | checkout venv `-m llm_manager.server` | n/a | n/a | 127.0.0.1:8101, healthy | 0 | `deploy/systemd/llm-manager-api.project.service` | `/health` was liveness-like but always reported `ok=true`; no canonical `/ready` |
| `llm-a` / chat | Primary chat | enabled / active | TGW venv `run/engine_launcher.py ...8500...chat_active_model` | TGW at capture; Tabby during storm | valid, Qwen GGUF | 127.0.0.1:8500, healthy | 4,027 | none exact | retained restart storm; `Restart=always`; prior valid Tabby config invisible to launcher |
| `llm-b` / intent | Optional intent | disabled / inactive | launcher on 8501 | TGW | broken/missing target | no listener; intentionally unloaded | 0 | none exact | project compatibility alias is broken because the deliberately unloaded slot retained a stale model symlink |
| `llm-c` / small | Optional utility | disabled / inactive | launcher on 8502 | TGW | valid | no listener; operator-disabled | 0 | none exact | inactive status was not classified for API/router consumers |
| `llm-embed` / embed | Embeddings | enabled / active | project venv launcher on 8503 | vLLM | valid BGE checkpoint | 127.0.0.1:8503, healthy | 0 | `deploy/systemd/llm-embed.service` | unit lacked config-error restart suppression/start limit |
| `llm-tgw-webui` | Standalone testing | enabled / active | TGW launcher on 8510 | TGW | small alias | 0.0.0.0:7860 and :8510, healthy | 0 | none exact | public/no-auth exposure is accepted; unit used unbounded `Restart=always` |

No additional LLM Manager timer, socket, or path unit was installed. The API,
chat, embedding, and standalone TGW listeners were present. Intent and small
listeners were absent.

## Incident analysis

### Immediate cause

At revision `e3005b1`, `run/engine_launcher.py` began calling
`probe_backend("tabbyapi", env=dict(os.environ))`. The launcher process did not
load the same project/global runtime configuration files that
`run/launch_tabbyapi.py` loads. Consequently `TABBYAPI_CMD` was valid and visible
to the API/backend endpoint but missing during engine preflight. The launcher
exited with:

`configured tabbyapi backend is unavailable: TABBYAPI_CMD is not configured or its executable is unavailable`

At 13:41 the persisted chat backend was changed to TGW, after which the unit
started successfully. This mitigated the active outage but did not repair the
failure class.

### Contributing causes

- Runtime-environment loading was duplicated and inconsistent.
- Launcher failures were unstructured string exits, all with ordinary failure
  status.
- Installed host units used `Restart=always` with no
  `RestartPreventExitStatus` distinction.
- A 10-second start-limit window plus 3-second restarts did not constrain this
  pattern, because attempts aged out nearly as quickly as they accumulated.
- Exact installed legacy units were not represented by checked-in source files.
- `/health` treated the API being alive as global success even when a required
  lane was absent.
- Dynamic availability ranking did not include local systemd/listener state.
- Local dispatch discovered a dead lane only by making the inference request.

### Why supervision amplified it

The launcher could never succeed with its observed environment, but systemd was
instructed to restart it after every exit. Static configuration error and
transient engine crash were indistinguishable, producing 4,027 attempts and a
large repeated journal/monitoring signal.

### Consumer and fallback impact

Local-first callers either waited through connection failures before fallback or
received a 502 if no fallback succeeded. Book Analysis and Creekwatch can retry
manager failures, amplifying work. Creative Studio can additionally fail over
directly, bypassing central budget/decision records. Remote fallback can preserve
functionality but may change cost, latency, privacy boundary, and output behavior;
it must be explicit in returned routing metadata.

### Monitoring impact

The repeated message was actionable but had no stable structured failure type or
incident key, so external monitors could treat each recurrence as distinct.
Banana Monitor is outside this repository and was not changed. LLM Manager should
emit one stable event schema/fingerprint and systemd should stop retrying status
78 configuration failures.

### Evidence

- `systemctl show llm-a`: `NRestarts=4027`
- retained `llm-a` journal: repeated missing `TABBYAPI_CMD` preflight message every
  approximately three seconds
- `git blame`: registry preflight added by `e3005b1`
- launcher source: probe environment was only `os.environ`
- Tabby launcher source: loads global/project runtime files before execution
- installed unit: `Restart=always`, `RestartSec=3`
- current backend API: Tabby runtime is available and revision pin matches once
  the full runtime configuration is used

## Contract analysis

### Current public contracts

- Liveness/status: `/health`, `/router/health`, `/engines/status`
- Routing: `/router/chat`, `/router/completions`, `/router/embed`,
  `/router/route-test`
- Lifecycle: `/switch`, `/models/load`, `/models/unload`,
  `/engines/{mode}/{action}`
- Governance/diagnostics: provider policy/catalog/state and router trace endpoints
- OpenAI-compatible local backend contracts on ports 8500-8503

### Inconsistencies and risks

- `/health.ok` means API liveness, not all-lane readiness; its name encouraged
  stronger interpretations.
- There was no `/ready` aggregate despite ecosystem conventions.
- `/router/health` validates router configuration but not local capability
  readiness.
- `/engines/status` exposed listener/systemd fields but no normalized
  availability/failure classification.
- A local candidate was selected from the catalog without first excluding a
  known inactive unit.
- Some consumers still call direct port 8500, bypassing broker policy.
- The API has no authentication. This pass intentionally does not add it; the
  existing Apache/network boundary and privileged lifecycle exposure remain a
  beta-readiness risk.

### Stabilization/versioning

Preserve existing endpoints and fields. Add `/ready`, normalized lane readiness,
and degradation/fallback fields additively. Do not change router response models
or existing 2xx/4xx/5xx meanings. A future `/v1` broker surface should be planned
only when a breaking contract is required; current consumer migration cost does
not justify it now.

## Security analysis

- API exposure: loopback service with Apache lab proxy; no app authentication.
  Lifecycle and governance mutations therefore rely on network/Apache trust.
- Lifecycle authority: API systemctl actions are scoped to configured unit names;
  packaged deployment uses a constrained sudoers rule. Host checkout currently
  runs as `nova`, increasing impact if the API is compromised.
- Subprocess execution: managed jobs use fixed script maps, path confinement,
  persisted process identity, and PID-safe cancellation. Backend commands still
  require explicit executable/path validation.
- Path allowlists: model and job paths are confined to managed roots, including
  symlink resolution.
- Environment handling: secret files are ignored and 0600, but duplicated env
  loading caused correctness drift. Logs and HTTP backend probes must never
  expose command arguments or credentials.
- Provider credentials: centralized in environment files; public backend status
  strips command arguments. Credential presence should be reported only as
  `[SET]`/`[MISSING]`.
- Request logging: middleware logs method/path/status/duration/request ID, not
  bodies. Router state redacts credential-like metadata but consumer-supplied
  free-form metadata should stay minimal.
- Cost/rate controls: curated-catalog enforcement, capability fail-closed
  behavior, paid-provider budgets, and the OpenRouter wait queue are present.
- Resource controls: per-slot CUDA, vLLM memory/tensor parallelism, and llama.cpp
  controls exist. Resource allocation failures still originate in backend logs
  and are transient unless a later classifier can prove they are static.
- Accepted exception: standalone TGW remains bound to `0.0.0.0` without
  authentication by explicit operator decision. It should be labeled clearly,
  monitored, and never reused as a privileged manager API boundary.

## Persistence and recovery analysis

- SQLite migrations fail closed on a newer schema and apply numbered upgrades.
- Managed conversion/training jobs persist process identity and reconcile after
  API restart; evaluation jobs are requeued/reconciled.
- Runtime state is not a substitute for curated configuration, model inventory,
  or engine units.
- SQLite should be backed up with the application state while the service is
  quiesced or via SQLite's backup mechanism. WAL/sidecar handling must be included
  if journal mode changes.
- Model weights, engine checkouts, venvs, logs, and generated reports should not
  be ordinary repo backups. The project `.codex.toml` excludes these large or
  generated classes.
- Corrupt SQLite recovery is not fully operator-automated. Add a future doctor
  check/backup-and-rebuild runbook rather than silently discarding state.

## Testing analysis

### Existing coverage

The repository has pytest, JavaScript tests, Ruff, CI, dependency locks,
packaging metadata, a clean-host bootstrap/doctor check, schema/recovery tests,
routing integration tests, security/path/XSS tests, and model readiness/lifecycle
tests. The configured coverage floor is 30%.

### Missing incident-class coverage at baseline

- launcher uses the effective runtime env rather than process-only env
- typed exit 78 for missing backend/executable/model alias
- no process execution after failed preflight
- distinct port-collision classification
- systemd source contains bounded restart and config-error suppression
- engine readiness classification for disabled/unconfigured/failed lanes
- router skips a known-unavailable local lane before dispatch
- additive remote-fallback/degraded metadata
- stable redacted incident key/event fields

### Recommended verification order

1. `python -m pytest tests/test_engine_stabilization.py tests/test_capabilities_and_readiness.py tests/test_router_endpoints_integration.py`
2. `python -m llm_manager.deployment doctor --asset-root .`
3. `python -m pytest tests/test_runtime_migrations_and_recovery.py tests/test_routing_security.py tests/test_secrets.py`
4. `ruff check llm_manager tests scripts run/*.py`
5. `python -m pytest`
6. `node --test tests/js/*.test.mjs`
7. launcher `--preflight-only` against a non-running optional slot or with port
   collision checking disabled
8. controlled unit reload/restart and post-restart listener/journal/restart-count
   checks

## Documentation authority

- Canonical current state: `README.md`, with this dated review as deployment
  evidence.
- Canonical API contract: `docs/API.md`.
- Canonical operator runbook: `docs/CLI_SYSOP_GUIDE.md`.
- Canonical roadmap/backlog after this pass: `docs/plans/ROADMAP.md` and
  `docs/plans/CHECKLIST.md`.
- `docs/plans/NEXT_PUSH.md` contains valuable implementation history but also
  stale “next” material; it should be relabeled as historical delivery notes or
  reduced to a pointer to the canonical roadmap.
- `docs/plans/DESIGN_DECISIONS.md` contains resolved questions (SQLite, first
  broker surfaces, Tabby lifecycle, EXL3 workflow) and must be refreshed.
- `docs/reports/*` are historical evidence, not current architecture or backlog.
  Keep them dated; do not use them as current model-health truth.
- The standards repository's LLM Manager API/CLI copies lag current package,
  SQLite, backend, and recovery behavior. They cannot be updated in this pass;
  project documentation is authoritative until a later standards sync.
- A project `docs/INDEX.md` is absent and should be added to make authority
  discoverable.

## Modernization review

| Current approach | Proposed approach | Benefit | Cost / risk | Classification |
|---|---|---|---|---|
| Process-only launcher preflight env | one shared effective runtime-env loader | removes configuration drift and fixes the incident cause | small; precedence must stay documented | required now |
| Unstructured launcher exits | typed preflight result, stable event, sysexits-compatible codes | actionable operations and safe supervision | small; preserve concise logs | required now |
| `Restart=always` legacy units | `on-failure`, start limit, backoff, prevent exit 78 | stops static storms while retaining transient recovery | small; requires daemon reload/restart | required now |
| Listener-only status fields | normalized per-lane readiness and `/ready` | consumers can distinguish liveness/degradation | medium; additive contract | required now |
| Local dispatch always attempted | skip known-unavailable managed unit, retain explicit attempt trace | avoids repeated connection failures | medium; must fail open only when unit ownership is unknown | required now |
| Host units not fully tracked | exact project-checkout host unit sources plus packaged template | reproducible operations | small/medium | required now |
| Monolithic `server.py` | incrementally extract engine/readiness and router services | testability and ownership | medium/high; merge risk | recommended during normal work |
| JSON sections inside SQLite | normalized high-value tables only where queries/migrations justify it | query efficiency and retention | medium/high | optional later |
| Python 3.10 | plan 3.12 migration with engine compatibility matrix | longer support horizon | medium; GPU/runtime compatibility risk | recommended during normal work |
| FastAPI app with no auth | scoped operator auth at Apache or API, separate inference vs lifecycle roles | protects privileged mutations | medium/high and explicitly deferred by operator | P3, not required now |
| TGW compatibility lane | preserve while preferred backends mature | avoids needless rewrite/format regressions | replacement has high risk/no current benefit | preserve |
| SQLite runtime store | keep; add backup/integrity runbook | already solves concurrency/durability needs | low | preserve |
| Separate provider adapters | keep and tighten normalized error contracts | stable extension point | low | preserve |
| Immediate move to containers/Kubernetes | no change | no concrete benefit on this single-host GPU deployment | high | not justified |

## Prioritized work

### P0 active incident

- Prevent recurrence of static backend/model configuration restart storms.
- Align launcher preflight with the same effective runtime configuration used by
  backend launchers.
- Deploy repository-backed bounded restart policy to LLM Manager engine units.

### P1 foundation blocker

- Add typed lane readiness and a canonical `/ready`.
- Skip a known-unavailable managed local lane before network dispatch.
- Preserve explicit fallback/degradation metadata.
- Track exact installed host-checkout unit sources.
- Resolve `llm-b`: retire its stale alias without selecting a model, because the
  operator explicitly wants no default intent model.

### P2 beta-readiness

- Add a project documentation index and consolidate roadmap/status authority.
- Raise coverage progressively from 30%, emphasizing router/lifecycle contracts.
- Add a SQLite integrity/backup/restore drill and operator runbook.
- Add consumer contract fixtures for request ID propagation and degraded routing
  metadata.
- Validate all packaged and project-checkout units from a clean host fixture.
- Split lifecycle/governance authority from inference-only access.

### P3 production hardening

- Authentication/authorization at the API or Apache boundary.
- Rate limits for privileged and expensive endpoints.
- Python 3.12/engine compatibility and migration.
- Structured metrics and external alert dedup based on incident keys.
- Normalize selected SQLite sections only when query requirements justify it.

### Defer

- Kubernetes/container orchestration.
- Wholesale router rewrite.
- Replacement of working TGW/SQLite architecture merely for novelty.
- Broad provider retry automation, which risks duplicate paid calls.

### Preserve

- Transactional model switching and exact-model readiness rollback.
- Capability fail-closed behavior.
- Curated catalog and paid-budget enforcement.
- SQLite/job identity recovery.
- Provider adapter boundary.
- Direct llama.cpp and vLLM embedding lanes.
- Pinned Tabby revision.

## Implementation slices

### Slice A — incident-class launcher and supervision stabilization

- Objective: fail static configuration once, with a structured redacted event,
  while retaining bounded restart recovery for transient crashes.
- Likely files: `run/engine_launcher.py`,
  `llm_manager/backend_registry.py`, `deploy/systemd/*`, deployment tests, new
  stabilization tests.
- Tests: missing command/backend/model alias, incompatible backend, port
  collision, no exec after preflight failure, exit codes, unit directives,
  secret redaction.
- Deployment impact: install updated LLM Manager-owned unit sources, daemon
  reload, sequential restart of currently active owned units only.
- Rollback: reinstall captured prior unit files and daemon reload; source changes
  remain uncommitted and can be manually reviewed.
- Completion: exit 78 is not restarted; transient exits remain restartable but
  bounded; healthy chat/embed/API remain functional.
- Dependencies: none.

### Slice B — readiness and unavailable-lane routing

- Objective: represent lane/capability state accurately and avoid calls to known
  unavailable managed units.
- Likely files: `llm_manager/server.py`, router integration/readiness tests,
  API/operator docs.
- Tests: disabled/unconfigured/config-failed/starting/ready states, `/ready`
  status, local skip, remote fallback metadata, no-fallback 502 detail.
- Deployment impact: API restart only; additive HTTP fields/endpoint.
- Rollback: revert API source and restart API.
- Completion: `/health` remains compatible; `/ready` and `/engines/status`
  explain degradation; router attempt trace contains `local_lane_unavailable`
  without making a local POST.
- Dependencies: Slice A failure types.

### Slice C — deployment source and documentation authority

- Objective: make current host and packaged units reproducible and reduce
  conflicting current-state docs.
- Likely files: `deploy/systemd/`, `llm_manager/deployment.py`, `pyproject.toml`,
  `docs/INDEX.md`, README, API/runbook, roadmap/checklist/decisions.
- Tests: deployment doctor/bootstrap and unit-source assertions.
- Deployment impact: unit installation as part of Slice A; docs only otherwise.
- Rollback: retain prior installed-unit capture and reinstall if necessary.
- Completion: every installed owned unit has an identified repository source;
  docs point to one current roadmap and this review.
- Dependencies: final Slice A/B behavior.

### Slice D — later beta hardening

- Objective: close remaining auth, recovery-drill, coverage, type-checking, and
  consumer-versioning gaps.
- Likely files: future plan after operator approval.
- Tests: end-to-end contract suite, backup restore, permissions, auth roles.
- Deployment impact: potentially breaking; not part of this pass.
- Rollback: slice-specific.
- Completion: separately approved acceptance criteria.
- Dependencies: stable foundation from A-C.

## `llm-b` alias decision

The intent slot was deliberately left unloaded at the operator's request, but
`intent_active_model` still pointed to a removed checkpoint. Silently choosing a
replacement would violate that intent. The correct stabilization is to retire the
stale active alias while keeping `/models.intent` populated from capability
metadata. The disabled `llm-b` unit then remains operator-unconfigured until a
model is explicitly loaded.

## Proposed changes for 2bananas standards

- Define sysexits-compatible service failure classes, reserving status 78 for
  static configuration failures.
- Require `Restart=on-failure`, `RestartPreventExitStatus=78`, meaningful
  `StartLimitIntervalSec/StartLimitBurst`, and a bounded backoff for managed
  application services.
- Distinguish `/health` liveness from `/ready` dependency/capability readiness.
- Require service registries to include unit source path, owner, bind, capability,
  readiness contract, and operator-disabled state.
- Require one shared, tested environment loader per application and documented
  precedence without printing values.
- Require installed units/drop-ins to have an exact checked-in or generated
  source and a drift check.
- Standardize stable incident keys so Banana Monitor can suppress repeated
  identical configuration failures while preserving state transitions.
- Add LLM broker response guidance for local success, remote fallback,
  degradation, and unavailable capability.

## Verification record

### Implemented

- Added one effective runtime environment loader used by engine and TGW/Tabby
  launcher preflight.
- Added structured `engine_preflight_failed`/`engine_preflight_succeeded` events,
  stable incident keys, secret-like value redaction, and status 78/75 failure
  classes.
- Expanded Tabby validation to require its executable and Python script/module
  entrypoint plus the pinned revision.
- Added bounded systemd supervision to the packaged template, embedding unit,
  and exact host-checkout sources for `llm-a/b/c` and standalone TGW.
- Added normalized local lane availability, `/ready`, readiness context in
  `/health`, `/router/health`, and `/engines/status`, and direct systemctl reads
  without repeated privileged sessions.
- Added router short-circuiting for known-unavailable managed local lanes and
  additive fallback `service_state`/`degraded` metadata.
- Retired the broken `llm-b` project link and shared
  `intent_active_model` link. No replacement was selected; `/models` still
  exposes eight compatible intent candidates.
- Added incident-class regression coverage and deployment-doctor checks.
- Added `docs/INDEX.md` and refreshed current API, operator, systemd, TGW,
  roadmap, checklist, and decision authority.

### Test and static verification

| Command/check | Result | State change |
|---|---|---|
| `python -m pytest` in an isolated venv installed from `requirements/dev.lock` | 78 passed; branch coverage 38.91%, above 30% gate | generated ignored coverage files |
| targeted incident/readiness/router/path/deployment/security/recovery suites | passed (55-test and 25-test slices, then full suite) | none |
| `node --test tests/js/*.test.mjs` | 1 passed | none |
| `ruff check ...` | passed | none |
| `python -m compileall -q llm_manager run` | passed | ignored cache files only |
| `python scripts/check_secrets.py` | passed, 168 tracked files scanned at that point | none |
| `python -m llm_manager.deployment doctor --asset-root .` | passed, 20 assets, config/runtime schema 2 | temporary SQLite only |
| locked wheel build, target install, packaged doctor, bootstrap, package import | passed; 28 clean-target assets created | temporary directories only |
| `systemd-analyze verify` on repository units | project units valid; host emitted unrelated pre-existing netplan permission and snapd-version warnings | none |
| `git diff --check` | passed | none |

The existing service venv intentionally contains runtime dependencies only and
could not import pytest. The host pytest lacked the coverage plugin. The
canonical test/coverage command was therefore run in an isolated temporary venv
created from the exact development lock; neither service environment was
modified.

### Controlled service actions

Initial active-state/restart counts were captured at 14:23 CDT. Then:

1. Installed the checked-in `llm-a`, `llm-b`, `llm-c`, standalone TGW, resource
   drop-ins, and embedding unit; ran `systemctl daemon-reload`.
2. Restarted `llm-a`; its model endpoint became ready in approximately 12
   seconds.
3. Restarted `llm-embed`; its model endpoint became ready in approximately 42
   seconds.
4. Restarted standalone TGW; its API/WebUI listeners became ready in
   approximately 13 seconds.
5. Restarted `llm-manager-api` to activate the additive API behavior (and once
   more after removing privileged reads from health polling).
6. Manually started disabled/unconfigured `llm-b` once as a supervision proof.
   It emitted one `missing_model` event, exited 78, remained at `NRestarts=0`
   beyond the 10-second restart interval, and was reset to its prior
   disabled/inactive state.

`llm-c` was not started or restarted. No unrelated unit or Apache configuration
was changed.

### Live verification

- `/health`: HTTP 200, API alive, chat/embed up, intent unconfigured, small
  operator-disabled.
- `/ready`: HTTP 200; chat, completions, and embeddings available; additive
  `degraded=true` because the optional intent/small lanes are unavailable.
- `/engines/status`: normalized states are `chat=ready`,
  `intent=operator_unconfigured`, `small=operator_disabled`, `embed=ready`.
- `/backends`: TGW, pinned TabbyAPI, vLLM, and llama.cpp all probe available;
  command arguments remained hidden.
- Exact model identity: chat reported `Qwen3.5-4B-Q6_K`; embedding reported
  `BAAI__bge-small-en-v1.5`.
- Local inference: chat returned a choices array and usage object; embedding
  returned a 384-element vector and usage object. Response/prompt contents were
  not retained in this review.
- Unavailable-lane proof: a local-only request constrained to
  `small_active_model` returned 502 with one skipped
  `local_lane_unavailable` attempt, no dispatch error, stable incident key,
  `service_state=unavailable`, and `degraded=true`.
- Installed-source drift: `cmp` matched all six installed unit sources to their
  repository counterparts.
- Final active units (`llm-manager-api`, `llm-a`, `llm-embed`,
  `llm-tgw-webui`) all show `NRestarts=0`, `ExecMainStatus=0`, and
  `Result=success`. `llm-b/c` are disabled/inactive with the same clean counters.
- Final listeners: 8101, 8500, and 8503 are loopback; standalone TGW remains on
  public 7860/8510 by accepted operator decision; 8501/8502 have no listeners.

### Skipped or intentionally deferred

- No paid or live remote-provider inference was called. Remote fallback behavior
  was verified with mocked HTTP integration tests; live routing used
  `allow_fallbacks=false`.
- Apache, global environment, global standards, Banana Monitor, and consumer
  projects were read-only.
- Authentication/authorization remains deferred per operator instruction.
- A destructive SQLite restore drill and GPU-failure injection were not
  appropriate for this live stabilization pass.

### Final assessment

The active restart storm is eliminated and its static-configuration recurrence
path is covered by source, deployed supervision, and a live exit-78 proof. The
API and working lanes are functional, optional unavailable lanes are explicit,
and the router does not issue inference requests to a known-unavailable managed
lane. This is a materially safer beta foundation, but not a production-readiness
claim: privileged API authorization and practiced state recovery remain open.
