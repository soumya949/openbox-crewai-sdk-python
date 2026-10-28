"""E2E test — single OpenBoxAgent signing with a WRONG private key.

Swaps in a freshly generated Ed25519 seed under OPENBOX_BAD_KEY_TESTER_PRIVATE_KEY
so the AIP signature on the startup validate_api_key call fails verification
against the DID's registered public key. Core must reject the request and
engine.govern(crew) must raise before the crew can run.

Prerequisites (in this folder's .env):
    OPENBOX_BAD_KEY_TESTER_API_KEY          API for a provisioned agent
    OPENBOX_BAD_KEY_TESTER_DID              did:aip:<uuid> matching that agent
    OPENBOX_BAD_KEY_TESTER_PRIVATE_KEY      base64 32-byte Ed25519 seed (overwritten at runtime)

Usage:
    uv run python3 -m pytest tests/e2e/did/test_single_agent_bad_key.py -v
"""

import logging
import os
from pathlib import Path

import pytest
from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine
from openbox.errors import OpenBoxAuthError

from ._helpers import generate_wrong_seed_b64

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger(__name__)

ENV_PREFIX = "OPENBOX_BAD_KEY_TESTER"
PRIVATE_KEY_ENV = f"{ENV_PREFIX}_PRIVATE_KEY"


def _make_agent() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="DID Signing Tester",
        goal="Execute the task exactly as described.",
        backstory="A test agent that signs every governance request with its DID.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        env_prefix=ENV_PREFIX,
    )


def test_signed_single_agent_crew_rejects_wrong_key(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_seed = generate_wrong_seed_b64()
    real_seed = os.environ.get(PRIVATE_KEY_ENV)
    assert real_seed, f"{PRIVATE_KEY_ENV} must be set in the test .env"
    assert wrong_seed != real_seed, "generated seed unexpectedly equals the real one"
    monkeypatch.setenv(PRIVATE_KEY_ENV, wrong_seed)

    agent = _make_agent()

    task = OpenBoxTask(
        name="did_signed_reject",
        activity_type="did_test",
        description="List 3 benefits of cloud computing.",
        expected_output="A numbered list of 3 benefits.",
        agent=agent,
    )

    with caplog.at_level(logging.WARNING, logger="openbox"):
        with create_openbox_engine(
            governance_policy="fail_closed",
            on_fallback="fail_closed",
            debug_log=True,
        ) as engine:
            crew = Crew(
                name="did-single-agent-bad-key-test",
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
            )

            with pytest.raises(OpenBoxAuthError) as exc_info:
                governed_crew = engine.govern(crew)
                governed_crew.kickoff()

    log.info("crew rejected at kickoff as expected: %s", exc_info.value)
