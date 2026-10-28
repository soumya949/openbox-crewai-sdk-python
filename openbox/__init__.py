"""OpenBox CrewAI SDK — governance for CrewAI crews, built on ``openbox_core``."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from openbox_core.contracts.results import EvaluationResult, Verdict
from openbox_core.instrumentation.function import governed

from openbox._version import __version__
from openbox.config import GovernanceConfig
from openbox.crewai.agent import OpenBoxAgent
from openbox.crewai.crew import GovernedCrew
from openbox.crewai.task import OpenBoxTask
from openbox.engine import OpenBoxEngine
from openbox.errors import (
    ApprovalExpiredError,
    ApprovalRejectedError,
    ApprovalTimeoutError,
    ContractError,
    GovernanceAPIError,
    GovernanceApprovalExpiredError,
    GovernanceBlockedError,
    GovernanceHaltError,
    GuardrailsValidationError,
    OpenBoxAuthError,
    OpenBoxConfigError,
    OpenBoxError,
    OpenBoxInsecureURLError,
    OpenBoxNetworkError,
    OpenBoxSigningError,
)

logger = logging.getLogger("openbox")

# Kept for code written against 1.x.
GovernanceResponse = EvaluationResult

__all__ = [
    "__version__",
    "create_openbox_engine",
    "create_openbox_flow",
    "governed",
    "OpenBoxAgent",
    "OpenBoxTask",
    "GovernedCrew",
    "OpenBoxEngine",
    "GovernanceConfig",
    "Verdict",
    "EvaluationResult",
    "GovernanceResponse",
    "OpenBoxError",
    "OpenBoxConfigError",
    "OpenBoxAuthError",
    "OpenBoxSigningError",
    "OpenBoxNetworkError",
    "OpenBoxInsecureURLError",
    "ContractError",
    "GovernanceAPIError",
    "GovernanceHaltError",
    "GovernanceBlockedError",
    "GuardrailsValidationError",
    "ApprovalExpiredError",
    "ApprovalRejectedError",
    "ApprovalTimeoutError",
    "GovernanceApprovalExpiredError",
]


def create_openbox_engine(
    *,
    api_url: str | None = None,
    config: GovernanceConfig | None = None,
    governance_timeout: float = 30.0,
    governance_policy: str = "fail_open",
    on_fallback: str = "log_warning",
    send_task_start_event: bool = True,
    send_task_completed_event: bool = True,
    llm_level_governance: bool = True,
    hitl_enabled: bool = True,
    hitl_poll_interval: float = 5.0,
    hitl_max_wait: float | None = None,
    exclude_crews_hitl: set[str] | None = None,
    instrument_http: bool = True,
    instrument_databases: bool = True,
    db_libraries: set[str] | None = None,
    instrument_file_io: bool = False,
    max_body_size: int | None = None,
    redact_keys: set[str] | None = None,
    debug_log: bool = False,
) -> OpenBoxEngine:
    resolved_api_url = api_url or os.environ.get("OPENBOX_URL")
    if not resolved_api_url:
        raise OpenBoxConfigError(
            "api_url must be provided or OPENBOX_URL environment variable must be set"
        )

    resolved_config = config or GovernanceConfig(
        on_api_error=governance_policy,
        on_fallback=on_fallback,
        api_timeout=governance_timeout,
        max_body_size=max_body_size,
        redact_keys=redact_keys or set(),
        send_task_start_event=send_task_start_event,
        send_task_completed_event=send_task_completed_event,
        llm_level_governance=llm_level_governance,
        hitl_enabled=hitl_enabled,
        hitl_poll_interval=hitl_poll_interval,
        hitl_max_wait=hitl_max_wait,
        exclude_crews_hitl=exclude_crews_hitl or set(),
        instrument_http=instrument_http,
        instrument_databases=instrument_databases,
        db_libraries=db_libraries,
        instrument_file_io=instrument_file_io,
        debug_log=debug_log,
    )
    return OpenBoxEngine(api_url=resolved_api_url, config=resolved_config)


def create_openbox_flow(
    flow_class: type,
    **flow_kwargs: Any,
) -> Any:
    """Wrap a Flow class with flow_execution_id and multi_agent_session_id correlation.

    Two correlation ids are set on ContextVars during kickoff:
      - flow_execution_id: identifies a single Flow run end-to-end. Resolved
        from inputs["id"] (explicit override), Flow.execution_id (CrewAI's
        per-run id), Flow.flow_id (legacy), or a fresh uuid4 fallback.
      - multi_agent_session_id: groups governance events for one kickoff.
        Re-entry-safe — if already set by an outer boundary, propagate.
    """
    from openbox._context import flow_execution_id_var, multi_agent_session_id_var

    flow = flow_class(**flow_kwargs)

    original_kickoff = flow.kickoff

    def _resolve_flow_execution_id(kwargs: dict[str, Any]) -> str:
        inputs = kwargs.get("inputs") or {}
        return (
            inputs.get("id")
            or getattr(flow, "execution_id", None)
            or getattr(flow, "flow_id", None)
            or str(uuid.uuid4())
        )

    def wrapped_kickoff(**kwargs: Any) -> Any:
        flow_execution_id = _resolve_flow_execution_id(kwargs)
        flow_token = flow_execution_id_var.set(flow_execution_id)
        logger.info("Flow execution started (flow_execution_id=%s)", flow_execution_id)
        try:
            if multi_agent_session_id_var.get() is not None:
                return original_kickoff(**kwargs)
            session_id = str(uuid.uuid4())
            session_token = multi_agent_session_id_var.set(session_id)
            logger.info(
                "Multi-agent session started (multi_agent_session_id=%s)", session_id
            )
            try:
                return original_kickoff(**kwargs)
            finally:
                multi_agent_session_id_var.reset(session_token)
        finally:
            flow_execution_id_var.reset(flow_token)

    flow.kickoff = wrapped_kickoff  # type: ignore[method-assign]

    if hasattr(flow, "akickoff"):
        original_akickoff = flow.akickoff

        async def wrapped_akickoff(**kwargs: Any) -> Any:
            flow_execution_id = _resolve_flow_execution_id(kwargs)
            flow_token = flow_execution_id_var.set(flow_execution_id)
            logger.info(
                "Async flow execution started (flow_execution_id=%s)", flow_execution_id
            )
            try:
                if multi_agent_session_id_var.get() is not None:
                    return await original_akickoff(**kwargs)
                session_id = str(uuid.uuid4())
                session_token = multi_agent_session_id_var.set(session_id)
                logger.info(
                    "Async multi-agent session started (multi_agent_session_id=%s)",
                    session_id,
                )
                try:
                    return await original_akickoff(**kwargs)
                finally:
                    multi_agent_session_id_var.reset(session_token)
            finally:
                flow_execution_id_var.reset(flow_token)

        flow.akickoff = wrapped_akickoff  # type: ignore[method-assign]

    return flow
