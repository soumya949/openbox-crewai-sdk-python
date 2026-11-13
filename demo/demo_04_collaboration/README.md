# Demo 04 — Collaboration

Three governed agents with `allow_delegation=True`.

The Content Writer owns the task and delegates research to the Research Specialist and editorial review to the Content Editor. Every `execute_task` call — including delegated sub-tasks routed through CrewAI's `DelegateWorkTool` — is intercepted by OpenBox governance and sent with its agent's own credentials.

## Layout

Standard `crewai create crew` layout:

- `src/demo_04_collaboration/crew.py` — `CollaborationCrew` with three `OpenBoxAgent` definitions (`researcher`, `writer`, `editor`) and one `OpenBoxTask` owned by the writer.
- `src/demo_04_collaboration/main.py` — kicks off the governed crew.
- `src/demo_04_collaboration/config/{agents,tasks}.yaml` — agent and task definitions.

## Prerequisites

1. In the OpenBox Platform UI, create three agents — `researcher`, `writer`, and `editor`. For each, save the API key and the workload private key shown when the agent is created (IAM v3 workload identity; the private key is shown once).
2. Copy `.env.example` to `.env` and fill in the three sets of credentials plus your `OPENAI_API_KEY`.

## Run

```bash
uv sync
crewai run
```

Or directly:

```bash
uv run demo_04_collaboration
```
