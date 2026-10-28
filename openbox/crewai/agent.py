"""OpenBoxAgent — CrewAI Agent with per-agent governance."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from crewai import Agent, Task
from openbox_core.context import activity_scope
from openbox_core.contracts import events
from openbox_core.contracts.context import ActivityContext
from openbox_core.contracts.events import EventEnvelope, rfc3339_from_datetime
from openbox_core.contracts.results import EvaluationResult, Verdict
from openbox_core.errors import GovernanceHaltError as _CoreGovernanceHaltError
from opentelemetry import context as otel_context
from opentelemetry import trace
from pydantic import PrivateAttr

from openbox._context import (
    flow_execution_id_var,
    handoff_origin_did_var,
    multi_agent_session_id_var,
)
from openbox.crewai.task import OpenBoxTask
from openbox.engine import AgentBinding, OpenBoxEngine
from openbox.errors import (
    GOVERNANCE_ERRORS,
    GovernanceHaltError,
    GuardrailsValidationError,
    OpenBoxConfigError,
    raise_for_stop,
)

logger = logging.getLogger("openbox")

PROMPT_SIGNAL = "user_prompt"


def _error_info(error: BaseException | str) -> dict[str, str]:
    if isinstance(error, str):
        return {"type": "Exception", "message": error}
    return {"type": type(error).__name__, "message": str(error)}


def _apply_input_redaction(task: OpenBoxTask, result: EvaluationResult) -> None:
    gr = result.guardrails
    if not gr or gr.input_type != "activity_input" or gr.redacted_input is None:
        return
    redacted = gr.redacted_input
    items = redacted if isinstance(redacted, list) else [redacted]
    for item in items:
        if isinstance(item, dict) and "description" in item:
            task.description = item["description"]
            logger.info("Task input redacted by governance for %r", task.name or "unnamed_task")
            return


def _apply_output_redaction(result: EvaluationResult, output: str) -> str:
    gr = result.guardrails
    if not gr or gr.input_type != "activity_output" or gr.redacted_input is None:
        return output
    logger.info("Task output redacted by governance")
    redacted = gr.redacted_input
    if isinstance(redacted, dict) and "result" in redacted:
        return str(redacted["result"])
    return str(redacted)


def _check_guardrails(result: EvaluationResult) -> None:
    # A failed check with redacted content is applied, not raised.
    gr = result.guardrails
    if gr is None or gr.validation_passed or gr.redacted_input is not None:
        return
    reasons = gr.get_reason_strings()
    if not reasons:
        label = "output " if gr.input_type == "activity_output" else ""
        reasons = [f"Guardrails {label}validation failed"]
    raise GuardrailsValidationError(reasons)


def _log_constraint(result: EvaluationResult, stage: str) -> None:
    if result.verdict is not Verdict.CONSTRAIN or not result.reason:
        return
    suffix = f" (policy: {result.policy_id})" if result.policy_id else ""
    logger.info("Governance constraint applied [%s]: %s%s", stage, result.reason, suffix)


class OpenBoxAgent(Agent):
    env_prefix: str

    _binding: AgentBinding | None = PrivateAttr(default=None)
    _engine: OpenBoxEngine | None = PrivateAttr(default=None)
    _session_id: str | None = PrivateAttr(default=None)
    _run_id: str | None = PrivateAttr(default=None)
    _halted: bool = PrivateAttr(default=False)
    _session_started: bool = PrivateAttr(default=False)
    _session_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _asession_lock: asyncio.Lock | None = PrivateAttr(default=None)
    _crew_name: str = PrivateAttr(default="")
    _crew_execution_id: str = PrivateAttr(default="")
    _last_output: str | None = PrivateAttr(default=None)
    _sequence: int = PrivateAttr(default=0)
    _last_event_ms: int = PrivateAttr(default=0)
    _sequence_lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _session_started_at: float = PrivateAttr(default=0.0)

    def get_delegation_tools(self, agents: Any) -> list[Any]:
        """Return delegation tools that propagate GovernanceHaltError.

        Stock crewai DelegateWorkTool / AskQuestionTool swallow exceptions from
        the delegated agent (base_agent_tools.py:130). Override here so a HALT
        raised by a delegated OpenBoxAgent aborts the crew/flow.
        """
        from openbox.crewai.delegation_tools import make_openbox_delegation_tools

        return make_openbox_delegation_tools(agents)

    # ── Configuration ─────────────────────────────────────────────────────

    def configure_governance(
        self,
        engine: OpenBoxEngine,
        crew_name: str,
        crew_execution_id: str,
    ) -> None:
        """Bind to the engine and resolve this agent's credentials.

        Credentials are resolved here (not at construction) so users can
        construct OpenBoxAgent before env vars are set.
        """
        self._engine = engine
        self._binding = engine.agent_binding(self.env_prefix)
        self._crew_name = crew_name
        self._crew_execution_id = crew_execution_id
        if engine.config.llm_level_governance:
            self._install_llm_pre_check()

    def _install_llm_pre_check(self) -> None:
        llm: Any = self.llm
        if llm is None or getattr(llm, "_openbox_pre_check_installed", False):
            return
        agent = self

        def _check() -> None:
            engine = agent._engine
            stop = engine.active_stop() if engine is not None else None
            if stop is not None:
                raise_for_stop(stop, subject="LLM call")

        original_call = llm.call
        original_acall = getattr(llm, "acall", None)

        def governed_call(*args: Any, **kwargs: Any) -> Any:
            _check()
            return original_call(*args, **kwargs)

        llm.call = governed_call

        if original_acall is not None:

            async def governed_acall(*args: Any, **kwargs: Any) -> Any:
                _check()
                return await original_acall(*args, **kwargs)

            llm.acall = governed_acall

        llm._openbox_pre_check_installed = True

    def _reset_run_state(self) -> None:
        self._session_id = None
        self._run_id = None
        self._session_started = False
        self._halted = False
        self._last_output = None
        with self._sequence_lock:
            self._sequence = 0

    # ── Event construction ────────────────────────────────────────────────

    @property
    def _workflow_type(self) -> str:
        return f"{self.role} Agent"

    def _metadata(self) -> dict[str, Any]:
        return {"crew_name": self._crew_name, "crew_execution_id": self._crew_execution_id}

    def _extra(self, **fields: Any) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "agent_role": self.role,
            "agent_name": self.role,
            "hook_trigger": False,
            "metadata": self._metadata(),
        }
        flow_execution_id = flow_execution_id_var.get()
        if flow_execution_id:
            extra["flow_execution_id"] = flow_execution_id
        extra.update(fields)
        return extra

    def _common(self) -> dict[str, Any]:
        return {
            "workflow_id": self._session_id or "",
            "run_id": self._run_id or "",
            "workflow_type": self._workflow_type,
            "task_queue": self._crew_name,
            "multi_agent_session_id": multi_agent_session_id_var.get(),
        }

    def _workflow_activity(self) -> dict[str, str]:
        # Session events share the run's own activity; Core titles their rows by
        # activity_type, so the agent's name heads the session's open and close.
        return {"activity_id": f"{self._run_id}-wf", "activity_type": self._workflow_type}

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.monotonic() - started_at) * 1000)

    def _stamp(self, event: EventEnvelope) -> EventEnvelope:
        """Give a session event its place in the run.

        Timestamps are strictly increasing per session and ``sequence`` is a
        gapless 1..N count, so Core's timeline order never depends on two
        events landing in different milliseconds.
        """
        with self._sequence_lock:
            now_ms = int(time.time() * 1000)
            self._last_event_ms = max(now_ms, self._last_event_ms + 1)
            self._sequence += 1
            sequence = self._sequence
            stamped_ms = self._last_event_ms
        return dataclasses.replace(
            event,
            timestamp=rfc3339_from_datetime(datetime.fromtimestamp(stamped_ms / 1000, UTC)),
            payload={**event.payload, "sequence": sequence},
        )

    def _activity_context(self, task: OpenBoxTask) -> ActivityContext:
        metadata: dict[str, Any] = {"metadata": self._metadata()}
        flow_execution_id = flow_execution_id_var.get()
        if flow_execution_id:
            metadata["flow_execution_id"] = flow_execution_id
        return ActivityContext(
            workflow_id=self._session_id,
            run_id=self._run_id,
            workflow_type=self._workflow_type,
            task_queue=self._crew_name,
            activity_id=str(uuid.uuid4()),
            activity_type=task.activity_type,
            agent_role=self.role,
            multi_agent_session_id=multi_agent_session_id_var.get(),
            metadata=metadata,
        )

    def _prompt_context(self) -> ActivityContext:
        return ActivityContext(
            workflow_id=self._session_id,
            run_id=self._run_id,
            workflow_type=self._workflow_type,
            task_queue=self._crew_name,
            activity_id=f"{self._run_id}-sig",
            activity_type=PROMPT_SIGNAL,
        )

    @staticmethod
    def _activity_input(task: OpenBoxTask) -> list[dict[str, Any]]:
        activity_input: list[dict[str, Any]] = []
        if task.description:
            activity_input.append({"description": str(task.description)})
        if task.expected_output:
            activity_input.append({"expected_output": str(task.expected_output)})
        return activity_input

    def _prompt_signal(self, prompt: str) -> EventEnvelope:
        context = self._prompt_context()
        return events.signal_received(
            **self._common(),
            signal_name=PROMPT_SIGNAL,
            extra=self._extra(
                activity_id=context.activity_id,
                activity_type=context.activity_type,
                signal_args=[prompt],
            ),
        )

    def _task_started_event(self, act: ActivityContext, task: OpenBoxTask) -> EventEnvelope:
        return events.activity_started(
            **self._common(),
            activity_id=act.activity_id or "",
            activity_type=act.activity_type or "",
            activity_input=self._activity_input(task) or None,
            extra=self._extra(),
        )

    def _task_completed_event(
        self, act: ActivityContext, output: str, started_at: float
    ) -> EventEnvelope:
        return events.activity_completed(
            **self._common(),
            activity_id=act.activity_id or "",
            activity_type=act.activity_type or "",
            extra=self._extra(
                status="completed",
                activity_output={"result": output},
                duration_ms=self._elapsed_ms(started_at),
            ),
        )

    def _activity_failed_event(
        self, act: ActivityContext, error: BaseException, started_at: float
    ) -> EventEnvelope:
        # Closes the activity row with the same id, and carries the reason in
        # activity_output, which is what Core shows for the row.
        return events.activity_completed(
            **self._common(),
            activity_id=act.activity_id or "",
            activity_type=act.activity_type or "",
            error=_error_info(error),
            extra=self._extra(
                status="failed",
                activity_output={"result": str(error)},
                duration_ms=self._elapsed_ms(started_at),
            ),
        )

    def _session_close_event(
        self, error: BaseException | str | None, status: str
    ) -> EventEnvelope:
        if error is None:
            output = {"result": self._last_output}
            return events.workflow_completed(
                **self._common(),
                extra=self._extra(
                    **self._workflow_activity(),
                    status=status,
                    activity_output=output,
                    workflow_output=output,
                    duration_ms=self._elapsed_ms(self._session_started_at),
                ),
            )
        info = _error_info(error)
        return events.workflow_failed(
            **self._common(),
            error=info,
            extra=self._extra(
                **self._workflow_activity(),
                status=status,
                activity_output={"result": info["message"]},
                duration_ms=self._elapsed_ms(self._session_started_at),
            ),
        )

    # ── Verdict handling ──────────────────────────────────────────────────

    def _stop_if_needed(self, result: EvaluationResult, subject: str) -> None:
        if result.verdict.should_stop():
            if result.verdict is Verdict.HALT:
                self._halted = True
            raise_for_stop(result, subject=subject)
        _check_guardrails(result)

    def _enforce(self, result: EvaluationResult, act: ActivityContext, subject: str) -> None:
        self._stop_if_needed(result, subject)
        if result.verdict.requires_approval():
            assert self._engine is not None
            try:
                self._engine.resolve_approval(result, act)
            except _CoreGovernanceHaltError:
                self._halted = True
                raise
        _log_constraint(result, subject)

    async def _aenforce(
        self, result: EvaluationResult, act: ActivityContext, subject: str
    ) -> None:
        self._stop_if_needed(result, subject)
        if result.verdict.requires_approval():
            assert self._engine is not None
            try:
                await self._engine.aresolve_approval(result, act)
            except _CoreGovernanceHaltError:
                self._halted = True
                raise
        _log_constraint(result, subject)

    def _raise_recorded_stop(
        self, act: ActivityContext, cause: BaseException | None = None
    ) -> None:
        """Surface a Layer 3 stop that CrewAI swallowed inside the task."""
        assert self._engine is not None
        stop = self._engine.stop_for(act)
        if stop is None:
            return
        if stop.verdict is Verdict.HALT:
            self._halted = True
        try:
            raise_for_stop(stop, subject="LLM call")
        except Exception as exc:
            raise exc from cause

    @contextmanager
    def _governed_scope(self, act: ActivityContext) -> Iterator[None]:
        """Bind ``act`` so Layer 3 hooks are attributed to this agent's task.

        Each task gets its own trace so a delegated agent's binding never
        replaces the delegator's trace correlation.
        """
        assert self._engine is not None and self._binding is not None
        engine = self._engine
        parent = trace.get_current_span().get_span_context()
        links = [trace.Link(parent)] if parent.is_valid else None
        tracer = trace.get_tracer("openbox")
        with tracer.start_as_current_span(
            f"agent.{self.role}", context=otel_context.Context(), links=links
        ) as span:
            trace_id = span.get_span_context().trace_id or None
            origin_token = handoff_origin_did_var.set(self._binding.did)
            try:
                with activity_scope(act, trace_id=trace_id, store=engine.context_store):
                    try:
                        yield
                    except GOVERNANCE_ERRORS as exc:
                        if isinstance(exc, _CoreGovernanceHaltError):
                            self._halted = True
                        raise
                    except Exception as exc:
                        self._raise_recorded_stop(act, cause=exc)
                        raise
                    self._raise_recorded_stop(act)
            finally:
                handoff_origin_did_var.reset(origin_token)
                engine.clear_stop(act)

    # ── Task execution ────────────────────────────────────────────────────

    def _prepare_task(self, task: Any) -> Any:
        # CrewAI's delegate_work_to_coworker tool dispatches to workers with
        # a plain crewai.Task; coerce so delegated work stays governed.
        if not isinstance(task, OpenBoxTask) and isinstance(task, Task):
            task = OpenBoxTask(
                description=task.description,
                expected_output=task.expected_output,
                agent=task.agent,
                activity_type="delegation",
            )
        if isinstance(task, OpenBoxTask) and self._halted:
            logger.warning(
                "Agent %r refused task — halted by a previous governance verdict in this kickoff",
                self.role,
            )
            raise GovernanceHaltError(
                f"Agent {self.role!r} is halted",
                verdict="halt",
                reason="Agent was halted by a previous governance verdict",
            )
        return task

    def _governed(self, task: Any) -> bool:
        return (
            isinstance(task, OpenBoxTask)
            and self._engine is not None
            and self._binding is not None
        )

    def _excluded(self) -> bool:
        assert self._engine is not None
        return self._crew_name in self._engine.config.exclude_crews

    def execute_task(
        self,
        task: Any,
        context: str | None = None,
        tools: Any | None = None,
    ) -> str:
        task = self._prepare_task(task)
        if not self._governed(task):
            return super().execute_task(task, context, tools)
        assert self._engine is not None
        config = self._engine.config
        governed = not self._excluded()

        handoff_origin_did = handoff_origin_did_var.get()
        self.ensure_session(task if governed else None)
        if self._should_emit_handoff(handoff_origin_did):
            self._emit_handoff(handoff_origin_did)  # type: ignore[arg-type]

        act = self._activity_context(task)
        started_at = time.monotonic()
        started = False
        try:
            # Layer 1: pre-task
            if governed and config.send_task_start_event:
                started = True
                result = self._evaluate(self._task_started_event(act, task))
                self._enforce(result, act, "Task")
                _apply_input_redaction(task, result)

            with self._governed_scope(act):
                output = super().execute_task(task, context, tools)
        except BaseException as exc:
            if started:
                self._close_failed_activity(act, exc, started_at)
            raise

        # Layer 2: post-task
        if governed and config.send_task_completed_event:
            result = self._evaluate(self._task_completed_event(act, output, started_at))
            self._enforce(result, act, "Task output")
            output = _apply_output_redaction(result, output)

        self._last_output = output
        return output

    async def aexecute_task(
        self,
        task: Any,
        context: str | None = None,
        tools: Any | None = None,
    ) -> str:
        task = self._prepare_task(task)
        if not self._governed(task):
            return await super().aexecute_task(task, context, tools)
        assert self._engine is not None
        config = self._engine.config
        governed = not self._excluded()

        handoff_origin_did = handoff_origin_did_var.get()
        await self._aensure_session(task if governed else None)
        if self._should_emit_handoff(handoff_origin_did):
            await self._aemit_handoff(handoff_origin_did)  # type: ignore[arg-type]

        act = self._activity_context(task)
        started_at = time.monotonic()
        started = False
        try:
            # Layer 1: pre-task
            if governed and config.send_task_start_event:
                started = True
                result = await self._aevaluate(self._task_started_event(act, task))
                await self._aenforce(result, act, "Task")
                _apply_input_redaction(task, result)

            with self._governed_scope(act):
                output = await super().aexecute_task(task, context, tools)
        except BaseException as exc:
            if started:
                await self._aclose_failed_activity(act, exc, started_at)
            raise

        # Layer 2: post-task
        if governed and config.send_task_completed_event:
            result = await self._aevaluate(
                self._task_completed_event(act, output, started_at)
            )
            await self._aenforce(result, act, "Task output")
            output = _apply_output_redaction(result, output)

        self._last_output = output
        return output

    def _close_failed_activity(
        self, act: ActivityContext, error: BaseException, started_at: float
    ) -> None:
        try:
            self._evaluate(self._activity_failed_event(act, error, started_at))
        except Exception:  # closure telemetry must never mask the original error
            logger.warning("Failed to close activity %s", act.activity_id, exc_info=True)

    async def _aclose_failed_activity(
        self, act: ActivityContext, error: BaseException, started_at: float
    ) -> None:
        try:
            await self._aevaluate(self._activity_failed_event(act, error, started_at))
        except Exception:
            logger.warning("Failed to close activity %s", act.activity_id, exc_info=True)

    def _require_binding(self) -> AgentBinding:
        if self._binding is None:
            raise OpenBoxConfigError(
                f"Agent {self.role!r} is not bound to an OpenBoxEngine; use engine.govern(crew)"
            )
        return self._binding

    def _evaluate(self, event: EventEnvelope) -> EvaluationResult:
        return self._require_binding().gate.evaluate(self._stamp(event))

    async def _aevaluate(self, event: EventEnvelope) -> EvaluationResult:
        return await self._require_binding().gate.aevaluate(self._stamp(event))

    # ── Handoff ───────────────────────────────────────────────────────────

    def _should_emit_handoff(self, handoff_origin_did: str | None) -> bool:
        if not handoff_origin_did or self._binding is None or not self._binding.did:
            return False
        # Retries within the same agent re-enter execute_task with the
        # contextvar still set to this agent's own DID — that is not a
        # handoff, skip it. Without this guard core persists phantom
        # session_handoffs rows where from_agent_id == to_agent_id.
        if handoff_origin_did == self._binding.did:
            return False
        return bool(multi_agent_session_id_var.get())

    def _handoff_event(self, handoff_origin_did: str) -> EventEnvelope:
        # Signed by the receiving agent; Core derives the receiver from that.
        return events.handoff(
            from_agent_did=handoff_origin_did,
            multi_agent_session_id=multi_agent_session_id_var.get() or "",
        )

    def _emit_handoff(self, handoff_origin_did: str) -> None:
        try:
            self._evaluate(self._handoff_event(handoff_origin_did))
        except Exception as exc:  # never block work on telemetry
            logger.warning("Handoff emission failed: %s", exc)

    async def _aemit_handoff(self, handoff_origin_did: str) -> None:
        try:
            await self._aevaluate(self._handoff_event(handoff_origin_did))
        except Exception as exc:
            logger.warning("Handoff emission failed: %s", exc)

    # ── Session lifecycle ─────────────────────────────────────────────────

    def _open_session(self) -> EventEnvelope:
        assert self._engine is not None
        self._session_id = str(uuid.uuid4())
        self._run_id = str(uuid.uuid4())
        with self._sequence_lock:
            self._sequence = 0
        self._session_started_at = time.monotonic()
        self._engine.register_session(self._session_id, self._require_binding())
        return events.workflow_started(
            **self._common(),
            extra=self._extra(
                **self._workflow_activity(),
                activity_input=[{"agent_role": self.role, **self._metadata()}],
                goal=self.goal,
            ),
        )

    def _abandon_session(self) -> None:
        if self._engine is not None and self._session_id:
            self._engine.unregister_session(self._session_id)
        self._session_id = None
        self._run_id = None

    def ensure_session(self, task: OpenBoxTask | None = None) -> None:
        """Open the governance session on the first task. Thread-safe.

        Sends WorkflowStarted, then one SignalReceived carrying the first
        task's prompt; the signal's verdict is enforced.
        """
        if self._session_started:
            return
        with self._session_lock:
            if self._session_started:
                return
            try:
                self._evaluate(self._open_session())
            except BaseException:
                self._abandon_session()
                raise
            self._session_started = True
            logger.info("Session started for agent %r (session=%s)", self.role, self._session_id)
            if task is not None and task.description:
                self._send_prompt(str(task.description))

    async def _aensure_session(self, task: OpenBoxTask | None = None) -> None:
        """Async :meth:`ensure_session`. Coroutine-safe."""
        if self._session_started:
            return
        if self._asession_lock is None:
            self._asession_lock = asyncio.Lock()
        async with self._asession_lock:
            if self._session_started:
                return
            try:
                await self._aevaluate(self._open_session())
            except BaseException:
                self._abandon_session()
                raise
            self._session_started = True
            logger.info("Session started for agent %r (session=%s)", self.role, self._session_id)
            if task is not None and task.description:
                await self._asend_prompt(str(task.description))

    def _send_prompt(self, prompt: str) -> None:
        context = self._prompt_context()
        started_at = time.monotonic()
        try:
            self._enforce(self._evaluate(self._prompt_signal(prompt)), context, "Prompt")
        except BaseException as exc:
            self._close_failed_activity(context, exc, started_at)
            raise

    async def _asend_prompt(self, prompt: str) -> None:
        context = self._prompt_context()
        started_at = time.monotonic()
        try:
            await self._aenforce(
                await self._aevaluate(self._prompt_signal(prompt)), context, "Prompt"
            )
        except BaseException as exc:
            await self._aclose_failed_activity(context, exc, started_at)
            raise

    def _close_event(
        self, error: BaseException | str | None, force: bool
    ) -> EventEnvelope | None:
        if not self._session_started:
            return None
        if self._halted and not force:
            # Core already terminated the session on the halt verdict.
            # Callers draining a crashed prior run pass force=True to push the
            # terminal event Core never received.
            return None
        status = "halted" if self._halted else ("failed" if error is not None else "completed")
        return self._session_close_event(error, status)

    def close_session(
        self, *, error: BaseException | str | None = None, force: bool = False
    ) -> None:
        """Send WorkflowCompleted, or WorkflowFailed when the kickoff raised."""
        event = self._close_event(error, force)
        try:
            if event is not None:
                self._evaluate(event)
                logger.info("Session closed for agent %r (session=%s)", self.role, self._session_id)
        finally:
            self._abandon_session()

    async def aclose_session(
        self, *, error: BaseException | str | None = None, force: bool = False
    ) -> None:
        event = self._close_event(error, force)
        try:
            if event is not None:
                await self._aevaluate(event)
                logger.info("Session closed for agent %r (session=%s)", self.role, self._session_id)
        finally:
            self._abandon_session()
