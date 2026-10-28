"""Process-level engine: one ``openbox_core`` runtime shared by every governed crew."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from openbox_core.approvals import ApprovalPoller
from openbox_core.config import OpenBoxConfig, _validate_url_security
from openbox_core.context import ContextStore, default_context_store
from openbox_core.contracts.context import ActivityContext
from openbox_core.contracts.results import ApprovalResult, EvaluationResult, Verdict
from openbox_core.gate import GovernanceGate
from openbox_core.runtime import OpenBoxRuntime

from openbox._clients import AgentClient, AgentRoutingClient
from openbox.config import GovernanceConfig, build_engine_config, resolve_agent_config
from openbox.errors import (
    ApprovalExpiredError,
    ApprovalRejectedError,
    GovernanceBlockedError,
    GovernanceHaltError,
    OpenBoxConfigError,
    raise_for_stop,
)

if TYPE_CHECKING:
    from crewai import Crew

    from openbox.crewai.crew import GovernedCrew

logger = logging.getLogger("openbox")

# db_libraries names → openbox_core DB instrumentation targets.
_DB_TARGETS = {
    "sqlalchemy": "sqlalchemy",
    "asyncpg": "asyncpg",
    "redis": "redis",
    "pymongo": "pymongo",
    "dbapi": "dbapi",
    "psycopg2": "dbapi",
    "mysql": "dbapi",
    "pymysql": "dbapi",
    "sqlite3": "dbapi",
}


@dataclass(frozen=True)
class AgentBinding:
    """An agent's resolved credentials: its client and lifecycle gate."""

    config: OpenBoxConfig
    client: AgentClient
    gate: GovernanceGate

    @property
    def did(self) -> str | None:
        return self.config.agent_did


class _ApprovalGuard:
    """Accept only decisions for the approval the SDK is waiting on.

    Polls are keyed by workflow/run/activity, which a task's start and end
    approvals share. Once the newer approval is decided, Core can answer with
    the older one; acting on that would turn a rejection into an approval, so
    a decision for any other approval fails safe as a rejection.
    """

    def __init__(self, router: AgentRoutingClient, expected_id: str | None) -> None:
        self._router = router
        self._expected_id = expected_id

    def _check(self, approval: ApprovalResult | None) -> ApprovalResult | None:
        if (
            approval is None
            or not self._expected_id
            or not approval.approval_id
            or approval.approval_id == self._expected_id
        ):
            return approval
        logger.warning(
            "Core answered approval %s while waiting on %s; treating it as rejected",
            approval.approval_id,
            self._expected_id,
        )
        return ApprovalResult(
            verdict=Verdict.BLOCK,
            reason=(
                f"Core returned the decision for approval {approval.approval_id} "
                f"instead of {self._expected_id}"
            ),
            approval_id=approval.approval_id,
            raw={**approval.raw, "approval_mismatch": True},
        )

    def poll_approval(
        self, workflow_id: str, run_id: str, activity_id: str
    ) -> ApprovalResult | None:
        return self._check(self._router.poll_approval(workflow_id, run_id, activity_id))

    async def apoll_approval(
        self, workflow_id: str, run_id: str, activity_id: str
    ) -> ApprovalResult | None:
        return self._check(await self._router.apoll_approval(workflow_id, run_id, activity_id))


class CrewAIAdapter:
    """``openbox_core`` FrameworkAdapter producing CrewAI-native effects."""

    name = "crewai"

    def __init__(self, engine: OpenBoxEngine) -> None:
        self._engine = engine

    async def handle_approval(
        self, result: EvaluationResult, context: ActivityContext | None = None
    ) -> None:
        await self._engine.aresolve_approval(result, context)

    def handle_approval_sync(
        self, result: EvaluationResult, context: ActivityContext | None = None
    ) -> None:
        self._engine.resolve_approval(result, context)

    def raise_lifecycle_blocked(self, result: EvaluationResult) -> NoReturn:
        raise_for_stop(result, subject="Task")

    def raise_hook_blocked(self, result: EvaluationResult) -> NoReturn:
        # Remembered so the task boundary and the LLM gate can re-raise it if
        # CrewAI swallows this error inside a tool call.
        context = self._engine.current_activity_context()
        if context is not None:
            self._engine.record_stop(context, result)
        raise_for_stop(result, subject="Operation")

    def on_completed_hook_result(
        self, result: EvaluationResult, context: ActivityContext | None = None
    ) -> None:
        # The operation already ran; only future work in this task is stopped.
        if result.verdict.should_stop() and context is not None:
            self._engine.record_stop(context, result)


class OpenBoxEngine:
    """Owns process-level instrumentation and shared governance configuration."""

    def __init__(self, api_url: str, config: GovernanceConfig) -> None:
        self.api_url = api_url.rstrip("/")
        _validate_url_security(self.api_url)
        self.config = config.validate()
        self._lock = threading.Lock()
        self._bindings: dict[str, AgentBinding] = {}
        self._router = AgentRoutingClient(config.on_api_error)
        self._runtime: OpenBoxRuntime | None = None
        self._started_settings: tuple[Any, ...] | None = None
        self._stops: dict[tuple[str | None, str | None], EvaluationResult] = {}

    def __enter__(self) -> OpenBoxEngine:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── Agents ────────────────────────────────────────────────────────────

    def agent_binding(self, env_prefix: str) -> AgentBinding:
        """Resolve (once) the client for the agent credentials under ``env_prefix``."""
        key = env_prefix.rstrip("_")
        with self._lock:
            binding = self._bindings.get(key)
            if binding is None:
                agent_config = resolve_agent_config(key, self.api_url, self.config)
                client = AgentClient(
                    agent_config,
                    on_fallback=self.config.on_fallback,
                    debug_log=self.config.debug_log,
                )
                binding = AgentBinding(
                    config=agent_config,
                    client=client,
                    gate=GovernanceGate(client, agent_config),  # type: ignore[arg-type]
                )
                self._bindings[key] = binding
            return binding

    def validate_agent_api_keys(self, agents: list[Any]) -> None:
        validated: set[str] = set()
        for agent in agents:
            key = str(agent.env_prefix).rstrip("_")
            if key in validated:
                continue
            validated.add(key)
            self.agent_binding(key).client.validate_api_key()

    def register_session(self, workflow_id: str, binding: AgentBinding) -> None:
        self._router.register(workflow_id, binding.client)

    def unregister_session(self, workflow_id: str) -> None:
        self._router.unregister(workflow_id)
        with self._lock:
            for key in [k for k in self._stops if k[0] == workflow_id]:
                del self._stops[key]

    # ── Runtime ───────────────────────────────────────────────────────────

    def _settings(self) -> tuple[Any, ...]:
        c = self.config
        return (
            c.instrument_http,
            c.instrument_databases,
            frozenset(c.db_libraries) if c.db_libraries is not None else None,
            c.instrument_file_io,
        )

    def ensure_started(self) -> OpenBoxRuntime:
        settings = self._settings()
        if self._runtime is None:
            runtime = OpenBoxRuntime(
                build_engine_config(self.api_url, self.config),
                adapter=CrewAIAdapter(self),
                client=self._router,  # type: ignore[arg-type]
            )
            runtime.install_instrumentation()
            self._runtime = runtime
            self._started_settings = settings
            self._limit_db_libraries()
        elif self._started_settings != settings:
            raise OpenBoxConfigError(
                "OpenBoxEngine instrumentation settings changed after startup. "
                "Create a new OpenBoxEngine for different HTTP/DB/file instrumentation."
            )
        return self._runtime

    def _limit_db_libraries(self) -> None:
        libraries = self.config.db_libraries
        if libraries is None or not self.config.instrument_databases:
            return
        from openbox_core.instrumentation import db as db_instrumentation

        wanted: set[str] = set()
        for name in libraries:
            target = _DB_TARGETS.get(name.lower())
            if target is None:
                logger.warning("db_libraries: %r is not supported by openbox_core; ignored", name)
            else:
                wanted.add(target)
        for target in set(_DB_TARGETS.values()) - wanted:
            getattr(db_instrumentation, f"uninstall_{target}")()

    @property
    def runtime(self) -> OpenBoxRuntime:
        if self._runtime is None:
            raise OpenBoxConfigError("OpenBoxEngine has not been started")
        return self._runtime

    @property
    def context_store(self) -> ContextStore:
        if self._runtime is not None:
            return self._runtime.context_store
        return default_context_store()

    def current_activity_context(self) -> ActivityContext | None:
        """The governed activity running here (ContextVar, then OTel trace)."""
        store = self.context_store
        context = store.current_activity_context()
        if context is not None:
            return context
        from opentelemetry import trace

        trace_id = trace.get_current_span().get_span_context().trace_id
        return store.context_for_trace(trace_id) if trace_id else None

    # ── Stop verdicts (Layer 3 → task boundary / LLM gate) ────────────────

    def record_stop(self, context: ActivityContext, result: EvaluationResult) -> None:
        key = (context.workflow_id, context.activity_id)
        with self._lock:
            existing = self._stops.get(key)
            if existing is None or result.verdict.priority > existing.verdict.priority:
                self._stops[key] = result

    def stop_for(self, context: ActivityContext) -> EvaluationResult | None:
        with self._lock:
            result = self._stops.get((context.workflow_id, context.activity_id))
        if result is not None:
            return result
        if self.context_store.is_activity_aborted(context.workflow_id, context.activity_id):
            return EvaluationResult(
                verdict=Verdict.BLOCK, reason="Activity aborted by a prior governance verdict"
            )
        return None

    def active_stop(self) -> EvaluationResult | None:
        context = self.current_activity_context()
        return self.stop_for(context) if context is not None else None

    def clear_stop(self, context: ActivityContext) -> None:
        with self._lock:
            self._stops.pop((context.workflow_id, context.activity_id), None)
        self.context_store.clear_activity_aborted(context.workflow_id, context.activity_id)

    # ── Approvals ─────────────────────────────────────────────────────────

    def _poller(self, result: EvaluationResult) -> ApprovalPoller:
        return ApprovalPoller(
            _ApprovalGuard(self._router, result.approval_id or result.governance_event_id),
            poll_interval_seconds=self.config.hitl_poll_interval,
            max_wait_seconds=self.config.hitl_max_wait,
        )

    def _approval_ids(self, context: ActivityContext | None) -> tuple[str, str, str]:
        if context is None or not (context.workflow_id and context.run_id and context.activity_id):
            raise ApprovalRejectedError(
                "REQUIRE_APPROVAL verdict without the workflow/run/activity ids to "
                "poll — failing safe (operation not run)"
            )
        return context.workflow_id, context.run_id, context.activity_id

    def _hitl_unavailable(self, result: EvaluationResult, crew_name: str | None) -> bool:
        """True when approval must not be polled; applies ``on_fallback`` first."""
        if self.config.hitl_enabled and crew_name not in self.config.exclude_crews_hitl:
            return False
        reason = result.reason or "approval required"
        if self.config.on_fallback == "fail_closed":
            raise GovernanceBlockedError(
                f"Approval required but HITL is disabled for this crew: {reason}",
                reason=reason,
                policy_id=result.policy_id,
            )
        logger.warning("Approval required but HITL is disabled; continuing: %s", reason)
        return True

    @staticmethod
    def _enforce_approval(approval: ApprovalResult) -> None:
        if approval.allow_shaped:
            return
        reason = approval.reason or "Approval rejected"
        if approval.raw.get("approval_mismatch"):
            raise GovernanceBlockedError(
                f"Approval could not be confirmed: {reason}", reason=reason
            )
        if approval.expired:
            raise ApprovalExpiredError(approval.reason or "Approval window expired")
        if approval.verdict is Verdict.HALT:
            raise GovernanceHaltError(
                f"Approval rejected: {reason}", verdict=Verdict.HALT.value, reason=reason
            )
        raise GovernanceBlockedError(f"Approval rejected: {reason}", reason=reason)

    def resolve_approval(
        self, result: EvaluationResult, context: ActivityContext | None
    ) -> None:
        """Wait for a human decision; return only when approved."""
        if self._hitl_unavailable(result, context.task_queue if context else None):
            return
        approval = self._poller(result).wait_for_decision(*self._approval_ids(context))
        self._enforce_approval(approval)

    async def aresolve_approval(
        self, result: EvaluationResult, context: ActivityContext | None
    ) -> None:
        if self._hitl_unavailable(result, context.task_queue if context else None):
            return
        approval = await self._poller(result).await_decision(*self._approval_ids(context))
        self._enforce_approval(approval)

    # ── Crews ─────────────────────────────────────────────────────────────

    def govern(self, crew: Crew) -> GovernedCrew:
        governed_crew = self._to_governed_crew(crew)
        governed_crew.bind_openbox_engine(self)
        return governed_crew

    def _to_governed_crew(self, crew: Crew) -> GovernedCrew:
        from crewai import Crew

        from openbox.crewai.crew import GovernedCrew

        if isinstance(crew, GovernedCrew):
            return crew
        if not isinstance(crew, Crew):
            raise OpenBoxConfigError(
                "OpenBoxEngine.govern(...) expects a CrewAI Crew instance."
            )

        excluded_fields = {
            "id",
            "usage_metrics",
            "token_usage",
            "execution_logs",
            "execution_context",
            "checkpoint_inputs",
            "checkpoint_train",
            "checkpoint_kickoff_event_id",
        }
        crew_kwargs = {
            field_name: getattr(crew, field_name)
            for field_name in Crew.model_fields
            if field_name not in excluded_fields
        }
        return GovernedCrew.model_construct(**crew_kwargs)

    def close(self) -> None:
        if self._runtime is not None:
            self._runtime.close()
        self._runtime = None
        self._started_settings = None
        with self._lock:
            bindings = list(self._bindings.values())
            self._bindings.clear()
            self._stops.clear()
        for binding in bindings:
            binding.client.close()
