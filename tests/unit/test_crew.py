"""GovernedCrew kickoff lifecycle, sessions and flow correlation."""

from __future__ import annotations

import uuid

import pytest
from crewai import Crew, Process

from openbox import GovernedCrew, OpenBoxConfigError, create_openbox_flow
from openbox._context import flow_execution_id_var, multi_agent_session_id_var
from openbox.errors import GOVERNANCE_ERRORS

from .conftest import CREW_NAME, make_agent, make_task


def _crew(*agents, tasks=None) -> Crew:
    return Crew(
        name=CREW_NAME,
        agents=list(agents),
        tasks=tasks if tasks is not None else [make_task(a) for a in agents],
        process=Process.sequential,
    )


def test_kickoff_opens_and_closes_one_session_per_agent(core, make_engine, task_body) -> None:
    task_body(lambda a, t: "ok")
    researcher, writer = make_agent(), make_agent("Writer", "OPENBOX_WRITER")
    engine = make_engine()
    engine.govern(_crew(researcher, writer)).kickoff()

    started = core.events("WorkflowStarted")
    completed = core.events("WorkflowCompleted")
    assert len(started) == 2 and len(completed) == 2
    assert {p["workflow_id"] for p in started} == {p["workflow_id"] for p in completed}
    assert {p["status"] for p in completed} == {"completed"}
    sessions = {p["multi_agent_session_id"] for p in core.lifecycle_payloads}
    assert len(sessions) == 1
    crew_runs = {p["metadata"]["crew_execution_id"] for p in core.lifecycle_payloads}
    assert len(crew_runs) == 1
    uuid.UUID(crew_runs.pop())


def test_failure_closes_session_with_workflow_failed(core, make_engine, task_body) -> None:
    def body(agent, task):
        raise RuntimeError("tool exploded")

    task_body(body)
    researcher = make_agent()
    engine = make_engine()
    with pytest.raises(RuntimeError, match="tool exploded"):
        engine.govern(_crew(researcher)).kickoff()

    failed = core.events("WorkflowFailed")
    assert len(failed) == 1
    assert failed[0]["error"] == {"type": "RuntimeError", "message": "tool exploded"}
    assert core.events("WorkflowCompleted") == []
    closed_activity = core.events("ActivityCompleted")[0]
    assert closed_activity["status"] == "failed"
    assert closed_activity["error"] == {"type": "RuntimeError", "message": "tool exploded"}


def test_halted_agent_session_is_not_closed_again(core, make_engine, task_body) -> None:
    task_body(lambda a, t: "ok")
    researcher = make_agent()
    engine = make_engine()
    core.queue = [{}, {}, {"verdict": "halt"}]
    with pytest.raises(Exception, match="halted"):
        engine.govern(_crew(researcher)).kickoff()
    assert core.events("WorkflowCompleted") == [] and core.events("WorkflowFailed") == []


def test_leftover_session_is_drained_before_next_kickoff(core, make_engine, task_body) -> None:
    task_body(lambda a, t: "ok")
    researcher = make_agent()
    engine = make_engine()
    crew = engine.govern(_crew(researcher))

    researcher.configure_governance(engine, CREW_NAME, "crashed-run")
    researcher.ensure_session()  # a session a crashed process never closed
    leftover = researcher._session_id

    crew.kickoff()
    failed = core.events("WorkflowFailed")
    assert [p["workflow_id"] for p in failed] == [leftover]
    assert len(core.events("WorkflowStarted")) == 2


def test_crew_inside_flow_inherits_session_and_flow_ids(core, make_engine, task_body) -> None:
    seen: dict[str, str | None] = {}

    def body(agent, task):
        seen["session"] = multi_agent_session_id_var.get()
        seen["flow"] = flow_execution_id_var.get()
        return "ok"

    task_body(body)
    researcher = make_agent()
    crew = make_engine().govern(_crew(researcher))

    class FakeFlow:
        def kickoff(self, **kwargs):
            return crew.kickoff()

    create_openbox_flow(FakeFlow).kickoff(inputs={"id": "flow-run-7"})

    assert seen["flow"] == "flow-run-7"
    assert seen["session"]
    for payload in core.lifecycle_payloads:
        assert payload["flow_execution_id"] == "flow-run-7"
        assert payload["multi_agent_session_id"] == seen["session"]
    assert multi_agent_session_id_var.get() is None


def test_govern_requires_an_openbox_agent(make_engine) -> None:
    from crewai import Agent, Task

    plain = Agent(role="Plain", goal="g", backstory="b")
    crew = Crew(agents=[plain], tasks=[Task(description="d", expected_output="o", agent=plain)])
    with pytest.raises(OpenBoxConfigError, match="OpenBoxAgent"):
        make_engine().govern(crew)


def test_govern_validates_each_agent_key(core, make_engine) -> None:
    researcher, writer = make_agent(), make_agent("Writer", "OPENBOX_WRITER")
    make_engine().govern(_crew(researcher, writer))
    validated = [
        r.headers["authorization"] for r in core.requests if r.url.path.endswith("/auth/validate")
    ]
    assert sorted(validated) == ["Bearer obx_test_researcher", "Bearer obx_test_writer"]


def test_governance_errors_are_not_retried_by_crewai(core, make_engine, task_body) -> None:
    import crewai.agent.core as crewai_core

    task_body(lambda a, t: "ok")
    make_engine().govern(_crew(make_agent())).kickoff()
    assert set(GOVERNANCE_ERRORS) <= set(crewai_core._passthrough_exceptions)


def test_instrumentation_settings_cannot_change_after_start(make_engine) -> None:
    engine = make_engine()
    engine.ensure_started()
    engine.config.instrument_file_io = True
    with pytest.raises(OpenBoxConfigError, match="changed after startup"):
        engine.ensure_started()


async def test_async_kickoff(core, make_engine, task_body) -> None:
    task_body(lambda a, t: "ok")
    crew = make_engine().govern(_crew(make_agent()))
    await crew.akickoff()
    assert len(core.events("WorkflowStarted")) == 1
    assert len(core.events("WorkflowCompleted")) == 1


def test_governed_crew_type(make_engine) -> None:
    crew = make_engine().govern(_crew(make_agent()))
    assert isinstance(crew, GovernedCrew)
