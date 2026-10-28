# API Reference

## Imports

```python
from openbox import (
    OpenBoxAgent,
    OpenBoxTask,
    OpenBoxEngine,
    GovernedCrew,
    GovernanceConfig,
    Verdict,
    EvaluationResult,
    create_openbox_engine,
    create_openbox_flow,
    governed,
)

from openbox import (
    OpenBoxError,
    OpenBoxConfigError,
    OpenBoxAuthError,
    OpenBoxSigningError,
    OpenBoxNetworkError,
    OpenBoxInsecureURLError,
    ContractError,
    GovernanceAPIError,
    GovernanceHaltError,
    GovernanceBlockedError,
    GuardrailsValidationError,
    ApprovalExpiredError,           # alias: GovernanceApprovalExpiredError
    ApprovalRejectedError,
    ApprovalTimeoutError,
)
```

Treat names re-exported from `openbox` (and `openbox.errors`) as the public surface. Other subpath imports (`openbox.crewai.*`, `openbox.engine`, `openbox._*`) are internal. `GovernanceResponse` remains as an alias of `EvaluationResult`.

## Entry points

### `create_openbox_engine(...)`

```python
engine = create_openbox_engine(
    api_url=None,                       # OPENBOX_URL env if None
    config=None,                        # if set, ignores the kwargs below
    governance_timeout=30.0,
    governance_policy="fail_open",      # or "fail_closed"
    on_fallback="log_warning",          # or "fail_closed"
    send_task_start_event=True,
    send_task_completed_event=True,
    llm_level_governance=True,
    hitl_enabled=True,
    hitl_poll_interval=5.0,
    hitl_max_wait=None,
    exclude_crews_hitl=None,
    instrument_http=True,
    instrument_databases=True,
    db_libraries=None,
    instrument_file_io=False,
    max_body_size=None,                 # openbox_core default: 65536 characters
    redact_keys=None,
    debug_log=False,
)
```

Returns `OpenBoxEngine`. Use as a context manager.

### `OpenBoxEngine`

| Member | Purpose |
| --- | --- |
| `engine.govern(crew)` | bind a `Crew` (or `GovernedCrew`); returns the `GovernedCrew` |
| `engine.config` | resolved `GovernanceConfig` |
| `engine.close()` | flush pending telemetry, tear down instrumentation and HTTP clients |

`OpenBoxEngine` is a context manager — use `with create_openbox_engine() as engine:` so `close()` runs on exit.

### `OpenBoxAgent`

Subclass of CrewAI `Agent`. Required field: `env_prefix: str` — the agent's credentials and identity are read from `{PREFIX}_*` only (`{PREFIX}_API_KEY`, plus the identity variables in [configuration.md](./configuration.md#agent-identity)).

### `OpenBoxTask`

Subclass of CrewAI `Task`. Required field: `activity_type: str` — passed verbatim into governance payloads. A plain `Task` that reaches an `OpenBoxAgent` (for example through delegation) is governed with `activity_type="delegation"`.

### `GovernedCrew`

Subclass of CrewAI `Crew` returned by `engine.govern(crew)`. Override of `kickoff()` / `akickoff()` runs the governance lifecycle.

### `create_openbox_flow(flow_class, **flow_kwargs)`

Wraps a CrewAI `Flow` so its `kickoff()`/`akickoff()` set a `flow_execution_id` `ContextVar` for the duration of the run. Every governed crew kickoff inside the flow includes that ID in its events. The flow itself is not governed.

### `@governed(name=None, capture_args=True, capture_result=True)`

From `openbox_core`. Decorate a sync or async function to evaluate each call as a Layer 3 `function_call` operation when it runs inside a governed task. Outside a task it runs ungoverned.

## Verdicts

```python
Verdict.ALLOW
Verdict.CONSTRAIN
Verdict.REQUIRE_APPROVAL
Verdict.BLOCK
Verdict.HALT
```

Enforcement order: `HALT` > `BLOCK` > failed guardrails > `REQUIRE_APPROVAL` > `CONSTRAIN` > `ALLOW`. `CONSTRAIN` is logged and execution continues.

| Method | Purpose |
| --- | --- |
| `verdict.should_stop()` | `True` for `BLOCK`/`HALT` |
| `verdict.requires_approval()` | `True` for `REQUIRE_APPROVAL` |
| `Verdict.from_string(value)` | parse a string verdict; unknown values → `ALLOW` with a warning |
| `Verdict.highest_priority(*verdicts)` | strongest verdict from a list |

Legacy aliases accepted by `Verdict.from_string`:

| Legacy string | Maps to |
| --- | --- |
| `continue` | `ALLOW` |
| `stop` | `HALT` |
| `require-approval` | `REQUIRE_APPROVAL` |

## `EvaluationResult`

Fields: `verdict`, `reason`, `policy_id`, `risk_score`, `governance_event_id`, `guardrails` (alias `guardrails_result`), `approval_id`, `approval_expiration_time`, `trust_tier`, `alignment_score`, `behavioral_violations`, `constraints`, `fallback_used`, `patch`, `raw`.

When Core returns `fallback_used=true` and `on_fallback="fail_closed"`, the SDK overrides the verdict to `BLOCK` after parsing.

## Errors

| Class | When |
| --- | --- |
| `OpenBoxConfigError` | invalid configuration (base of the auth/network/URL errors below) |
| `OpenBoxAuthError` | Core returns 401/403, at validation or on any request |
| `OpenBoxSigningError` | Core rejected a signed request; carries `reason_code` |
| `OpenBoxNetworkError` | engine cannot reach Core during validation |
| `OpenBoxInsecureURLError` | non-localhost `OPENBOX_URL` is HTTP |
| `ContractError` | a governance event failed `openbox_core` validation before sending |
| `GovernanceAPIError` | API failure at a task boundary with `governance_policy="fail_closed"` |
| `GovernanceBlockedError` | `BLOCK` at a task boundary or Layer 3 operation, or a rejected approval; carries `verdict`, `reason`, `policy_id` |
| `GovernanceHaltError` | `HALT` at any layer, or an unreachable Core at Layer 3 under `fail_closed`; carries `verdict`, `reason`, `policy_id` |
| `GuardrailsValidationError` | guardrails failed without returning redacted content; carries `reasons` |
| `ApprovalExpiredError` (`GovernanceApprovalExpiredError`) | approval window expired |
| `ApprovalTimeoutError` | `hitl_max_wait` elapsed, or Core stayed unreachable while polling |

All inherit from `OpenBoxError` and are CrewAI passthroughs (no retries). `GovernanceBlockedError` and `GovernanceHaltError` subclass the `openbox_core` classes of the same name.

## Example

```python
from crewai import Crew, Process
from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

with create_openbox_engine() as engine:
    agent = OpenBoxAgent(
        role="Researcher",
        goal="Find information",
        env_prefix="OPENBOX_RESEARCHER",
    )
    task = OpenBoxTask(
        description="Research RAG.",
        expected_output="Notes.",
        agent=agent,
        activity_type="research",
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
    result = engine.govern(crew).kickoff()
```

Async equivalent: `await engine.govern(crew).akickoff()`.
