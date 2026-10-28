# Configuration

## `create_openbox_engine()`

| Option | Default | Purpose |
| --- | --- | --- |
| `api_url` | `OPENBOX_URL` env | OpenBox Core base URL |
| `governance_timeout` | `30.0` | HTTP timeout (s) |
| `governance_policy` | `"fail_open"` | API outage policy: `fail_open` or `fail_closed` |
| `on_fallback` | `"log_warning"` | Core `fallback_used=true` policy: `log_warning` or `fail_closed` |
| `send_task_start_event` | `True` | emit `ActivityStarted` (Layer 1) |
| `send_task_completed_event` | `True` | emit `ActivityCompleted` (Layer 2) |
| `llm_level_governance` | `True` | gate LLM calls after a Layer 3 stop |
| `hitl_enabled` | `True` | poll for approval on `REQUIRE_APPROVAL` |
| `hitl_poll_interval` | `5.0` | poll interval (s) |
| `hitl_max_wait` | `None` | give up waiting after this many seconds (`ApprovalTimeoutError`); `None` waits until Core decides or the approval expires |
| `exclude_crews_hitl` | `None` | crew names to skip HITL polling for |
| `instrument_http` | `True` | enable HTTP instrumentation |
| `instrument_databases` | `True` | enable DB instrumentation |
| `db_libraries` | `None` (all) | restrict DB instrumentation, e.g. `{"sqlalchemy", "psycopg2"}` |
| `instrument_file_io` | `False` | enable file I/O instrumentation |
| `max_body_size` | `65536` | truncate captured request/response bodies to this many characters |
| `redact_keys` | `None` | attribute/body keys whose values are redacted before sending |
| `debug_log` | `False` | per-agent trace logging to `openbox_logs/` |

## Environment variables

| Name | Required | Purpose |
| --- | --- | --- |
| `OPENBOX_URL` | yes (or `api_url`) | Core URL |
| `{PREFIX}_API_KEY` | yes per agent | per-agent OpenBox API key (`obx_live_*` / `obx_test_*`) |

`{PREFIX}` is each agent's `env_prefix`. Only variables under that prefix are read for an agent: global `OPENBOX_API_KEY`, `OPENBOX_AGENT_DID` and the like are never shared between agents.

### Agent identity

Each agent can use one identity method. They are mutually exclusive; configure none of them for API-key-only governance.

**OpenBox DID (Ed25519)**

| Name | Purpose |
| --- | --- |
| `{PREFIX}_DID` (or `{PREFIX}_AGENT_DID`) | agent DID, `did:aip:<uuid>` |
| `{PREFIX}_PRIVATE_KEY` (or `{PREFIX}_AGENT_PRIVATE_KEY`) | base64 Ed25519 seed |

DID and private key must be configured together.

**Okta AI Agent**

| Name | Purpose |
| --- | --- |
| `{PREFIX}_OKTA_AGENT_PRIVATE_KEY` | PKCS#8 PEM RS256 key. On its own, the remaining identity metadata is fetched from Core at startup (`/api/v2/auth/bootstrap`). |
| `{PREFIX}_OKTA_AGENT_ID`, `{PREFIX}_OKTA_AGENT_KEY_ID`, `{PREFIX}_AGENT_ID`, `{PREFIX}_ORGANIZATION_ID`, `{PREFIX}_DEPLOYMENT_ID`, `{PREFIX}_AGENT_PROOF_AUDIENCE` | explicit metadata; set all of them or none |
| `{PREFIX}_AGENT_IDENTITY_METHOD` | optional explicit override: `openbox_did` or `okta_ai_agent` |

**Keycloak workload identity**

| Name | Purpose |
| --- | --- |
| `{PREFIX}_WORKLOAD_PRIVATE_KEY` | service-account key used to obtain a workload token for Core's v3 API |

## API failure policy

`governance_policy`:

- `fail_open` — network error, 5xx, or unparseable response → `verdict=ALLOW, fallback_used=true`, execution continues
- `fail_closed` — the same failures raise `GovernanceAPIError` at task boundaries and stop Layer 3 operations with `GovernanceHaltError`

`401`/`403` responses always fail closed with `OpenBoxAuthError` (or `OpenBoxSigningError` for a rejected signature), whatever `governance_policy` says.

`on_fallback` (when Core itself returns `fallback_used=true`):

- `log_warning` — accept the verdict
- `fail_closed` — override to `BLOCK`

## Approvals

When the verdict is `REQUIRE_APPROVAL`:

- `hitl_enabled=True` → the SDK polls the approval endpoint at `hitl_poll_interval` until Core decides
- `hitl_enabled=False` or crew in `exclude_crews_hitl` → `on_fallback` decides: `log_warning` continues, `fail_closed` raises `GovernanceBlockedError`

Polling ends when Core returns `allow`/`block`/`halt`, the approval expires (`GovernanceApprovalExpiredError`), `hitl_max_wait` runs out, or Core stays unreachable for 60 polls in a row (`ApprovalTimeoutError`).
