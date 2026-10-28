# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - Unreleased

The SDK is now a CrewAI adapter over [`openbox-sdk-python`](https://github.com/OpenBox-AI/openbox-sdk-python)
(`openbox_core`) instead of carrying its own copy of the governance client, signing,
payloads and instrumentation. The public CrewAI API (`create_openbox_engine`,
`create_openbox_flow`, `OpenBoxAgent`, `OpenBoxTask`, `GovernedCrew`, `OpenBoxEngine`) is unchanged.

### Added
- Okta AI Agent identity (`{PREFIX}_OKTA_AGENT_PRIVATE_KEY`, with metadata fetched from Core or set explicitly) and Keycloak workload identity (`{PREFIX}_WORKLOAD_PRIVATE_KEY`), per agent. Requests route to Core's v2 and v3 APIs accordingly.
- `OpenBoxSigningError` with a `reason_code` when Core rejects a signed request.
- `WorkflowFailed` is sent when a kickoff raises, with the error type and message, and for sessions left open by a crashed run.
- `@governed` function decorator (Layer 3 `function_call` operations).
- HTTP instrumentation for `urllib` and `httpx2`, and capture of streamed `httpx` response bodies.
- OpenRouter routing provenance for governed model calls (from `openbox_core`).
- Engine options `hitl_max_wait`, `instrument_http`, `max_body_size` and `redact_keys`.
- New result fields on `EvaluationResult`: `trust_tier`, `alignment_score`, `behavioral_violations`, `constraints`, `diagnostics`, `patch`.
- `ContractError`, `ApprovalRejectedError`, `ApprovalTimeoutError` and `GuardrailsValidationError` exported from `openbox`.

### Changed
- **Python 3.11 or newer is required.**
- **`401`/`403` from Core always fail closed** with `OpenBoxAuthError`, even under `governance_policy="fail_open"`. A revoked key no longer gets a fallback allow.
- **`governance_policy="fail_closed"` now covers Layer 3.** When Core is unreachable, HTTP/DB/file operations inside a task are stopped with `GovernanceHaltError`. Previously these evaluations failed open regardless of policy.
- **`BLOCK` raises `GovernanceBlockedError` at every layer** and only `HALT` raises `GovernanceHaltError`. A `HALT` from a Layer 3 operation now also halts the agent for the rest of the kickoff.
- Layer 3 operations are sent with the credentials of the agent running the task, through `openbox_core`'s instrumentation. When CrewAI swallows a Layer 3 verdict inside a tool, the verdict is re-raised from the task.
- Each agent reads credentials from its own `env_prefix` only; global `OPENBOX_*` credentials are never shared between agents. `{PREFIX}_AGENT_DID` / `{PREFIX}_AGENT_PRIVATE_KEY` are accepted alongside `{PREFIX}_DID` / `{PREFIX}_PRIVATE_KEY`.
- Event payloads follow the `openbox_core` wire contract: `source` is `"workflow-telemetry"`, spans are flat (no nested `data`), and `semantic_type` is computed by Core.
- `X-OpenBox-SDK-Version` is `openbox-crewai-python-v3.0.0`.
- Approval polling backs off on failures and gives up with `ApprovalTimeoutError` after 60 consecutive failed polls; a failed poll no longer raises `GovernanceAPIError`.
- Failed guardrails (without redacted content) now take precedence over `REQUIRE_APPROVAL`.
- Captured bodies are truncated to 65 536 characters by default (previously unlimited); set `max_body_size` to change it.
- Database drivers are no longer installed by default. Install `openbox-crewai-sdk-python[db]` for SQLAlchemy, DB-API, asyncpg, redis and pymongo instrumentation.

### Removed
- `openbox.core`, `openbox.instrumentation` and `openbox.utils`. Import errors from `openbox` or `openbox.errors`.
- `aiopg` and async `psycopg` (v3) instrumentation, which `openbox_core` does not provide.

### Migration
- `from openbox.core.errors import X` → `from openbox import X` (or `from openbox.errors import X`).
- `GovernanceApprovalExpiredError` and `GovernanceResponse` still work, as aliases of `ApprovalExpiredError` and `EvaluationResult`.
- Code that caught `GovernanceHaltError` for a `BLOCK` at a task boundary should catch `GovernanceBlockedError`.
- `db_libraries` names `psycopg2`, `mysql`, `pymysql` and `sqlite3` all select DB-API instrumentation.

## [1.0.0]

Initial release.
