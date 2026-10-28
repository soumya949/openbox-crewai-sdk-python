"""Per-agent credential resolution and engine validation."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from openbox import (
    GovernanceConfig,
    OpenBoxConfigError,
    OpenBoxInsecureURLError,
    create_openbox_engine,
)
from openbox.config import SDK_ENGINE, resolve_agent_config

from .conftest import API_URL, RESEARCHER_KEY

DID = "did:aip:6ba7b810-9dad-51d1-80b4-00c04fd430c8"


def _seed() -> str:
    raw = Ed25519PrivateKey.generate().private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    return base64.b64encode(raw).decode()


def test_api_key_resolved_from_agent_prefix() -> None:
    config = resolve_agent_config("OPENBOX_RESEARCHER", API_URL, GovernanceConfig())
    assert config.api_key == RESEARCHER_KEY
    assert config.identity_method == "legacy_unsigned"
    assert config.sdk_engine == SDK_ENGINE


def test_missing_agent_key_does_not_fall_back_to_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_API_KEY", "obx_test_shared")
    with pytest.raises(OpenBoxConfigError, match="OPENBOX_EDITOR_API_KEY"):
        resolve_agent_config("OPENBOX_EDITOR", API_URL, GovernanceConfig())


@pytest.mark.parametrize("names", [("DID", "PRIVATE_KEY"), ("AGENT_DID", "AGENT_PRIVATE_KEY")])
def test_did_identity_from_short_and_core_names(
    monkeypatch: pytest.MonkeyPatch, names: tuple[str, str]
) -> None:
    monkeypatch.setenv(f"OPENBOX_RESEARCHER_{names[0]}", DID)
    monkeypatch.setenv(f"OPENBOX_RESEARCHER_{names[1]}", _seed())
    config = resolve_agent_config("OPENBOX_RESEARCHER", API_URL, GovernanceConfig())
    assert config.identity_method == "openbox_did"
    assert config.agent_did == DID
    assert config.load_identity() is not None


def test_did_without_private_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_RESEARCHER_DID", DID)
    with pytest.raises(OpenBoxConfigError, match="must be set together"):
        resolve_agent_config("OPENBOX_RESEARCHER", API_URL, GovernanceConfig())


def test_okta_identity_from_agent_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_RESEARCHER_OKTA_AGENT_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----")
    config = resolve_agent_config("OPENBOX_RESEARCHER", API_URL, GovernanceConfig())
    assert config.identity_method == "okta_ai_agent"


def test_did_and_okta_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_RESEARCHER_DID", DID)
    monkeypatch.setenv("OPENBOX_RESEARCHER_PRIVATE_KEY", _seed())
    monkeypatch.setenv("OPENBOX_RESEARCHER_OKTA_AGENT_ID", "okta-agent")
    with pytest.raises(OpenBoxConfigError, match="mutually exclusive"):
        resolve_agent_config("OPENBOX_RESEARCHER", API_URL, GovernanceConfig())


def test_global_identity_is_not_shared_with_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_AGENT_DID", DID)
    monkeypatch.setenv("OPENBOX_AGENT_PRIVATE_KEY", _seed())
    config = resolve_agent_config("OPENBOX_RESEARCHER", API_URL, GovernanceConfig())
    assert config.agent_did is None


def test_insecure_url_rejected_at_engine_creation() -> None:
    with pytest.raises(OpenBoxInsecureURLError):
        create_openbox_engine(api_url="http://core.example.com")


def test_localhost_http_allowed() -> None:
    create_openbox_engine(api_url="http://localhost:8086").close()


def test_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENBOX_URL", API_URL)
    engine = create_openbox_engine()
    assert engine.api_url == API_URL
    engine.close()


def test_missing_url_rejected() -> None:
    with pytest.raises(OpenBoxConfigError, match="OPENBOX_URL"):
        create_openbox_engine()


@pytest.mark.parametrize(
    "options", [{"governance_policy": "fail_maybe"}, {"on_fallback": "ignore"}]
)
def test_invalid_policies_rejected(options: dict[str, str]) -> None:
    with pytest.raises(OpenBoxConfigError):
        create_openbox_engine(api_url=API_URL, **options)
