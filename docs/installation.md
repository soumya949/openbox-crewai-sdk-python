# Installation

## Install

```bash
pip install openbox-crewai-sdk-python
# or
uv add openbox-crewai-sdk-python

# with database instrumentation (SQLAlchemy, DB-API, asyncpg, redis, pymongo)
pip install "openbox-crewai-sdk-python[db]"
```

Requires Python 3.11 or newer. The governance core ships as the `openbox-sdk-python` dependency (imported as `openbox_core`).

## Environment variables

```bash
export OPENBOX_URL="https://your-openbox-core.example"

# Per agent — env_prefix on OpenBoxAgent drives the lookup
export OPENBOX_RESEARCHER_API_KEY="obx_live_your_key"
export OPENBOX_RESEARCHER_DID="did:aip:<uuid>"              # OpenBox DID signing
export OPENBOX_RESEARCHER_PRIVATE_KEY="base64_ed25519_seed" # paired with DID
```

Okta AI Agent and Keycloak workload identities are configured under the same prefix — see [configuration.md](./configuration.md#agent-identity).

Validation at `engine.govern(crew)`:

- API key matches `obx_live_*` or `obx_test_*`
- non-localhost URLs use HTTPS
- exactly one identity method per agent; DID and private key configured together
- API key (and signature, when an identity is configured) round-tripped against Core's auth-validate endpoint

A bad value raises `OpenBoxConfigError`, `OpenBoxAuthError`, or `OpenBoxNetworkError`.

## Minimal startup

```python
from crewai import Crew, Process
from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

with create_openbox_engine() as engine:
    agent = OpenBoxAgent(
        role="Researcher",
        goal="Find information",
        backstory="You are a research analyst.",
        env_prefix="OPENBOX_RESEARCHER",
    )
    task = OpenBoxTask(
        description="Research RAG techniques.",
        expected_output="A 200-word summary.",
        agent=agent,
        activity_type="research",
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential)
    governed = engine.govern(crew)
    print(governed.kickoff())
```

The context manager closes the engine on exit, flushing pending telemetry and tearing down instrumentation and HTTP clients.

## Local development

Run a local OpenBox Core. Use `obx_test_*` keys with `OPENBOX_URL=http://localhost:8086` — the localhost host check permits HTTP.
