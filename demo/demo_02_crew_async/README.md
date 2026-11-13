# Demo 02 — Crew Async

Same single-agent OpenBox demo as `demo_01_crew_sync`, but driven via `akickoff()` so the crew runs inside an asyncio event loop. Useful for embedding a governed crew inside an async application (e.g. a web handler).

Also demonstrates handling `GovernanceHaltError`, raised when an OpenBox policy/guardrail blocks execution.

## Layout

Standard `crewai create crew` layout, with OpenBox-specific tweaks:

- `src/demo_02_crew_async/crew.py` — `ResearchCrew` using `OpenBoxAgent` and `OpenBoxTask`.
- `src/demo_02_crew_async/main.py` — wraps `governed_crew.akickoff()` in `asyncio.run` so `crewai run` can drive it.
- `src/demo_02_crew_async/config/{agents,tasks}.yaml` — agent and task definitions.

## Prerequisites

1. In the OpenBox Platform UI, create the `researcher` agent. Save the API key and the workload private key shown when the agent is created (IAM v3 workload identity; the private key is shown once).
2. Copy `.env.example` to `.env` and fill in those values plus your `OPENAI_API_KEY`.

## Run

```bash
uv sync
crewai run
```

Or directly:

```bash
uv run demo_02_crew_async
```
