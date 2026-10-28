"""E2E test — single OpenBoxAgent with a configured DID + private key.

Runs a minimal governed crew (1 agent, 1 task) with Agent Identity Protocol
signing enabled. Every governance call Core receives will carry the 5 AIP
headers and Core will verify the Ed25519 signature against the alias
alias/openbox-agent/<didUuid>.

Prerequisites (in this folder's .env):
    OPENBOX_URL                             Core base URL
    OPENAI_API_KEY / OPENAI_MODEL_NAME      LLM for CrewAI
    OPENBOX_DID_TESTER_API_KEY              API for a provisioned agent
    OPENBOX_DID_TESTER_DID                  did:aip:<uuid> matching that agent
    OPENBOX_DID_TESTER_PRIVATE_KEY          base64 32-byte Ed25519 seed

The agent referenced by the API key must already have its KMS alias
provisioned with the matching public key.

Usage:
    uv run python3 -m pytest tests/e2e/did/ -v
"""

import logging
import os
from pathlib import Path

import pytest
from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine

load_dotenv(Path(__file__).parent / ".env")

ENV_PREFIX = "OPENBOX_DID_TESTER"
DID_ENV = f"{ENV_PREFIX}_DID"


def _make_agent() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="DID Signing Tester",
        goal="Execute the task exactly as described.",
        backstory="A test agent that signs every governance request with its DID.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        env_prefix=ENV_PREFIX,
    )


def test_signed_single_agent_crew(caplog: pytest.LogCaptureFixture) -> None:
    agent = _make_agent()

    task = OpenBoxTask(
        name="did_signed_allow",
        activity_type="did_test",
        description="List 3 benefits of cloud computing.",
        expected_output="A numbered list of 3 benefits.",
        agent=agent,
    )

    with create_openbox_engine(
        governance_policy="fail_closed",
        on_fallback="fail_closed",
        debug_log=True,
    ) as engine:
        crew = Crew(
            name="did-single-agent-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with caplog.at_level(logging.WARNING, logger="openbox"):
            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()

        assert result is not None

    binding = agent._binding
    assert binding is not None and binding.config.identity_method == "openbox_did", (
        "identity must resolve when did + private key envs are set"
    )
    assert binding.did == os.environ[DID_ENV]

    governance_warnings = [
        r for r in caplog.records if r.name.startswith("openbox") and r.levelno >= logging.WARNING
    ]
    assert not governance_warnings, (
        "Core returned errors or fallback responses during signed evaluation:\n"
        + "\n".join(f"{r.levelname} {r.name}: {r.getMessage()}" for r in governance_warnings)
    )
