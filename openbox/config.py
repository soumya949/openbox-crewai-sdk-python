"""CrewAI-facing governance configuration, resolved into ``openbox_core`` config."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from openbox_core.config import (
    HitlConfig,
    InstrumentationConfig,
    OpenBoxConfig,
    PrivacyConfig,
)
from openbox_core.errors import OpenBoxConfigError

from openbox._version import __version__

SDK_ENGINE = "crewai"

_ON_API_ERROR = ("fail_open", "fail_closed")
_ON_FALLBACK = ("log_warning", "fail_closed")


@dataclass
class GovernanceConfig:
    on_api_error: str = "fail_open"  # "fail_open" or "fail_closed"
    on_fallback: str = "log_warning"  # "log_warning" or "fail_closed"

    api_timeout: float = 30.0
    max_body_size: int | None = None  # None = openbox_core default
    redact_keys: set[str] = field(default_factory=set)

    send_task_start_event: bool = True  # Layer 1
    send_task_completed_event: bool = True  # Layer 2
    llm_level_governance: bool = True  # Layer 3

    hitl_enabled: bool = True
    hitl_poll_interval: float = 5.0
    hitl_max_wait: float | None = None  # None = wait until Core decides or expires

    exclude_crews: set[str] = field(default_factory=set)
    exclude_crews_hitl: set[str] = field(default_factory=set)

    instrument_http: bool = True
    instrument_databases: bool = True
    db_libraries: set[str] | None = None  # None = all available

    instrument_file_io: bool = False

    debug_log: bool = False

    def validate(self) -> GovernanceConfig:
        if self.on_api_error not in _ON_API_ERROR:
            raise OpenBoxConfigError(
                f"governance_policy must be one of {_ON_API_ERROR}, got {self.on_api_error!r}"
            )
        if self.on_fallback not in _ON_FALLBACK:
            raise OpenBoxConfigError(
                f"on_fallback must be one of {_ON_FALLBACK}, got {self.on_fallback!r}"
            )
        if self.hitl_poll_interval <= 0:
            raise OpenBoxConfigError("hitl_poll_interval must be greater than 0")
        return self


def _privacy(config: GovernanceConfig) -> PrivacyConfig:
    privacy = PrivacyConfig(redact_keys=set(config.redact_keys))
    if config.max_body_size is not None:
        privacy.max_body_size = config.max_body_size
    return privacy


def build_engine_config(api_url: str, config: GovernanceConfig) -> OpenBoxConfig:
    """Process-level config for the shared instrumentation runtime.

    It carries no credentials: every request it triggers is routed to the
    acting agent's own client (see ``openbox.engine``).
    """
    return OpenBoxConfig(
        api_url=api_url,
        timeout_seconds=config.api_timeout,
        on_api_error=config.on_api_error,
        sdk_version=__version__,
        sdk_engine=SDK_ENGINE,
        hitl=HitlConfig(
            enabled=config.hitl_enabled,
            poll_interval_ms=int(config.hitl_poll_interval * 1000),
            max_wait_ms=int(config.hitl_max_wait * 1000) if config.hitl_max_wait else None,
        ),
        instrumentation=InstrumentationConfig(
            http_enabled=config.instrument_http,
            db_enabled=config.instrument_databases,
            file_enabled=config.instrument_file_io,
        ),
        privacy=_privacy(config),
    )


def resolve_agent_config(
    env_prefix: str,
    api_url: str,
    config: GovernanceConfig,
    environ: Mapping[str, str] | None = None,
) -> OpenBoxConfig:
    """Resolve one agent's credentials and identity from ``{env_prefix}_*``.

    Only the agent's own prefix is consulted: governed roles must never fall
    back to shared global credentials. Every ``openbox_core`` identity method
    is available under the prefix (``_AGENT_DID``/``_AGENT_PRIVATE_KEY``,
    ``_OKTA_*``, ``_WORKLOAD_PRIVATE_KEY``, ...); the short ``_DID`` and
    ``_PRIVATE_KEY`` names are accepted for the OpenBox DID method.
    """
    prefix = env_prefix.rstrip("_")
    source = os.environ if environ is None else environ
    scoped = {k: v for k, v in source.items() if k.startswith(f"{prefix}_")}

    api_key_env = f"{prefix}_API_KEY"
    if not scoped.get(api_key_env):
        raise OpenBoxConfigError(f"Environment variable {api_key_env!r} is not set")

    explicit: dict[str, Any] = {}
    did = scoped.get(f"{prefix}_AGENT_DID") or scoped.get(f"{prefix}_DID")
    private_key = scoped.get(f"{prefix}_AGENT_PRIVATE_KEY") or scoped.get(f"{prefix}_PRIVATE_KEY")
    if bool(did) != bool(private_key):
        raise OpenBoxConfigError(
            f"Environment variables {prefix + '_DID'!r} and {prefix + '_PRIVATE_KEY'!r} "
            "must be set together"
        )
    if did:
        explicit["agent_did"] = did
        explicit["agent_private_key"] = private_key

    return OpenBoxConfig.resolve(
        env_prefix=prefix,
        # Global OPENBOX_* values are deliberately absent from this mapping.
        environ=scoped,
        api_url=api_url,
        timeout_seconds=config.api_timeout,
        on_api_error=config.on_api_error,
        agent_name=prefix,
        sdk_version=__version__,
        sdk_engine=SDK_ENGINE,
        privacy=_privacy(config),
        **explicit,
    )
