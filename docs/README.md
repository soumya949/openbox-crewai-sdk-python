# OpenBox CrewAI SDK Documentation

`openbox-crewai-sdk` lets a CrewAI application send governance events to OpenBox Core, enforce verdicts (allow / block / halt / require approval) returned by Core, and capture HTTP, database, and file telemetry.

## Quickstart

```bash
pip install openbox-crewai-sdk-python
export OPENBOX_URL=https://your-openbox-core.example
export OPENBOX_RESEARCHER_API_KEY=obx_live_your_key
export OPENBOX_RESEARCHER_DID=did:aip:550e8400-e29b-41d4-a716-446655440000
export OPENBOX_RESEARCHER_PRIVATE_KEY=base64_ed25519_seed
```

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
        expected_output="A summary.",
        agent=agent,
        activity_type="research",
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
    engine.govern(crew).kickoff()
```

## Governance layers

The SDK evaluates governance at three points around each CrewAI task:

| Layer | When | Event |
| --- | --- | --- |
| 1 | before a task starts | `ActivityStarted` |
| 2 | after a task completes | `ActivityCompleted` |
| 3 | during HTTP / DB / file / function / LLM operations | hook payload |

Other docs reference these layer numbers.

## Per-agent identity

Each `OpenBoxAgent` declares an `env_prefix`. Credentials resolve from environment variables under that prefix. DID signing is enabled when both DID fields are set:

```bash
OPENBOX_RESEARCHER_API_KEY=obx_live_...
OPENBOX_RESEARCHER_DID=did:aip:550e8400-e29b-41d4-a716-446655440000
OPENBOX_RESEARCHER_PRIVATE_KEY=base64_ed25519_seed
```

Distinct agents in one crew can have distinct identities. Okta AI Agent and Keycloak workload identities are also supported under the same prefix — see [configuration.md](./configuration.md#agent-identity). Omit identity variables only if you intend to run without request signing.

## Support matrix

| Component | Requirement |
| --- | --- |
| Python | `>=3.11` |
| openbox-sdk-python | `>=1.5.0` (installed automatically) |
| CrewAI | `>=1.14.1` |
| OpenBox Core | reachable over HTTPS (HTTP allowed for localhost) |

## Reading order

1. [installation.md](./installation.md)
2. [configuration.md](./configuration.md)
3. [events-and-telemetry.md](./events-and-telemetry.md) — what gets sent to OpenBox, including HTTP/DB/file capture
4. [approvals-and-guardrails.md](./approvals-and-guardrails.md) — verdicts, errors, HITL
5. [security-and-privacy.md](./security-and-privacy.md) — transport, request signing
6. [architecture.md](./architecture.md) — runtime model and usage rules
7. [troubleshooting.md](./troubleshooting.md)
8. [api-reference.md](./api-reference.md)
