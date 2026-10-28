# Architecture

The SDK is a CrewAI adapter over [`openbox-sdk-python`](https://github.com/OpenBox-AI/openbox-sdk-python) (`openbox_core`). The base SDK owns the governance contract: configuration, identity and request signing, the HTTP client and fail policy, event validation, verdict parsing, and HTTP/DB/file/function instrumentation. This package maps CrewAI crews, agents and tasks onto it.

## Layout

```
CrewAI app  →  OpenBoxEngine  →  GovernedCrew  →  OpenBoxAgent (per-agent governance)
                  │                                       │
                  ├─ OpenBoxRuntime (openbox_core)        ├─ AgentClient (own key + identity)
                  │   ├─ instrumentation                  │    signs and sends Layer 1/2 events
                  │   │  (HTTP / DB / file / function)    │
                  │   └─ CrewAIAdapter (native errors,    │
                  │      approvals)                       │
                  └─ AgentRoutingClient ──────────────────┘
                     (Layer 3 → acting agent's client)
                                       │
                                       ▼
                                OpenBox Core
                     /api/v1|v2|v3/auth/validate
                     /api/v1|v2|v3/governance/evaluate
                     /api/v1|v2|v3/governance/approval
```

The API version follows the agent's identity: OpenBox DID and API-key-only agents use v1, Okta AI Agent identities use v2, and Keycloak workload identities use v3.

## Components

| Component | Responsibility |
| --- | --- |
| `OpenBoxEngine` | owns the process-wide `openbox_core` runtime and instrumentation, the per-agent clients, and approval polling; `engine.govern(crew)` binds crews |
| `GovernedCrew` | manages the governance lifecycle around `kickoff()` / `akickoff()`: opens and closes per-agent sessions, recovers from prior crashed runs, and installs the LLM gate |
| `OpenBoxAgent` | resolves credentials from `env_prefix`, runs Layer 1 / Layer 2 evaluation around each task, binds the task as the active `openbox_core` activity so Layer 3 operations are attributed to it, and emits `WorkflowStarted` lazily on the first task |
| `CrewAIAdapter` (internal) | turns `openbox_core` verdicts into `GovernanceBlockedError` / `GovernanceHaltError` and drives approvals |
| `AgentRoutingClient` (internal) | sends each Layer 3 evaluation and approval poll with the credentials of the agent whose session it belongs to |

## Boundary vs telemetry

The SDK distinguishes:

- **business boundary events** — `ActivityStarted`/`ActivityCompleted` per task, `WorkflowStarted`/`WorkflowCompleted`/`WorkflowFailed` per agent session
- **internal telemetry** — Layer 3 hook payloads for HTTP, DB, file and function operations

Policy is easiest to write at boundary events. Treating hook payloads as if they were business actions creates duplicate approvals.

## Per-run lifecycle

When `governed_crew.kickoff()` runs:

1. any session left open by a prior crashed run is closed (`WorkflowFailed` is emitted) and per-agent state is reset
2. governance is wired into each agent
3. CrewAI runs the configured `Process`
4. sessions are closed — `WorkflowCompleted` on success, `WorkflowFailed` with the error when the kickoff raised — and per-agent state is reset, so the same crew is safe to reuse

## Usage rules

- one `OpenBoxEngine` per process; close it on shutdown (or use `with`)
- changing an engine's instrumentation settings after it started raises `OpenBoxConfigError`
- govern any number of crews against a single engine
- `engine.govern(crew)` is idempotent on a given crew
- a plain `Agent` in the same crew is allowed and emits no governance events
- a hierarchical crew's `manager_agent` is governed when it is an `OpenBoxAgent`

### Multi-crew correlation

When a CrewAI `Flow` orchestrates multiple governed crews, wrap the flow with `create_openbox_flow(FlowClass)` to share a `flow_execution_id` across their events. The flow itself is not governed — only correlation. See [api-reference.md](./api-reference.md#create_openbox_flowflow_class-flow_kwargs).

## Failure model

| Failure | Surface |
| --- | --- |
| OpenBox API unreachable | soft-allow with `fallback_used=true` (`fail_open`); `GovernanceAPIError` at task boundaries and `GovernanceHaltError` for Layer 3 operations (`fail_closed`) |
| `401`/`403` from Core | `OpenBoxAuthError` / `OpenBoxSigningError`, regardless of policy |
| `BLOCK` at Layer 1/2 | `GovernanceBlockedError` |
| `HALT` at any layer | `GovernanceHaltError`; the agent refuses further tasks this kickoff |
| `BLOCK` at Layer 3 | `GovernanceBlockedError` (re-raised at the task boundary if CrewAI swallowed it in a tool — see [approvals-and-guardrails.md](./approvals-and-guardrails.md)) |
| Guardrails failed without a redaction | `GuardrailsValidationError` |
| Approval rejected / expired / timed out | `GovernanceBlockedError` / `GovernanceApprovalExpiredError` / `ApprovalTimeoutError` |
| Underlying CrewAI failure | unchanged |
