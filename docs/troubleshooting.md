# Troubleshooting

## Startup errors

| Error | Check |
| --- | --- |
| `OpenBoxConfigError` | `OPENBOX_URL` set; `{PREFIX}_API_KEY` set per agent; only one identity method per agent (DID **or** Okta **or** workload key); DID and private key paired (or both omitted); DID matches `did:aip:<uuid>`; private key is base64-decodable to 32 bytes; Okta metadata either all set or all omitted |
| `OpenBoxAuthError` | API key matches `obx_live_*`/`obx_test_*` and is valid in Core |
| `OpenBoxSigningError` | read `reason_code`: `timestamp_outside_window` means the host clock is off by more than 5 minutes; `did_agent_mismatch` means the DID belongs to another agent; `signature_invalid` means the private key doesn't match the registered key |
| `OpenBoxInsecureURLError` | non-localhost URL is `https://` |
| `OpenBoxNetworkError` | the application can reach `OPENBOX_URL` |

Validation runs at `engine.govern(crew)`, not engine creation. Bind during bootstrap to fail fast.

## No events in OpenBox

- you're running the crew returned by `engine.govern(crew)` — bare `Crew` emits nothing
- the agent is an `OpenBoxAgent` and the task is an `OpenBoxTask`
- the engine isn't being closed before `kickoff()`
- Core is reachable from the runtime

## Guardrail UI test passes but live run doesn't fire

Core evaluates **policy before guardrails**. A non-`ALLOW` policy verdict skips guardrail evaluation. Inspect the `ActivityStarted` event — if policy returned `BLOCK`/`HALT`/`REQUIRE_APPROVAL`, the guardrail never ran.

## DB block policy doesn't fire

The policy is matching `span.db_operation` (Core strips top-level `db_*`). Match `span.attributes["db.operation"]` instead:

```rego
result := {"decision": "BLOCK", "reason": "..."} if {
    some span in input.spans
    span.hook_type == "db_query"
    span.stage == "started"
    upper(object.get(span.attributes, "db.operation", "")) in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE"}
}
```

## File block policy doesn't fire

Match `span.name == "file.write"` (always forwarded by Core), not a stripped attribute.

## Test expects `GovernanceHaltError` but gets `GovernanceBlockedError`

Since 3.0, `BLOCK` raises `GovernanceBlockedError` at every layer; only `HALT` raises `GovernanceHaltError`. Catch `GovernanceBlockedError` for blocks. See [approvals-and-guardrails.md — Layer 3 swallowing caveat](./approvals-and-guardrails.md#layer-3-swallowing-caveat) for how Layer 3 verdicts surface from tools.

## Requests fail with `OpenBoxAuthError` under `fail_open`

Expected: `401`/`403` always fail closed. Check that the agent's API key is valid and not revoked in Core.

## Duplicate approval requests

Policy is treating hook-triggered telemetry as a business action. Limit approval requirements to `ActivityStarted`/`ActivityCompleted`.

## Approval never resolves

- `hitl_enabled=True` and crew not in `exclude_crews_hitl`
- Core eventually returns `allow`/`block`/`halt` for the polled approval ID
- approval window hasn't expired (raises `GovernanceApprovalExpiredError`)

By default polling continues until Core decides or the approval expires. Set `hitl_max_wait` to bound it; polling also stops with `ApprovalTimeoutError` after 60 consecutive failed polls.

## Multiple engines

`OpenBoxConfigError: instrumentation settings changed after startup`. One engine per process. Govern multiple crews against the single engine.

## Local SDK changes not reflected

```bash
uv pip install -e /path/to/openbox-crewai-sdk
```

## Debug logging

```python
engine = create_openbox_engine(debug_log=True)
```

Per-agent trace records: evaluate payloads, response verdicts, approval polling cycles.

For payload-level capture in tests, use the `capture_engine_evaluations` fixture in `tests/e2e/conftest.py`.
