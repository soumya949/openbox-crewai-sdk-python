"""Shared fixtures: a fake OpenBox Core behind every agent client."""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from openbox_core.client import EvaluationClient
from openbox_core.conformance.fake_core import FakeCore
from openbox_core.conformance.instrumentation import LocalCountingServer

import openbox._clients as clients_module
from openbox import OpenBoxAgent, OpenBoxEngine, OpenBoxTask, create_openbox_engine

API_URL = "https://core.test"
CREW_NAME = "test_crew"
RESEARCHER_KEY = "obx_test_researcher"
WRITER_KEY = "obx_test_writer"


@pytest.fixture(autouse=True, scope="session")
def _configure_crewai_storage(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    home_dir = tmp_path_factory.mktemp("crewai-home")
    data_home = home_dir / ".local" / "share"
    data_home.mkdir(parents=True, exist_ok=True)
    overrides = {
        "HOME": str(home_dir),
        "XDG_DATA_HOME": str(data_home),
        "CREWAI_STORAGE_DIR": "openbox-crewai-sdk-tests",
        "CREWAI_DISABLE_TELEMETRY": "true",
        "OPENAI_API_KEY": "sk-fake-key-for-tests",
    }
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(autouse=True)
def _agent_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("OPENBOX_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENBOX_RESEARCHER_API_KEY", RESEARCHER_KEY)
    monkeypatch.setenv("OPENBOX_WRITER_API_KEY", WRITER_KEY)


class RecordingCore(FakeCore):
    """FakeCore that also keeps every request (headers included)."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        super().__init__(*responses)
        self.requests: list[httpx.Request] = []
        self.status_overrides: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status_overrides and request.url.path.endswith("/governance/evaluate"):
            self.payloads.append(json.loads(request.content))
            return httpx.Response(self.status_overrides.pop(0), json={})
        return super().handler(request)

    def keys_for(self, predicate: Callable[[dict[str, Any]], bool]) -> list[str]:
        """Bearer keys of evaluate requests whose payload matches ``predicate``."""
        keys = []
        for request in self.requests:
            if not request.url.path.endswith("/governance/evaluate"):
                continue
            if predicate(json.loads(request.content)):
                keys.append(request.headers["authorization"].removeprefix("Bearer "))
        return keys

    def events(self, event_type: str) -> list[dict[str, Any]]:
        return [
            p for p in self.lifecycle_payloads if p.get("event_type") == event_type
        ]


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch) -> RecordingCore:
    fake = RecordingCore()
    transport = httpx.MockTransport(fake.handler)
    monkeypatch.setattr(
        clients_module,
        "EvaluationClient",
        functools.partial(EvaluationClient, transport=transport, async_transport=transport),
    )
    return fake


@pytest.fixture
def make_engine(core: RecordingCore) -> Iterator[Callable[..., OpenBoxEngine]]:
    engines: list[OpenBoxEngine] = []

    def _make(**options: Any) -> OpenBoxEngine:
        options.setdefault("hitl_poll_interval", 0.01)
        engine = create_openbox_engine(api_url=API_URL, **options)
        engines.append(engine)
        return engine

    yield _make
    for engine in engines:
        engine.close()


@pytest.fixture
def server() -> Iterator[LocalCountingServer]:
    local = LocalCountingServer()
    yield local
    local.stop()


def make_agent(role: str = "Researcher", env_prefix: str = "OPENBOX_RESEARCHER") -> OpenBoxAgent:
    return OpenBoxAgent(
        role=role,
        goal=f"{role} goal",
        backstory=f"{role} backstory",
        env_prefix=env_prefix,
    )


def make_task(agent: OpenBoxAgent, description: str = "Find information") -> OpenBoxTask:
    return OpenBoxTask(
        name="research",
        description=description,
        expected_output="A summary",
        agent=agent,
        activity_type="research",
    )


def bind(engine: OpenBoxEngine, *agents: OpenBoxAgent, crew_name: str = CREW_NAME) -> None:
    """What GovernedCrew does before kickoff, without running a crew."""
    engine.validate_agent_api_keys(list(agents))
    engine.ensure_started()
    for agent in agents:
        agent.configure_governance(engine, crew_name, "crew-exec-1")


@pytest.fixture
def task_body(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., str]], None]:
    """Replace CrewAI's own task execution (no LLM) with ``body``."""
    from crewai import Agent

    def _install(body: Callable[..., str]) -> None:
        def execute_task(self: Any, task: Any, context: Any = None, tools: Any = None) -> str:
            return body(self, task)

        async def aexecute_task(
            self: Any, task: Any, context: Any = None, tools: Any = None
        ) -> str:
            return body(self, task)

        monkeypatch.setattr(Agent, "execute_task", execute_task)
        monkeypatch.setattr(Agent, "aexecute_task", aexecute_task)

    return _install
