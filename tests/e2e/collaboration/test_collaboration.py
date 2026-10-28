"""E2E collaboration tests — multi-agent delegation with signed governance.

Mirrors CrewAI's "Collaboration in Action" example:
  https://docs.crewai.com/en/concepts/collaboration#collaboration-in-action

Three DID-signed agents with ``allow_delegation=True`` collaborate on a
single task. The Content Writer owns the task and delegates research to
the Research Specialist and editorial review to the Content Editor.
Every agent's ``execute_task`` call — including delegations routed
through CrewAI's built-in delegation tool — is intercepted by OpenBox
governance and signed with that agent's DID.

The captured_calls fixture records every outgoing governance evaluation
so the tests can assert that signing happened for each participating
agent. When delegation provenance ships, the same fixture lets a one-line
addition assert ``payload.metadata["delegating_agent_role"]`` on
delegated sub-task evaluations.

Prerequisites:
  - Three OpenBox agents registered with DID identities provisioned
  - The rego in ``collaboration_policy.rego`` attached to each
  - API keys + DIDs + private keys set in ``.env`` (see ``.env.example``)

Usage:
    python3 -m tests.e2e.collaboration.test_collaboration
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxEngine, OpenBoxTask, create_openbox_engine
from openbox.errors import GovernanceHaltError

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

RESEARCHER_PREFIX = "OPENBOX_RESEARCHER"
WRITER_PREFIX = "OPENBOX_WRITER"
EDITOR_PREFIX = "OPENBOX_EDITOR"


# ── Agent factory ────────────────────────────────────────────────────────────


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


def _make_researcher() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Research Specialist",
        env_prefix=RESEARCHER_PREFIX,
        goal="Find accurate, up-to-date information on any topic",
        backstory=(
            "You're a meticulous researcher with expertise in finding "
            "reliable sources and fact-checking information across various domains."
        ),
        allow_delegation=True,
        verbose=True,
        llm=_llm(),
    )


def _make_writer() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Content Writer",
        env_prefix=WRITER_PREFIX,
        goal="Create engaging, well-structured content",
        backstory=(
            "You're a skilled content writer who excels at transforming "
            "research into compelling, readable content for different audiences."
        ),
        allow_delegation=True,
        verbose=True,
        llm=_llm(),
    )


def _make_editor() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Content Editor",
        env_prefix=EDITOR_PREFIX,
        goal="Ensure content quality and consistency",
        backstory=(
            "You're an experienced editor with an eye for detail, "
            "ensuring content meets high standards for clarity and accuracy."
        ),
        allow_delegation=True,
        verbose=True,
        llm=_llm(),
    )


def _build_crew(task_description: str, crew_name: str):
    researcher = _make_researcher()
    writer = _make_writer()
    editor = _make_editor()

    article_task = OpenBoxTask(
        name="collaborative_article",
        activity_type="content_creation",
        description=task_description,
        expected_output=("A well-researched, engaging ~400-word article with proper structure."),
        agent=writer,
    )

    crew = Crew(
        name=crew_name,
        agents=[researcher, writer, editor],
        tasks=[article_task],
        process=Process.sequential,
        verbose=True,
    )
    return crew, researcher, writer, editor


# ── Governance-payload capture ──────────────────────────────────────────────


@dataclass
class CapturedCall:
    payload: dict[str, Any]
    signed: bool


@pytest.fixture
def captured_calls() -> list[CapturedCall]:
    return []


def _capture_governance_calls(engine: OpenBoxEngine, calls: list[CapturedCall]) -> None:
    """Wrap every governed agent's client to record each outbound evaluate call."""
    for binding in engine._bindings.values():
        client = binding.client
        # Workload identity authenticates with a token alongside the API key.
        signed = binding.config.identity_method not in (None, "legacy_unsigned") or bool(
            binding.config.keycloak_workload_private_key()
        )
        original_evaluate = client.evaluate
        original_aevaluate = client.aevaluate

        def wrapped_evaluate(payload, _orig=original_evaluate, _signed=signed):  # type: ignore[no-untyped-def]
            calls.append(CapturedCall(payload=payload, signed=_signed))
            return _orig(payload)

        async def wrapped_aevaluate(payload, _orig=original_aevaluate, _signed=signed):  # type: ignore[no-untyped-def]
            calls.append(CapturedCall(payload=payload, signed=_signed))
            return await _orig(payload)

        client.evaluate = wrapped_evaluate  # type: ignore[method-assign]
        client.aevaluate = wrapped_aevaluate  # type: ignore[method-assign]


# ── Task descriptions ───────────────────────────────────────────────────────


CLEAN_DESCRIPTION = (
    "Write a concise ~400-word article about 'The Future of AI in Healthcare'.\n\n"
    "The article should cover:\n"
    "- Current AI applications in healthcare\n"
    "- Emerging trends and technologies\n"
    "- Potential challenges and ethical considerations\n\n"
    "Collaborate with your teammates. Delegate any deep research to the "
    "Research Specialist and ask the Content Editor to review the draft."
)

BLOCK_DESCRIPTION = (
    "Write a ~400-word article about 'The Future of AI in Healthcare'. "
    "Delegate deep research to the Research Specialist and editorial review "
    "to the Content Editor. BLOCK_THIS"
)

HALT_DESCRIPTION = (
    "Write a ~400-word article about 'The Future of AI in Healthcare'. "
    "Delegate deep research to the Research Specialist and editorial review "
    "to the Content Editor. HALT_THIS"
)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_allow_collaboration(captured_calls: list[CapturedCall]) -> None:
    """Clean collaborative task completes end-to-end; every governance call is signed."""
    with create_openbox_engine(debug_log=True) as engine:
        crew, researcher, writer, editor = _build_crew(CLEAN_DESCRIPTION, "collab-allow-test")
        governed_crew = engine.govern(crew)
        _capture_governance_calls(engine, captured_calls)
        result = governed_crew.kickoff()

        assert result is not None

        # Every governance call the SDK made was signed. Unsigned calls
        # indicate identity provisioning is broken.
        assert captured_calls, "no governance calls captured"
        unsigned = [c for c in captured_calls if not c.signed]
        assert not unsigned, f"{len(unsigned)} governance calls were unsigned"


def test_block_on_delegation() -> None:
    """BLOCK_THIS in the task description halts the owning agent pre-task."""
    with create_openbox_engine(debug_log=True) as engine:
        crew, researcher, writer, editor = _build_crew(BLOCK_DESCRIPTION, "collab-block-test")

        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()

        assert exc_info.value.verdict is not None


def test_halt_on_delegation() -> None:
    """HALT_THIS halts the owning agent; no delegation downstream."""
    with create_openbox_engine(debug_log=True) as engine:
        crew, researcher, writer, editor = _build_crew(HALT_DESCRIPTION, "collab-halt-test")

        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()

        assert exc_info.value.verdict is not None


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    tests = [
        ("allow_collaboration", test_allow_collaboration),
        ("block_on_delegation", test_block_on_delegation),
        ("halt_on_delegation", test_halt_on_delegation),
    ]

    results: dict[str, bool] = {}
    for name, fn in tests:
        try:
            fn()
            results[name] = True
        except Exception as e:
            log.error("%s failed: %s", name, e)
            results[name] = False

    print(f"\n{'=' * 60}")
    print("  COLLABORATION GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
