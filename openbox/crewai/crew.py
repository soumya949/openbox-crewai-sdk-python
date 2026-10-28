"""GovernedCrew — CrewAI Crew with governance lifecycle management."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from crewai import Crew
from crewai.hooks.llm_hooks import (
    register_before_llm_call_hook,
    unregister_before_llm_call_hook,
)
from pydantic import PrivateAttr

from openbox._context import multi_agent_session_id_var
from openbox.crewai.agent import OpenBoxAgent
from openbox.engine import OpenBoxEngine
from openbox.errors import GOVERNANCE_ERRORS, OpenBoxConfigError, raise_for_stop

logger = logging.getLogger("openbox")

_LEFTOVER_SESSION = "Previous kickoff ended without closing this session"


def _register_passthrough_exceptions() -> None:
    """Register governance exceptions so CrewAI doesn't retry them."""
    import crewai.agent.core as _core

    existing = _core._passthrough_exceptions
    missing = tuple(e for e in GOVERNANCE_ERRORS if e not in existing)
    if missing:
        _core._passthrough_exceptions = existing + missing


def _make_llm_hook(engine: OpenBoxEngine) -> Any:
    def llm_hook(context: Any) -> bool:
        stop = engine.active_stop()
        if stop is not None:
            raise_for_stop(stop, subject="LLM call")
        return True

    return llm_hook


class GovernedCrew(Crew):
    _governance_engine: OpenBoxEngine | None = PrivateAttr(default=None)
    _crew_execution_id: str = PrivateAttr(default="")
    _llm_hook: Any = PrivateAttr(default=None)

    def bind_openbox_engine(self, engine: OpenBoxEngine) -> GovernedCrew:
        governed_agents = self._governed_agents()
        if not governed_agents:
            raise OpenBoxConfigError(
                "At least one agent must be an OpenBoxAgent. "
                "Plain Agent instances are not governed."
            )
        engine.validate_agent_api_keys(governed_agents)
        engine.ensure_started()
        self._governance_engine = engine
        return self

    def _governed_agents(self) -> list[OpenBoxAgent]:
        agents: list[OpenBoxAgent] = [a for a in self.agents if isinstance(a, OpenBoxAgent)]
        manager = getattr(self, "manager_agent", None)
        if isinstance(manager, OpenBoxAgent) and manager not in agents:
            agents.append(manager)
        return agents

    def _create_manager_agent(self) -> None:
        """Equip hierarchical-process managers with HALT-propagating delegation tools.

        Stock crewai.Crew._create_manager_agent instantiates AgentTools(...).tools()
        inline for the auto-created manager, bypassing OpenBoxAgent.get_delegation_tools
        and letting HALT raised by a worker get swallowed by the stock DelegateWorkTool.
        """
        from types import MethodType

        from crewai import Agent as CrewAIAgent
        from crewai.utilities.i18n import get_i18n
        from crewai.utilities.llm_utils import create_llm

        from openbox.crewai.delegation_tools import (
            make_openbox_delegation_tools,
            openbox_manager_get_delegation_tools,
        )

        if self.manager_agent is not None:
            super()._create_manager_agent()
            manager = self.manager_agent
            if isinstance(manager, OpenBoxAgent):
                return
            current = manager.get_delegation_tools
            if (
                hasattr(current, "__func__")
                and current.__func__ is openbox_manager_get_delegation_tools
            ):
                return
            if type(manager).get_delegation_tools is not CrewAIAgent.get_delegation_tools:
                logger.warning(
                    "manager_agent (%s) overrides get_delegation_tools; HALT "
                    "propagation through delegation may not work. Use OpenBoxAgent "
                    "or subclass OpenBoxDelegateWorkTool/OpenBoxAskQuestionTool "
                    "in your override.",
                    type(manager).__name__,
                )
                return
            object.__setattr__(
                manager,
                "get_delegation_tools",
                MethodType(openbox_manager_get_delegation_tools, manager),
            )
            return

        self.manager_llm = create_llm(self.manager_llm)
        i18n = get_i18n(prompt_file=self.prompt_file)
        manager = CrewAIAgent(
            role=i18n.retrieve("hierarchical_manager_agent", "role"),
            goal=i18n.retrieve("hierarchical_manager_agent", "goal"),
            backstory=i18n.retrieve("hierarchical_manager_agent", "backstory"),
            tools=make_openbox_delegation_tools(self.agents, i18n=i18n),
            allow_delegation=True,
            llm=self.manager_llm,
            verbose=self.verbose,
        )
        self.manager_agent = manager
        manager.crew = self

    def _bound_engine(self, engine: OpenBoxEngine | None) -> OpenBoxEngine | None:
        if engine is not None:
            self.bind_openbox_engine(engine)
        if self._governed_agents() and self._governance_engine is None:
            raise OpenBoxConfigError(
                "This governed crew has not been bound to an OpenBoxEngine. Use "
                "engine.govern(crew) before kickoff()."
            )
        return self._governance_engine

    @contextmanager
    def _kickoff_scope(self, engine: OpenBoxEngine) -> Iterator[None]:
        self._crew_execution_id = str(uuid.uuid4())
        crew_name = self.name or "crew"

        # Outer wins: if a Flow above us already opened a session, propagate.
        # Otherwise this Crew opens its own per-kickoff session.
        session_token = None
        if multi_agent_session_id_var.get() is None:
            session_token = multi_agent_session_id_var.set(str(uuid.uuid4()))

        try:
            for agent in self._governed_agents():
                agent.configure_governance(engine, crew_name, self._crew_execution_id)
            _register_passthrough_exceptions()
            if engine.config.llm_level_governance:
                self._llm_hook = _make_llm_hook(engine)
                register_before_llm_call_hook(self._llm_hook)
            yield
        finally:
            if session_token is not None:
                multi_agent_session_id_var.reset(session_token)

    def _release(self) -> None:
        if self._llm_hook:
            unregister_before_llm_call_hook(self._llm_hook)
            self._llm_hook = None
        for agent in self._governed_agents():
            agent._reset_run_state()

    def kickoff(
        self,
        engine: OpenBoxEngine | None = None,
        inputs: dict[str, Any] | None = None,
        input_files: dict[str, Any] | None = None,
    ) -> Any:
        bound = self._bound_engine(engine)
        if bound is None:
            return super().kickoff(inputs=inputs, input_files=input_files)

        self._drain_leftover_sessions()
        with self._kickoff_scope(bound):
            error: BaseException | None = None
            try:
                return super().kickoff(inputs=inputs, input_files=input_files)
            except BaseException as exc:
                error = exc
                raise
            finally:
                try:
                    self._cleanup(error)
                finally:
                    self._release()

    async def akickoff(
        self,
        engine: OpenBoxEngine | None = None,
        inputs: dict[str, Any] | None = None,
        input_files: dict[str, Any] | None = None,
    ) -> Any:
        bound = self._bound_engine(engine)
        if bound is None:
            return await super().akickoff(inputs=inputs, input_files=input_files)

        await self._adrain_leftover_sessions()
        with self._kickoff_scope(bound):
            error: BaseException | None = None
            try:
                return await super().akickoff(inputs=inputs, input_files=input_files)
            except BaseException as exc:
                error = exc
                raise
            finally:
                try:
                    await self._acleanup(error)
                finally:
                    self._release()

    def _drain_leftover_sessions(self) -> None:
        """Close any session left open by a prior crashed run, then reset state.

        Why: a prior kickoff that exits via os._exit, signal, or thread death
        skips the finally block, so cleanup never runs. Without this, the
        leftover session is reported with the new run's metadata and the next
        ensure_session() short-circuits. force=True so halted leftovers still
        push a terminal event — Core never saw the halt verdict.
        """
        for agent in self._governed_agents():
            if agent._session_started:
                try:
                    agent.close_session(error=_LEFTOVER_SESSION, force=True)
                except Exception:
                    logger.exception("Error closing leftover session for agent %r", agent.role)
            agent._reset_run_state()

    async def _adrain_leftover_sessions(self) -> None:
        for agent in self._governed_agents():
            if agent._session_started:
                try:
                    await agent.aclose_session(error=_LEFTOVER_SESSION, force=True)
                except Exception:
                    logger.exception("Error closing leftover session for agent %r", agent.role)
            agent._reset_run_state()

    def _cleanup(self, error: BaseException | None) -> None:
        for agent in self._governed_agents():
            try:
                agent.close_session(error=error)
            except Exception:
                logger.exception("Error closing session for agent %r", agent.role)

    async def _acleanup(self, error: BaseException | None) -> None:
        for agent in self._governed_agents():
            try:
                await agent.aclose_session(error=error)
            except Exception:
                logger.exception("Error closing session for agent %r", agent.role)
