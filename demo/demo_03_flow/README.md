# Demo 03 — Flow

Governed CrewAI **Flow** with 2 crews and 3 governed agents:

- **Research Crew** — `senior_researcher` + `data_analyst`
- **Writing Crew** — `technical_writer`

The Flow orchestrates them via `@start` / `@listen`. Each crew is wrapped with `engine.govern(...)`, so every governance event from every agent is sent with that agent's own credentials.

## Layout

Standard `crewai create flow` layout:

- `src/demo_03_flow/main.py` — `ResearchWritingFlow` (sync) + `AsyncResearchWritingFlow` (async) plus `kickoff()` / `kickoff_async()` / `plot()` entry points.
- `src/demo_03_flow/crews/research_crew/research_crew.py` — `ResearchCrew` class.
- `src/demo_03_flow/crews/research_crew/config/{agents,tasks}.yaml` — research-crew config.
- `src/demo_03_flow/crews/writing_crew/writing_crew.py` — `WritingCrew` class.
- `src/demo_03_flow/crews/writing_crew/config/{agents,tasks}.yaml` — writing-crew config.

## Prerequisites

1. In the OpenBox Platform UI, create three agents — `senior_researcher`, `data_analyst`, and `technical_writer`. For each, save the API key and the workload private key shown when the agent is created (IAM v3 workload identity; the private key is shown once).
2. Copy `.env.example` to `.env` and fill in the three sets of credentials plus your `OPENAI_API_KEY`.

## Run

Sync flow (default):

```bash
uv sync
crewai run
```

Async flow:

```bash
uv run kickoff_async
```

Generate the flow diagram:

```bash
uv run plot
```
