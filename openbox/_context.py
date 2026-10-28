"""Correlation ids shared across crews, flows and delegated agents."""

from __future__ import annotations

from contextvars import ContextVar

# Per-Flow.kickoff() correlation id — every governance event during a flow run
# carries this so telemetry can stitch crews/agents back to a single Flow run.
flow_execution_id_var: ContextVar[str | None] = ContextVar(
    "openbox_flow_execution_id", default=None
)

# Set by the outermost multi-agent boundary (create_openbox_flow or GovernedCrew.kickoff)
# so every governance event in the run can be correlated by a shared id.
multi_agent_session_id_var: ContextVar[str | None] = ContextVar(
    "openbox_multi_agent_session_id", default=None
)

# Carries the delegating agent's DID across CrewAI's delegate_work_to_coworker
# call so the receiving agent can emit a Handoff event identifying the origin.
handoff_origin_did_var: ContextVar[str | None] = ContextVar(
    "openbox_handoff_origin_did", default=None
)
