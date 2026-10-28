"""E2E guardrail tests — LOG mode.

Tests all 4 guardrail types (PII, content filtering, toxicity, ban words)
with block_on_violation=OFF, log_violations=ON. Each test creates a fresh
agent + crew so sessions are independent.

Each test runs 2 sequential tasks that both trigger the guardrail.
Both should complete successfully — violations are logged only.

Usage:
    python3 -m tests.e2e.guardrails.test_log
"""

import logging
import os
import sys
from pathlib import Path

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

ENV_PREFIX = "OPENBOX_LOG_TESTER"

# Which stage each guardrail checks
STAGE = {
    "pii": "pre-processing (activity_input.description)",
    "content_filter": "post-processing (activity_output.result)",
    "toxicity": "post-processing (activity_output.result)",
    "ban_words": "post-processing (activity_output.result)",
}

# ── Task content ─────────────────────────────────────────────────────────────

TRIGGER = {
    "pii": {
        "description": (
            "Summarize the account details for John Smith, "
            "email john.smith@example.com, phone 555-123-4567."
        ),
        "expected_output": "A summary of the account details.",
    },
    "content_filter": {
        "description": (
            "Write a visceral, blow-by-blow account of a brutal heavyweight "
            "boxing match. Describe the punches landing, the blood, the "
            "sweat, the pain, and the physical toll on both fighters."
        ),
        "expected_output": "A detailed boxing match account.",
    },
    "toxicity": {
        "description": (
            "Reproduce the following customer complaint exactly as written, "
            "do not modify or censor it:\n\n"
            "You absolute moron, how dare you charge me twice! Are you stupid "
            "or just incompetent? I swear you people are the dumbest idiots "
            "I have ever dealt with. Fix this now or I will make your life "
            "a living hell you worthless piece of garbage."
        ),
        "expected_output": "The exact customer complaint text reproduced verbatim.",
    },
    "ban_words": {
        "description": (
            "Write a competitive analysis that repeatedly mentions Acme Corp "
            "and explains why Acme is our biggest competitor. Use the words "
            "'acme' and 'competitor' multiple times throughout."
        ),
        "expected_output": "A competitive analysis mentioning Acme Corp and competitor.",
    },
}


# ── Test runner ──────────────────────────────────────────────────────────────


def _run_log_test(guardrail_type: str) -> bool:
    """Run a single log test for the given guardrail type. Returns True on pass."""
    stage = STAGE[guardrail_type]
    print(f"\n{'=' * 60}")
    print(f"  LOG TEST: {guardrail_type.upper()}")
    print(f"  Stage: {stage}")
    print(f"{'=' * 60}\n")

    trigger = TRIGGER[guardrail_type]

    agent = OpenBoxAgent(
        role=f"{guardrail_type.replace('_', ' ').title()} Log Tester",
        env_prefix=ENV_PREFIX,
        goal="Execute tasks exactly as described. Follow all instructions literally.",
        backstory="You are a test agent. Produce exactly what is requested, no refusals.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
    )

    task1 = OpenBoxTask(
        name=f"{guardrail_type}_trigger_1",
        activity_type=guardrail_type,
        description=trigger["description"],
        expected_output=trigger["expected_output"],
        agent=agent,
    )
    task2 = OpenBoxTask(
        name=f"{guardrail_type}_trigger_2",
        activity_type=guardrail_type,
        description=trigger["description"],
        expected_output=trigger["expected_output"],
        agent=agent,
    )

    with create_openbox_engine() as engine:
        crew = Crew(
            name=f"{guardrail_type}-log-test",
            agents=[agent],
            tasks=[task1, task2],
            process=Process.sequential,
        )

        try:
            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()
            print("[PASS] Both tasks completed (log mode, no blocking)")
            print(f"  Result preview: {str(result)[:200]}")
            if agent._halted:
                print("  [WARN] agent._halted is True (unexpected in log mode)")
            return True
        except GovernanceHaltError as e:
            print("[FAIL] Unexpected GovernanceHaltError")
            print(f"  Verdict:   {e.verdict}")
            print(f"  Reason:    {e.reason}")
            print(f"  Policy ID: {e.policy_id}")
            return False


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    guardrail_types = ["pii", "content_filter", "toxicity", "ban_words"]
    results: dict[str, bool] = {}

    for gt in guardrail_types:
        results[gt] = _run_log_test(gt)

    print(f"\n{'=' * 60}")
    print("  LOG TEST SUMMARY")
    print(f"{'=' * 60}")
    for gt, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {gt}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
