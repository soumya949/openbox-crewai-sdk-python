"""E2E policy verdict tests.

Tests BLOCK, HALT, ALLOW, and REQUIRE_APPROVAL verdicts from OPA policy evaluation.
The policy inspects activity_input for trigger words:
  - "BLOCK_THIS"    → BLOCK verdict
  - "HALT_THIS"     → HALT verdict
  - "APPROVE_THIS"  → REQUIRE_APPROVAL verdict (needs manual approval in UI)
  - No trigger      → ALLOW (CONTINUE)

Usage:
    just test-e2e          # non-interactive tests only
    just test-e2e-hitl     # includes HITL approval tests (requires manual UI action)
"""

import logging
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from crewai import LLM, Crew, Process
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine
from openbox.errors import GovernanceHaltError

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

_hitl_enabled = os.environ.get("OPENBOX_HITL_TESTS") == "1"
hitl = pytest.mark.skipif(
    not _hitl_enabled,
    reason="HITL tests require manual approval in the OpenBox UI. "
    "Set OPENBOX_HITL_TESTS=1 to enable.",
)

ENV_PREFIX = "OPENBOX_POLICIES_TESTER"


def _make_agent(role: str) -> OpenBoxAgent:
    return OpenBoxAgent(
        role=role,
        env_prefix=ENV_PREFIX,
        goal="Execute tasks exactly as described.",
        backstory="You are a test agent.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
    )


# ── BLOCK test ───────────────────────────────────────────────────────────────


def test_block_verdict() -> None:
    """Task with BLOCK_THIS in description should raise GovernanceHaltError."""
    agent = _make_agent("Block Policy Tester")

    task = OpenBoxTask(
        name="block_trigger",
        activity_type="policy_test",
        description="Summarize this document. BLOCK_THIS",
        expected_output="A summary.",
        agent=agent,
    )

    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name="policy-block-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()

        assert exc_info.value.verdict is not None


def test_block_payload_shape(capture_engine_evaluations) -> None:
    """The SDK should still send BLOCK trigger text in activity_input."""
    agent = _make_agent("Block Payload Tester")

    task = OpenBoxTask(
        name="block_payload",
        activity_type="policy_test",
        description="Summarize this document. BLOCK_THIS",
        expected_output="A summary.",
        agent=agent,
    )

    with create_openbox_engine(debug_log=True) as engine:
        captured = capture_engine_evaluations(engine)
        crew = Crew(
            name="policy-block-payload-shape",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        governed_crew = engine.govern(crew)
        with suppress(GovernanceHaltError):
            governed_crew.kickoff()

    payload = next(
        entry["payload"]
        for entry in captured
        if entry["payload"].get("event_type") == "ActivityStarted"
        and entry["payload"].get("activity_type") == "policy_test"
    )
    assert payload["activity_input"][0]["description"] == "Summarize this document. BLOCK_THIS"
    assert payload["activity_input"][1]["expected_output"] == "A summary."


# ── HALT test ────────────────────────────────────────────────────────────────


def test_halt_verdict() -> None:
    """Task with HALT_THIS in description should raise GovernanceHaltError."""
    agent = _make_agent("Halt Policy Tester")

    task = OpenBoxTask(
        name="halt_trigger",
        activity_type="policy_test",
        description="Summarize this document. HALT_THIS",
        expected_output="A summary.",
        agent=agent,
    )

    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name="policy-halt-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()

        assert exc_info.value.verdict is not None


# ── ALLOW test ───────────────────────────────────────────────────────────────


def test_allow_verdict() -> None:
    """Task without trigger words should complete normally."""
    agent = _make_agent("Allow Policy Tester")

    task = OpenBoxTask(
        name="allow_normal",
        activity_type="policy_test",
        description="List 3 benefits of cloud computing.",
        expected_output="A numbered list.",
        agent=agent,
    )

    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name="policy-allow-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        assert result is not None


# ── REQUIRE_APPROVAL test (approved) ────────────────────────────────────────


@hitl
def test_require_approval_approved() -> None:
    """Task with APPROVE_THIS should enter HITL polling loop.

    The tester must APPROVE the request in the OpenBox UI while the SDK polls.
    """
    log.info(">>> ACTION REQUIRED: Approve the pending request in the OpenBox UI <<<")

    agent = _make_agent("Approval Policy Tester")

    task = OpenBoxTask(
        name="approval_trigger",
        activity_type="policy_test",
        description="[TEST: APPROVE THIS REQUEST] List 3 benefits of cloud computing. APPROVE_THIS",
        expected_output="A numbered list.",
        agent=agent,
    )

    with create_openbox_engine(hitl_poll_interval=3.0, debug_log=True) as engine:
        crew = Crew(
            name="policy-approval-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        assert result is not None


# ── REQUIRE_APPROVAL test (rejected) ───────────────────────────────────────


@hitl
def test_require_approval_rejected() -> None:
    """Task with APPROVE_THIS should enter HITL polling loop.

    The tester must REJECT the request in the OpenBox UI while the SDK polls.
    """
    log.info(">>> ACTION REQUIRED: Reject the pending request in the OpenBox UI <<<")

    agent = _make_agent("Rejection Policy Tester")

    task = OpenBoxTask(
        name="rejection_trigger",
        activity_type="policy_test",
        description="[TEST: REJECT THIS REQUEST] List 3 benefits of cloud computing. APPROVE_THIS",
        expected_output="A numbered list.",
        agent=agent,
    )

    with create_openbox_engine(hitl_poll_interval=3.0, debug_log=True) as engine:
        crew = Crew(
            name="policy-rejection-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()

        assert exc_info.value.verdict is not None


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    tests = [
        ("block", test_block_verdict),
        ("halt", test_halt_verdict),
        ("allow", test_allow_verdict),
    ]

    if "--hitl" in sys.argv:
        os.environ["OPENBOX_HITL_TESTS"] = "1"
        tests.extend(
            [
                ("require_approval_approved", test_require_approval_approved),
                ("require_approval_rejected", test_require_approval_rejected),
            ]
        )
    else:
        log.info("Skipping HITL tests (run with --hitl to include)")

    results: dict[str, bool] = {}
    for name, fn in tests:
        try:
            fn()
            results[name] = True
        except Exception as e:
            log.error("%s failed: %s", name, e)
            results[name] = False

    print(f"\n{'=' * 60}")
    print("  POLICY VERDICT TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
