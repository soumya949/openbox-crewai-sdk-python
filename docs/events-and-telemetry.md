# Events and Telemetry

The SDK emits two kinds of payload to the governance evaluate endpoint: **boundary events** at session and task start/end, and **hook payloads** from HTTP / DB / file / function telemetry. Payload assembly, validation and span projection come from `openbox_core`, so the wire format matches every other OpenBox SDK.

## Event types

| Event | When | Layer |
| --- | --- | --- |
| `WorkflowStarted` | first task per agent per kickoff | session open |
| `WorkflowCompleted` | kickoff finished | session close (`status=completed`) |
| `WorkflowFailed` | kickoff raised, or a session left open by a crashed run | session close (`error={type, message}`) |
| `SignalReceived` | task description before Layer 1 (`user_input`), task output after Layer 2 (`agent_output`) | — |
| `ActivityStarted` | before each governed task | Layer 1 |
| `ActivityCompleted` | after each governed task | Layer 2 |
| `Handoff` | a delegated task starts on a DID-signed agent | — |
| Hook payload | HTTP / DB / file / function operations | Layer 3 (`hook_trigger=true`, carries `spans`) |

Common payload fields: `source: "workflow-telemetry"`, `timestamp`, `workflow_id` (agent session), `run_id`, `workflow_type` (`"<role> Agent"`), `task_queue` (crew name), `agent_role`, `multi_agent_session_id`, `metadata.{crew_name, crew_execution_id}`, and `flow_execution_id` inside a wrapped flow. Absent fields are omitted, never sent as `null`.

## What's instrumented

| Surface | Default | Libraries |
| --- | --- | --- |
| HTTP | `instrument_http=True` | `httpx` (sync + async, including streamed bodies), `requests`, `urllib3`, `urllib` |
| Databases | `instrument_databases=True` | SQLAlchemy, DB-API drivers (`psycopg2`, `mysql-connector-python`, `pymysql`, `sqlite3`), `asyncpg`, `redis`, `pymongo` — install with `pip install "openbox-crewai-sdk-python[db]"` |
| File I/O | `instrument_file_io=False` | patch on `open()` |
| Functions | always available | `@governed` decorator (`from openbox import governed`) |
| LLM gate | `llm_level_governance=True` | CrewAI before-LLM-call hook (no spans, blocks after a Layer 3 stop) |

Restrict DB instrumentation with `db_libraries={"sqlalchemy", "psycopg2"}`. Request/response bodies are truncated to `max_body_size` (65 536 characters by default); keys listed in `redact_keys` are redacted before sending.

## Identity

- `workflow_id` / `run_id` are UUIDs per agent per kickoff (fresh on every kickoff after cleanup).
- `metadata.crew_execution_id` is a UUID per crew kickoff.
- `flow_execution_id` is present only when running inside a `create_openbox_flow()`-wrapped flow.

## Activity payload shape

`ActivityStarted.activity_input`:

```json
[
  {"description": "..."},
  {"expected_output": "..."}
]
```

`ActivityCompleted.activity_output`:

```json
{"result": "..."}
```

`activity_type` comes from `OpenBoxTask.activity_type` verbatim (no normalization).

## Hook payloads

Layer 3 telemetry is sent as `ActivityStarted` with `hook_trigger: true`, the task's `activity_id`/`activity_type`, and a `spans` array holding one flat span. Each operation produces a `stage="started"` payload before it runs (its verdict can stop it) and a `stage="completed"` payload after (its verdict only affects later work in the task).

Spans are flat: ids are lowercase hex (`span_id` 16 chars, `trace_id` 32 chars), started spans carry explicit `end_time: null` and `duration_ns: null`, and family fields sit at the top level:

| `hook_type` | Fields |
| --- | --- |
| `http_request` | `http_method`, `http_url`, `http_status_code`, `request_headers`, `response_headers`, `request_body`, `response_body` |
| `db_query` | `db_system`, `db_name`, `db_operation`, `db_statement`, `server_address`, `server_port`, `rowcount` |
| `file_operation` | `file_path`, `file_mode`, `file_operation`, `bytes_read`, `bytes_written` |
| `function_call` | `function`, `module`, `args`, `result` |

`semantic_type` is computed by Core, not sent by the SDK.

OPA receives span attributes verbatim but not the top-level `db_*` fields, so DB policies should match against attributes:

```rego
result := {"decision": "BLOCK", "reason": "..."} if {
    some span in input.spans
    span.hook_type == "db_query"
    span.stage == "started"
    upper(object.get(span.attributes, "db.operation", "")) in {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE"}
}
```

### Ignored URLs

The engine's own `api_url` is never instrumented. The SDK doesn't trace its own API traffic.

## Sequences

Single task:

```
WorkflowStarted -> SignalReceived(user_input) -> ActivityStarted -> hook payloads
  -> ActivityCompleted -> SignalReceived(agent_output) -> WorkflowCompleted
```

Halted (HALT at Layer 1):

```
WorkflowStarted -> ActivityStarted (verdict=HALT -> raise GovernanceHaltError)
remaining tasks on that agent short-circuit; Core has already closed the session
```

## Policy guidance

- govern `ActivityStarted`/`ActivityCompleted` for business decisions
- treat hook payloads as internal telemetry by default — gating them as if they were business actions creates duplicate approvals and noisy timelines
