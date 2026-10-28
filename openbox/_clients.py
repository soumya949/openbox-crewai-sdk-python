"""Per-agent evaluation clients and the router the shared runtime sends through.

Each governed role signs with its own credentials, but ``openbox_core``
instrumentation is process-wide and evaluates through one runtime client.
``AgentRoutingClient`` is that client: it resolves the acting agent from the
``workflow_id`` every hook and approval request carries (each agent session
has its own) and forwards to that agent's ``AgentClient``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from openbox_core.client import EvaluationClient
from openbox_core.config import OpenBoxConfig
from openbox_core.contracts.results import ApprovalResult, EvaluationResult, Verdict
from openbox_core.errors import GovernanceAPIError

logger = logging.getLogger("openbox")

_DEBUG_LOG_DIR = "openbox_logs"


def _trace_logger(name: str, api_url: str) -> logging.Logger:
    """File logger at ``openbox_logs/agent_<name>.log`` for ``debug_log``."""
    os.makedirs(_DEBUG_LOG_DIR, exist_ok=True)
    trace = logging.getLogger(f"openbox.governance.trace.{name}")
    trace.setLevel(logging.DEBUG)
    trace.propagate = False
    trace.handlers.clear()
    handler = logging.FileHandler(
        os.path.join(_DEBUG_LOG_DIR, f"agent_{name}.log"), mode="w", encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    trace.addHandler(handler)
    trace.debug("OpenBox governance trace started — %s", api_url)
    return trace


class AgentClient:
    """One agent's ``EvaluationClient`` plus the CrewAI ``on_fallback`` policy."""

    def __init__(
        self,
        config: OpenBoxConfig,
        *,
        on_fallback: str,
        debug_log: bool = False,
    ) -> None:
        self.config = config
        self._on_fallback = on_fallback
        self._client = EvaluationClient(
            config.api_url,
            config.api_key,
            timeout_seconds=config.timeout_seconds,
            on_api_error=config.on_api_error,
            identity=config.load_okta_identity() or config.load_identity(),
            okta_bootstrap_private_key=config.okta_bootstrap_private_key(),
            workload_private_key=config.keycloak_workload_private_key(),
            sdk_version=config.sdk_version,
            sdk_engine=config.sdk_engine,
            sdk_language=config.sdk_language,
        )
        self._trace = (
            _trace_logger(config.api_key[-8:], config.api_url) if debug_log else None
        )

    def _log(self, what: str, request: Any, response: Any) -> None:
        if self._trace is None:
            return
        self._trace.debug(
            "─── %s ───\nREQUEST:\n%s\nRESPONSE:\n%s",
            what,
            json.dumps(request, indent=2, default=str),
            json.dumps(getattr(response, "raw", response), indent=2, default=str),
        )

    def _apply_fallback(self, result: EvaluationResult) -> EvaluationResult:
        # Core-side fallback (Core reports fallback_used) is distinct from the
        # client's own fail-open result, which on_api_error already governs.
        if self._on_fallback != "fail_closed" or not result.raw.get("fallback_used"):
            return result
        reason = result.reason or "Core used a fallback decision"
        logger.warning("Governance fallback rejected by on_fallback=fail_closed: %s", reason)
        return EvaluationResult(
            verdict=Verdict.BLOCK,
            reason=f"Governance fallback rejected: {reason}",
            policy_id=result.policy_id,
            fallback_used=True,
            raw=result.raw,
        )

    def evaluate(self, payload: dict) -> EvaluationResult:
        result = self._client.evaluate(payload)
        self._log(f"evaluate {payload.get('event_type')}", payload, result)
        return self._apply_fallback(result)

    async def aevaluate(self, payload: dict) -> EvaluationResult:
        result = await self._client.aevaluate(payload)
        self._log(f"evaluate {payload.get('event_type')}", payload, result)
        return self._apply_fallback(result)

    def poll_approval(
        self, workflow_id: str, run_id: str, activity_id: str
    ) -> ApprovalResult | None:
        result = self._client.poll_approval(workflow_id, run_id, activity_id)
        self._log("approval poll", {"workflow_id": workflow_id, "activity_id": activity_id}, result)
        return result

    async def apoll_approval(
        self, workflow_id: str, run_id: str, activity_id: str
    ) -> ApprovalResult | None:
        result = await self._client.apoll_approval(workflow_id, run_id, activity_id)
        self._log("approval poll", {"workflow_id": workflow_id, "activity_id": activity_id}, result)
        return result

    def validate_api_key(self) -> bool:
        return self._client.validate_api_key()

    async def avalidate_api_key(self) -> bool:
        return await self._client.avalidate_api_key()

    def close(self) -> None:
        self._client.close()

    async def aclose(self) -> None:
        await self._client.aclose()


class AgentRoutingClient:
    """Runtime client that forwards each request to the acting agent's client."""

    def __init__(self, on_api_error: str) -> None:
        self._on_api_error = on_api_error
        self._lock = threading.Lock()
        self._sessions: dict[str, AgentClient] = {}

    def register(self, workflow_id: str, client: AgentClient) -> None:
        with self._lock:
            self._sessions[workflow_id] = client

    def unregister(self, workflow_id: str) -> None:
        with self._lock:
            self._sessions.pop(workflow_id, None)

    def client_for(self, workflow_id: str | None) -> AgentClient | None:
        if not workflow_id:
            return None
        with self._lock:
            return self._sessions.get(workflow_id)

    def _unrouted(self, workflow_id: str | None) -> EvaluationResult:
        reason = f"No governed agent session for workflow_id={workflow_id!r}"
        logger.warning(reason)
        if self._on_api_error == "fail_closed":
            raise GovernanceAPIError(reason)
        return EvaluationResult.fallback_allow(reason)

    def evaluate(self, payload: dict) -> EvaluationResult:
        client = self.client_for(payload.get("workflow_id"))
        if client is None:
            return self._unrouted(payload.get("workflow_id"))
        return client.evaluate(payload)

    async def aevaluate(self, payload: dict) -> EvaluationResult:
        client = self.client_for(payload.get("workflow_id"))
        if client is None:
            return self._unrouted(payload.get("workflow_id"))
        return await client.aevaluate(payload)

    def poll_approval(
        self, workflow_id: str, run_id: str, activity_id: str
    ) -> ApprovalResult | None:
        client = self.client_for(workflow_id)
        if client is None:
            return None
        return client.poll_approval(workflow_id, run_id, activity_id)

    async def apoll_approval(
        self, workflow_id: str, run_id: str, activity_id: str
    ) -> ApprovalResult | None:
        client = self.client_for(workflow_id)
        if client is None:
            return None
        return await client.apoll_approval(workflow_id, run_id, activity_id)

    def close(self) -> None:
        # Agent clients are owned and closed by the engine.
        with self._lock:
            self._sessions.clear()

    async def aclose(self) -> None:
        self.close()
