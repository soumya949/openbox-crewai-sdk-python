# Security and Privacy

## Transport

`OPENBOX_URL` must be `https://` for non-localhost hosts. `http://` is accepted only for `localhost`, `127.0.0.1`, `[::1]`. Anything else raises `OpenBoxInsecureURLError`.

## API key validation

Per-agent keys must match `obx_live_*` or `obx_test_*`. `engine.govern(crew)` round-trips each unique key against the auth-validate endpoint; failure raises `OpenBoxAuthError`. A `401`/`403` on any later request also raises, even under `governance_policy="fail_open"` — a revoked key never turns into a fallback allow.

## Request signing

Signing is implemented by `openbox_core`; this SDK never handles signatures itself. Each agent signs with the identity configured under its own `env_prefix` (see [configuration.md](./configuration.md#agent-identity)).

**OpenBox DID** — every request carries:

- `X-OpenBox-Agent-DID`
- `X-OpenBox-Agent-Timestamp`
- `X-OpenBox-Agent-Nonce`
- `X-OpenBox-Body-SHA256`
- `X-OpenBox-Agent-Signature` — Ed25519 signature over method, path, timestamp, nonce and body hash

**Okta AI Agent** — every request carries an RS256 JWT in `X-OpenBox-Agent-Assertion`, bound to the method, URL, body hash and a SHA-256 of the API key. In bootstrap mode the SDK checks that the key's JWK thumbprint matches the one Core registered before sending anything.

**Keycloak workload identity** — every request carries a short-lived workload token in `X-OpenBox-Workload-Token`, refreshed before it expires.

When Core rejects a signature, the SDK raises `OpenBoxSigningError` with a `reason_code` (for example `signature_invalid`, `nonce_replayed`, `timestamp_outside_window`).

Store private keys in your runtime secret store. Don't commit them. Don't reuse one agent's key for another.

## Per-agent identity

Distinct agents in a single crew have distinct identities — each `OpenBoxAgent` resolves credentials only from its own `env_prefix`, and HTTP/DB/file operations are sent with the credentials of the agent running the task. Global `OPENBOX_*` credentials are never shared between agents.

## Body capture

HTTP request and response bodies (text content types only) are captured by `openbox_core` and merged into governance payloads, truncated to `max_body_size`. Values under `redact_keys` are redacted before the payload is signed and sent. Sensitive headers (`Authorization`, `Cookie`, API-key headers) are always redacted.

## File capture

Disabled by default. When enabled, interpreter-owned paths (the Python installation, `site-packages`, `__pycache__`) are skipped.

## API failure policy

`governance_policy` and `on_fallback` together determine what happens when Core is unreachable or returns a degraded verdict. See [configuration.md](./configuration.md#api-failure-policy).

## Debug logging

`debug_log=True` writes per-agent trace records (evaluate payloads, response verdicts, approval polling) to `openbox_logs/agent_<key suffix>.log`. Useful for development and incident response. Review captured fields for sensitive data before enabling broadly.
