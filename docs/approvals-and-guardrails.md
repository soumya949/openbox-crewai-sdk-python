# Approvals and Guardrails

## Verdicts

| Verdict | Effect |
| --- | --- |
| `ALLOW` | continue |
| `CONSTRAIN` | log the constraint and continue |
| `REQUIRE_APPROVAL` | poll for approval if `hitl_enabled`, else `on_fallback` |
| `BLOCK` | raise `GovernanceBlockedError` |
| `HALT` | raise `GovernanceHaltError`; subsequent tasks on the same agent short-circuit |

Failed guardrails rank above `REQUIRE_APPROVAL`: an event that both fails guardrails and requires approval raises `GuardrailsValidationError` without asking a human.

Legacy strings are normalized and unknown values fall back to `ALLOW` with a warning. See [api-reference.md — Verdicts](./api-reference.md#verdicts) for the alias mapping.

## Errors

| Class | When |
| --- | --- |
| `GovernanceBlockedError` | `BLOCK` at a task boundary or Layer 3 operation (HTTP/DB/file/function), or an approval rejected with `block` |
| `GovernanceHaltError` | `HALT` at any layer, or an approval rejected with `halt` |
| `GovernanceAPIError` | API failure with `governance_policy=fail_closed` |
| `GuardrailsValidationError` | `guardrails_result.validation_passed=false` with no redacted content |
| `GovernanceApprovalExpiredError` | approval window expired (alias of `ApprovalExpiredError`) |
| `ApprovalTimeoutError` | `hitl_max_wait` elapsed, or Core stayed unreachable while polling |

All inherit from `OpenBoxError` and are registered as CrewAI passthroughs (no retries).

## Guardrail redaction

OpenBox responses can carry `guardrails_result`:

- `input_type="activity_input"` + `redacted_input` → SDK applies redaction to `task.description` before execution
- `input_type="activity_output"` + `redacted_input` → SDK applies redaction to the result before return
- `validation_passed=false` without `redacted_input` → SDK raises `GuardrailsValidationError`

## Policy-before-guardrails caveat

Core evaluates policy **before** guardrails. If policy returns a non-`ALLOW` verdict, guardrails for that event may not run. If a guardrail you expect to fire doesn't, check the policy verdict first.

## Layer 3 swallowing caveat

When Layer 3 raises `GovernanceBlockedError` from inside a CrewAI tool, the framework may catch it and hand the error string to the LLM as a tool result. The SDK remembers the verdict for that task: the next LLM call in the task is refused, and when the task ends — normally or with CrewAI's own error — the original `GovernanceBlockedError` / `GovernanceHaltError` is raised from the task.

Writing the policy at Layer 1 against `activity_input.description` still stops the task earlier, before any tool runs. Keep a Layer 3 policy for defence-in-depth.

## OPA matching shortlist

| Boundary | Match against |
| --- | --- |
| `ActivityStarted` | `input.activity_input[*].description` |
| `ActivityCompleted` | `input.activity_output.result` |
| DB hook | `input.spans[*].attributes["db.operation"]` |
| File hook | `input.spans[*].name == "file.write"` plus `span.file_path` |
