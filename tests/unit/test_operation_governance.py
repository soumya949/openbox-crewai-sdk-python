"""Layer 3: HTTP/function operations inside a governed task go through openbox_core."""

from __future__ import annotations

import pytest
import requests
from openbox_core.conformance.fake_core import assert_hook_wire_shape

from openbox import GovernanceBlockedError, GovernanceHaltError, governed

from .conftest import RESEARCHER_KEY, WRITER_KEY, bind, make_agent, make_task


def _is_hook(payload: dict) -> bool:
    return bool(payload.get("hook_trigger"))


def test_http_call_is_governed_with_flat_wire_shape(core, make_engine, task_body, server) -> None:
    agent = make_agent()
    task_body(lambda a, t: requests.get(server.url, timeout=5).text)
    bind(make_engine(), agent)

    agent.execute_task(make_task(agent))

    assert server.hits == 1
    assert len(core.started_payloads) == 1 and len(core.completed_payloads) == 1
    for payload in core.started_payloads + core.completed_payloads:
        assert_hook_wire_shape(payload)
    started = core.started_payloads[0]
    activity = core.events("ActivityStarted")[0]
    assert started["workflow_id"] == activity["workflow_id"]
    assert started["activity_id"] == activity["activity_id"]
    assert started["agent_role"] == "Researcher"
    assert started["spans"][0]["http_url"] == server.url


def test_operations_are_sent_with_the_acting_agents_key(
    core, make_engine, task_body, server
) -> None:
    researcher = make_agent()
    writer = make_agent("Writer", "OPENBOX_WRITER")
    task_body(lambda a, t: requests.get(server.url, timeout=5).text)
    bind(make_engine(), researcher, writer)

    researcher.execute_task(make_task(researcher))
    writer.execute_task(make_task(writer))

    assert core.keys_for(lambda p: _is_hook(p) and p["agent_role"] == "Researcher") == [
        RESEARCHER_KEY,
        RESEARCHER_KEY,
    ]
    assert core.keys_for(lambda p: _is_hook(p) and p["agent_role"] == "Writer") == [
        WRITER_KEY,
        WRITER_KEY,
    ]


@pytest.mark.parametrize(
    ("verdict", "error"), [("block", GovernanceBlockedError), ("halt", GovernanceHaltError)]
)
def test_stop_verdict_prevents_request(
    core, make_engine, task_body, server, verdict, error
) -> None:
    agent = make_agent()
    task_body(lambda a, t: requests.get(server.url, timeout=5).text)
    bind(make_engine(), agent)
    core.queue = [{}, {}, {}, {"verdict": verdict, "reason": "egress denied"}]

    with pytest.raises(error, match="egress denied"):
        agent.execute_task(make_task(agent))
    assert server.hits == 0
    assert agent._halted is (verdict == "halt")


def test_swallowed_block_still_surfaces_at_task_boundary(
    core, make_engine, task_body, server
) -> None:
    """CrewAI tool paths can swallow the error; the task must still fail."""

    def body(agent, task):
        try:
            requests.get(server.url, timeout=5)
        except Exception as exc:  # what a tool wrapper would do
            return f"tool error: {exc}"
        return "ok"

    agent = make_agent()
    task_body(body)
    bind(make_engine(), agent)
    core.queue = [{}, {}, {}, {"verdict": "block", "reason": "egress denied"}]

    with pytest.raises(GovernanceBlockedError, match="egress denied"):
        agent.execute_task(make_task(agent))
    assert server.hits == 0
    # The block is scoped to that task: the next one runs.
    core.queue = []
    assert agent.execute_task(make_task(agent)) == "ok"


def test_fail_closed_stops_operation_when_core_unreachable(
    core, make_engine, task_body, server
) -> None:
    agent = make_agent()
    task_body(lambda a, t: requests.get(server.url, timeout=5).text)
    bind(make_engine(governance_policy="fail_closed"), agent)
    core.status_overrides = [200, 200, 200, 503]

    with pytest.raises(GovernanceHaltError, match="failed closed"):
        agent.execute_task(make_task(agent))
    assert server.hits == 0


def test_fail_open_lets_operation_run_when_core_unreachable(
    core, make_engine, task_body, server
) -> None:
    agent = make_agent()
    task_body(lambda a, t: requests.get(server.url, timeout=5).text)
    bind(make_engine(governance_policy="fail_open"), agent)
    core.status_overrides = [200, 200, 200, 503, 503]

    agent.execute_task(make_task(agent))
    assert server.hits == 1


def test_operation_approval_is_polled_for_the_task(core, make_engine, task_body, server) -> None:
    agent = make_agent()
    task_body(lambda a, t: requests.get(server.url, timeout=5).text)
    bind(make_engine(), agent)
    core.queue = [{}, {}, {}, {"verdict": "require_approval"}, {"action": "allow"}]

    agent.execute_task(make_task(agent))
    assert server.hits == 1
    activity = core.events("ActivityStarted")[0]
    assert core.approval_requests == [
        {
            "workflow_id": activity["workflow_id"],
            "run_id": activity["run_id"],
            "activity_id": activity["activity_id"],
        }
    ]


def test_completed_stop_blocks_later_llm_calls(core, make_engine, task_body, server) -> None:
    from openbox.errors import raise_for_stop

    calls: list[str] = []

    def body(agent, task):
        requests.get(server.url, timeout=5)
        stop = agent._engine.active_stop()
        if stop is not None:
            calls.append("gated")
            raise_for_stop(stop, subject="LLM call")
        return "unreachable"

    agent = make_agent()
    task_body(body)
    bind(make_engine(), agent)
    core.queue = [{}, {}, {}, {}, {"verdict": "halt", "reason": "post-check"}]

    with pytest.raises(GovernanceHaltError, match="post-check"):
        agent.execute_task(make_task(agent))
    assert server.hits == 1  # completed verdicts never undo the operation
    assert calls == ["gated"]


def test_governed_function_decorator(core, make_engine, task_body) -> None:
    @governed(name="lookup")
    def lookup(x: int) -> int:
        return x * 2

    agent = make_agent()
    task_body(lambda a, t: str(lookup(21)))
    bind(make_engine(), agent)

    assert agent.execute_task(make_task(agent)) == "42"
    functions = [p for p in core.started_payloads if p["spans"][0]["hook_type"] == "function_call"]
    assert len(functions) == 1
    assert_hook_wire_shape(functions[0])


def test_operations_outside_tasks_are_not_governed(core, make_engine, server) -> None:
    agent = make_agent()
    bind(make_engine(), agent)
    requests.get(server.url, timeout=5)
    assert server.hits == 1
    assert core.payloads == []
