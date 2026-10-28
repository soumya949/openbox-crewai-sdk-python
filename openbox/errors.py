"""Exception hierarchy — ``openbox_core`` errors plus CrewAI-specific subclasses.

``GovernanceHaltError`` and ``GovernanceBlockedError`` subclass the core types,
so ``except openbox_core.errors.GovernanceBlockedError`` also catches errors
raised by this SDK.
"""

from __future__ import annotations

from typing import NoReturn

from openbox_core.contracts.results import EvaluationResult, Verdict
from openbox_core.errors import (
    ApprovalExpiredError,
    ApprovalRejectedError,
    ApprovalTimeoutError,
    ContractError,
    GovernanceAPIError,
    GuardrailsValidationError,
    OpenBoxAuthError,
    OpenBoxConfigError,
    OpenBoxError,
    OpenBoxInsecureURLError,
    OpenBoxNetworkError,
    OpenBoxSigningError,
)
from openbox_core.errors import GovernanceBlockedError as _CoreGovernanceBlockedError
from openbox_core.errors import GovernanceHaltError as _CoreGovernanceHaltError

__all__ = [
    "ApprovalExpiredError",
    "ApprovalRejectedError",
    "ApprovalTimeoutError",
    "ContractError",
    "GovernanceAPIError",
    "GovernanceApprovalExpiredError",
    "GovernanceBlockedError",
    "GovernanceHaltError",
    "GuardrailsValidationError",
    "OpenBoxAuthError",
    "OpenBoxConfigError",
    "OpenBoxError",
    "OpenBoxInsecureURLError",
    "OpenBoxNetworkError",
    "OpenBoxSigningError",
    "GOVERNANCE_ERRORS",
    "raise_for_stop",
]

# Kept for code written against 1.x.
GovernanceApprovalExpiredError = ApprovalExpiredError


class GovernanceHaltError(_CoreGovernanceHaltError):
    """HALT verdict: the agent stops and refuses further tasks this kickoff."""

    def __init__(
        self,
        message: str,
        *,
        verdict: str | None = None,
        reason: str | None = None,
        policy_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.verdict = verdict
        self.reason = reason
        self.policy_id = policy_id


class GovernanceBlockedError(_CoreGovernanceBlockedError):
    """BLOCK verdict: the task or operation does not run."""

    def __init__(
        self,
        message: str,
        *,
        verdict: str | None = None,
        reason: str | None = None,
        policy_id: str | None = None,
        hook_type: str | None = None,
    ) -> None:
        super().__init__(verdict or Verdict.BLOCK, reason or message)
        self.args = (message,)
        self.policy_id = policy_id
        self.hook_type = hook_type


# Errors CrewAI must neither retry nor swallow.
GOVERNANCE_ERRORS: tuple[type[BaseException], ...] = (
    _CoreGovernanceHaltError,
    _CoreGovernanceBlockedError,
    GovernanceAPIError,
    GuardrailsValidationError,
    ApprovalExpiredError,
    ApprovalRejectedError,
    ApprovalTimeoutError,
    OpenBoxAuthError,
    ContractError,
)


def raise_for_stop(
    result: EvaluationResult,
    *,
    subject: str,
    hook_type: str | None = None,
) -> NoReturn:
    """Raise the native error for a BLOCK/HALT result."""
    halted = result.verdict is Verdict.HALT
    message = f"{subject} {'halted' if halted else 'blocked'} by governance"
    if result.reason:
        message = f"{message}: {result.reason}"
    if halted:
        raise GovernanceHaltError(
            message,
            verdict=result.verdict.value,
            reason=result.reason,
            policy_id=result.policy_id,
        )
    raise GovernanceBlockedError(
        message,
        verdict=Verdict.BLOCK.value,
        reason=result.reason,
        policy_id=result.policy_id,
        hook_type=hook_type,
    )
