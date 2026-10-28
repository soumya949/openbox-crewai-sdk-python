"""Layer 1/2: task lifecycle events and verdict handling."""

from __future__ import annotations

import pytest

from openbox import (
    GovernanceApprovalExpiredError,
    GovernanceBlockedError,
    GovernanceHaltError,
    GuardrailsValidationError,
    OpenBoxAuthError,
)
from openbox._context import multi_agent_session_id_var

from .conftest import CREW_NAME, RESEARCHER_KEY, bind, make_agent, make_task


@pytest.fixture
def researcher(make_engine, task_body):
    task_body(lambda agent, task: f"done: {task.description}")
    agent = make_agent()
    return agent


def test_allow_emits_lifecycle_in_order(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    output = researcher.execute_task(make_task(researcher))

    assert output == "done: Find information"
    sequence = [(p["event_type"], p.get("signal_name")) for p in core.lifecycle_payloads]
    assert sequence == [
        ("WorkflowStarted", None),
        ("SignalReceived", "user_prompt"),
        ("ActivityStarted", None),
        ("ActivityCompleted", None),
    ]
    signal = core.events("SignalReceived")[0]
    assert signal["signal_args"] == ["Find information"]
    assert signal["activity_id"] == f"{signal['run_id']}-sig"
    assert signal["activity_type"] == "user_prompt"
    # Session events share the run's activity, titled with the agent (as Agentforce does).
    workflow = core.events("WorkflowStarted")[0]
    assert workflow["activity_id"] == f"{workflow['run_id']}-wf"
    assert workflow["activity_type"] == "Researcher Agent"
    assert workflow["goal"] == "Researcher goal"
    assert workflow["activity_input"] == [
        {"agent_role": "Researcher", "crew_name": CREW_NAME, "crew_execution_id": "crew-exec-1"}
    ]
    assert all(p["hook_trigger"] is False for p in core.lifecycle_payloads)
    started = core.events("ActivityStarted")[0]
    assert started["source"] == "workflow-telemetry"
    assert started["workflow_type"] == "Researcher Agent"
    assert started["task_queue"] == CREW_NAME
    assert started["agent_role"] == "Researcher"
    assert started["activity_type"] == "research"
    assert started["metadata"] == {"crew_name": CREW_NAME, "crew_execution_id": "crew-exec-1"}
    assert started["activity_input"] == [
        {"description": "Find information"},
        {"expected_output": "A summary"},
    ]
    completed = core.events("ActivityCompleted")[0]
    assert completed["activity_output"] == {"result": "done: Find information"}
    assert completed["status"] == "completed"
    assert isinstance(completed["duration_ms"], int)
    assert completed["activity_id"] == started["activity_id"]


def test_session_events_are_sequenced(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    researcher.execute_task(make_task(researcher))
    researcher.execute_task(make_task(researcher))
    researcher.close_session()

    events = core.lifecycle_payloads
    assert [p["sequence"] for p in events] == list(range(1, len(events) + 1))
    stamps = [p["timestamp"] for p in events]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    # One prompt signal per session, however many tasks run in it.
    assert len(core.events("SignalReceived")) == 1
    closed = core.events("WorkflowCompleted")[0]
    assert closed["status"] == "completed"
    assert closed["activity_output"] == {"result": "done: Find information"}
    assert closed["workflow_output"] == closed["activity_output"]
    assert closed["activity_id"] == f"{closed['run_id']}-wf"
    assert closed["activity_type"] == "Researcher Agent"
    assert isinstance(closed["duration_ms"], int)


def test_blocked_task_closes_its_activity(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [{}, {}, {"verdict": "block", "reason": "not allowed"}]
    with pytest.raises(GovernanceBlockedError):
        researcher.execute_task(make_task(researcher))

    started = core.events("ActivityStarted")[0]
    closed = core.events("ActivityCompleted")
    assert len(closed) == 1
    assert closed[0]["activity_id"] == started["activity_id"]
    assert closed[0]["status"] == "failed"
    assert closed[0]["error"]["type"] == "GovernanceBlockedError"
    assert "not allowed" in closed[0]["activity_output"]["result"]


def test_blocked_prompt_stops_before_any_task(core, make_engine, task_body) -> None:
    ran: list[str] = []
    task_body(lambda agent, task: ran.append("ran") or "x")
    agent = make_agent()
    bind(make_engine(), agent)
    core.queue = [{}, {"verdict": "block", "reason": "prompt rejected"}]

    with pytest.raises(GovernanceBlockedError, match="prompt rejected"):
        agent.execute_task(make_task(agent))
    assert ran == []
    assert core.events("ActivityStarted") == []
    closure = core.events("ActivityCompleted")[0]
    assert closure["activity_type"] == "user_prompt" and closure["status"] == "failed"


def test_requests_carry_agent_key_and_crewai_version(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    researcher.execute_task(make_task(researcher))

    evaluate = [r for r in core.requests if r.url.path == "/api/v1/governance/evaluate"]
    assert evaluate
    assert {r.headers["authorization"] for r in evaluate} == {f"Bearer {RESEARCHER_KEY}"}
    assert evaluate[0].headers["x-openbox-sdk-version"] == "openbox-crewai-python-v3.0.0"


def test_session_is_reused_across_tasks(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    researcher.execute_task(make_task(researcher))
    researcher.execute_task(make_task(researcher))

    assert len(core.events("WorkflowStarted")) == 1
    workflow_ids = {p["workflow_id"] for p in core.events("ActivityStarted")}
    assert len(workflow_ids) == 1


def test_block_before_task_prevents_execution(core, make_engine, task_body) -> None:
    ran: list[str] = []
    task_body(lambda agent, task: ran.append("ran") or "x")
    agent = make_agent()
    bind(make_engine(), agent)
    core.queue = [{}, {}, {"verdict": "block", "reason": "not allowed"}]

    with pytest.raises(GovernanceBlockedError, match="not allowed"):
        agent.execute_task(make_task(agent))
    assert ran == []
    assert not agent._halted


def test_halt_refuses_later_tasks(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [{}, {}, {"verdict": "halt", "reason": "kill switch"}]

    with pytest.raises(GovernanceHaltError, match="kill switch"):
        researcher.execute_task(make_task(researcher))
    with pytest.raises(GovernanceHaltError, match="is halted"):
        researcher.execute_task(make_task(researcher))


def test_failed_guardrails_raise(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [
        {},
        {},
        {
            "verdict": "allow",
            "guardrails_result": {
                "validation_passed": False,
                "input_type": "activity_input",
                "reasons": [{"reason": "PII detected"}],
            },
        },
    ]
    with pytest.raises(GuardrailsValidationError, match="PII detected"):
        researcher.execute_task(make_task(researcher))


def test_input_and_output_redaction(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [
        {},
        {},
        {
            "verdict": "allow",
            "guardrails_result": {
                "input_type": "activity_input",
                "redacted_input": [{"description": "Find [REDACTED]"}],
            },
        },
        {
            "verdict": "allow",
            "guardrails_result": {
                "input_type": "activity_output",
                "redacted_input": {"result": "[REDACTED OUTPUT]"},
            },
        },
    ]
    output = researcher.execute_task(make_task(researcher, "Find secret"))
    assert output == "[REDACTED OUTPUT]"
    assert core.events("ActivityCompleted")[0]["activity_output"] == {
        "result": "done: Find [REDACTED]"
    }


def test_require_approval_polls_until_approved(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [
        {},
        {},
        {"verdict": "require_approval", "reason": "needs review", "governance_event_id": "a-1"},
        {"action": "require_approval", "id": "a-1"},
        {"action": "allow", "id": "a-1"},
    ]
    assert researcher.execute_task(make_task(researcher)).startswith("done")

    started = core.events("ActivityStarted")[0]
    assert len(core.approval_requests) == 2
    assert core.approval_requests[0] == {
        "workflow_id": started["workflow_id"],
        "run_id": started["run_id"],
        "activity_id": started["activity_id"],
    }


def test_decision_for_another_approval_is_rejected(core, make_engine, researcher) -> None:
    """Core answering with an older approval must never approve this one."""
    bind(make_engine(), researcher)
    core.queue = [
        {},
        {},
        {"verdict": "require_approval", "governance_event_id": "start-approval"},
        {"action": "allow", "id": "start-approval"},
        {"verdict": "require_approval", "governance_event_id": "output-approval"},
        {"action": "require_approval", "id": "output-approval"},
        {"action": "allow", "id": "start-approval"},
    ]
    with pytest.raises(GovernanceBlockedError, match="could not be confirmed"):
        researcher.execute_task(make_task(researcher))
    assert len(core.approval_requests) == 3


def test_expired_approval_raises_legacy_alias(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [{}, {}, {"verdict": "require_approval"}, {"expired": True}]
    with pytest.raises(GovernanceApprovalExpiredError):
        researcher.execute_task(make_task(researcher))


def test_rejected_approval_blocks(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [{}, {}, {"verdict": "require_approval"}, {"action": "block", "reason": "no"}]
    with pytest.raises(GovernanceBlockedError, match="Approval rejected"):
        researcher.execute_task(make_task(researcher))


def test_hitl_excluded_crew_follows_on_fallback(core, make_engine, researcher) -> None:
    bind(make_engine(exclude_crews_hitl={CREW_NAME}), researcher)
    core.queue = [{}, {}, {"verdict": "require_approval"}]
    assert researcher.execute_task(make_task(researcher)).startswith("done")
    assert core.approval_requests == []


def test_hitl_disabled_with_fail_closed_fallback_blocks(core, make_engine, researcher) -> None:
    bind(make_engine(hitl_enabled=False, on_fallback="fail_closed"), researcher)
    core.queue = [{}, {}, {"verdict": "require_approval"}]
    with pytest.raises(GovernanceBlockedError, match="HITL is disabled"):
        researcher.execute_task(make_task(researcher))


def test_core_fallback_rejected_by_on_fallback(core, make_engine, researcher) -> None:
    bind(make_engine(on_fallback="fail_closed"), researcher)
    core.queue = [{}, {}, {"verdict": "allow", "fallback_used": True, "reason": "degraded"}]
    with pytest.raises(GovernanceBlockedError, match="fallback rejected"):
        researcher.execute_task(make_task(researcher))


def test_unauthorized_fails_closed_even_when_fail_open(core, make_engine, researcher) -> None:
    bind(make_engine(governance_policy="fail_open"), researcher)
    core.status_overrides = [401]
    with pytest.raises(OpenBoxAuthError):
        researcher.execute_task(make_task(researcher))


def test_core_unreachable_fail_open_continues(core, make_engine, researcher) -> None:
    bind(make_engine(governance_policy="fail_open"), researcher)
    core.status_overrides = [503, 503, 503, 503, 503]
    assert researcher.execute_task(make_task(researcher)).startswith("done")


def test_excluded_crew_skips_task_events(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    researcher._engine.config.exclude_crews = {CREW_NAME}
    researcher.execute_task(make_task(researcher))
    assert core.events("ActivityStarted") == []


def test_handoff_signed_by_receiver_for_did_agents(
    core, make_engine, task_body, monkeypatch
) -> None:
    import base64

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    def seed() -> str:
        raw = Ed25519PrivateKey.generate().private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )
        return base64.b64encode(raw).decode()

    researcher_did = "did:aip:6ba7b810-9dad-51d1-80b4-00c04fd430c8"
    writer_did = "did:aip:6ba7b811-9dad-51d1-80b4-00c04fd430c8"
    monkeypatch.setenv("OPENBOX_RESEARCHER_DID", researcher_did)
    monkeypatch.setenv("OPENBOX_RESEARCHER_PRIVATE_KEY", seed())
    monkeypatch.setenv("OPENBOX_WRITER_DID", writer_did)
    monkeypatch.setenv("OPENBOX_WRITER_PRIVATE_KEY", seed())

    researcher = make_agent()
    writer = make_agent("Writer", "OPENBOX_WRITER")

    def body(agent, task):
        if agent is researcher:
            return writer.execute_task(make_task(writer, "Delegated"))
        return "written"

    task_body(body)
    bind(make_engine(), researcher, writer)
    token = multi_agent_session_id_var.set("mas-1")
    try:
        researcher.execute_task(make_task(researcher))
    finally:
        multi_agent_session_id_var.reset(token)

    handoffs = core.events("Handoff")
    assert handoffs == [
        {
            "source": "workflow-telemetry",
            "event_type": "Handoff",
            "from_agent_did": researcher_did,
            "multi_agent_session_id": "mas-1",
            "timestamp": handoffs[0]["timestamp"],
            "sequence": handoffs[0]["sequence"],
        }
    ]
    signed_by = [
        r.headers.get("x-openbox-agent-did")
        for r in core.requests
        if r.url.path.endswith("/evaluate") and b'"Handoff"' in r.content
    ]
    assert signed_by == [writer_did]


async def test_async_task_lifecycle(core, make_engine, researcher) -> None:
    bind(make_engine(), researcher)
    core.queue = [{}, {}, {"verdict": "require_approval"}, {"action": "allow"}]
    output = await researcher.aexecute_task(make_task(researcher))
    assert output.startswith("done")
    assert [p["event_type"] for p in core.lifecycle_payloads][:4] == [
        "WorkflowStarted",
        "SignalReceived",
        "ActivityStarted",
        "ActivityCompleted",
    ]
    assert len(core.approval_requests) == 1
