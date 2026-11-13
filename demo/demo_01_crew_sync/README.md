# Demo 01 — Crew Sync

Simplest OpenBox demo. One governed agent (`researcher`) runs a single task. The crew is wrapped with `engine.govern(...)` so every `ActivityStarted` / `ActivityCompleted` / operation event is sent to OpenBox with the agent's own credentials.

## Layout

This demo follows the standard `crewai create crew` layout, with OpenBox-specific changes:

- `src/demo_01_crew_sync/crew.py` — `ResearchCrew` class. Uses `OpenBoxAgent` and `OpenBoxTask` instead of the plain `Agent` / `Task` classes.
- `src/demo_01_crew_sync/main.py` — entry point. Wraps the crew in an `OpenBoxEngine` context.
- `src/demo_01_crew_sync/config/{agents,tasks}.yaml` — agent and task definitions.

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
uv run demo_01_crew_sync
```
