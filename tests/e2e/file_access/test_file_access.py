"""E2E tests for file I/O governance.

Runs a governed crew with ``instrument_file_io=True`` and an OPA policy
that blocks writes to paths containing "restricted".

Prerequisites:
  - An OpenBox agent with the rego from file_access_policy.rego
  - API key in .env as OPENBOX_FILE_ACCESS_API_KEY

Usage:
    python3 -m tests.e2e.file_access.test_file_access
"""

import logging
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from crewai import LLM, Crew, Process
from crewai.tools import tool
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine
from openbox.errors import GovernanceBlockedError, GovernanceHaltError

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ENV_PREFIX = "OPENBOX_FILE_ACCESS_TESTER"

TEMP_DIR = Path(__file__).parent / "tmp"


def _ensure_temp_dir() -> Path:
    TEMP_DIR.mkdir(exist_ok=True)
    return TEMP_DIR


@tool("Write File")
def write_file(file_path: str, content: str) -> str:
    """Write to a file."""
    with open(file_path, "w") as f:
        f.write(content)
    return f"Wrote {len(content)} chars to {file_path}"


@tool("Read File")
def read_file(file_path: str) -> str:
    """Read a file."""
    with open(file_path, "r") as f:
        return f.read()


def _make_agent(role: str) -> OpenBoxAgent:
    return OpenBoxAgent(
        role=role,
        env_prefix=ENV_PREFIX,
        goal="Do what the task says.",
        backstory="Test agent.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        tools=[write_file, read_file],
    )


def test_file_read_allowed() -> None:
    """Read from a non-restricted path — should complete normally."""
    tmp_dir = _ensure_temp_dir()
    tmp_file = tmp_dir / "allowed_read.txt"
    tmp_file.write_text("Hello from the allowed read test.")

    try:
        agent = _make_agent("File Read Tester")

        task = OpenBoxTask(
            name="file_read_allowed",
            activity_type="file_access",
            description=(
                f"Read the file at {tmp_file} using the Read File tool "
                "and summarize its contents in one sentence."
            ),
            expected_output="One-sentence summary.",
            agent=agent,
        )

        with create_openbox_engine(instrument_file_io=True, debug_log=True) as engine:
            crew = Crew(
                name="file-read-allow-test",
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
            )

            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()
            assert result is not None
    finally:
        tmp_file.unlink(missing_ok=True)


def test_file_write_allowed() -> None:
    """Write to a non-restricted path — should complete normally."""
    tmp_dir = _ensure_temp_dir()
    tmp_file = tmp_dir / "allowed_write.txt"

    try:
        agent = _make_agent("File Write Tester")

        task = OpenBoxTask(
            name="file_write_allowed",
            activity_type="file_access",
            description=(
                f"Use the Write File tool to write the text 'Test output from allowed write' "
                f"to the file at {tmp_file}."
            ),
            expected_output="Write confirmation.",
            agent=agent,
        )

        with create_openbox_engine(instrument_file_io=True, debug_log=True) as engine:
            crew = Crew(
                name="file-write-allow-test",
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
            )

            governed_crew = engine.govern(crew)
            result = governed_crew.kickoff()
            assert result is not None
            assert tmp_file.exists(), "File not created"
    finally:
        tmp_file.unlink(missing_ok=True)


def test_file_write_blocked() -> None:
    """Write to a path containing 'restricted' — policy should block."""
    tmp_dir = _ensure_temp_dir()
    tmp_file = tmp_dir / "restricted_secrets.txt"

    try:
        agent = _make_agent("File Write Block Tester")

        task = OpenBoxTask(
            name="file_write_blocked",
            activity_type="file_access",
            description=(
                f"Use the Write File tool to write the text 'This should be blocked' "
                f"to the file at {tmp_file}."
            ),
            expected_output="Write confirmation.",
            agent=agent,
        )

        with create_openbox_engine(instrument_file_io=True, debug_log=True) as engine:
            crew = Crew(
                name="file-write-block-test",
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
            )

            with pytest.raises(GovernanceBlockedError):
                governed_crew = engine.govern(crew)
                governed_crew.kickoff()

        assert not tmp_file.exists(), "Restricted file exists after block"
    finally:
        tmp_file.unlink(missing_ok=True)


def test_file_write_payload_shape(capture_engine_evaluations) -> None:
    """The SDK should send a started-stage file.write span with top-level file fields."""
    tmp_dir = _ensure_temp_dir()
    tmp_file = tmp_dir / "restricted_payload_shape.txt"

    try:
        agent = _make_agent("File Payload Tester")

        task = OpenBoxTask(
            name="file_write_payload_shape",
            activity_type="file_access",
            description=(
                f"Use the Write File tool to write the text 'payload check' "
                f"to the file at {tmp_file}."
            ),
            expected_output="Write confirmation.",
            agent=agent,
        )

        with create_openbox_engine(instrument_file_io=True, debug_log=True) as engine:
            captured = capture_engine_evaluations(engine)
            crew = Crew(
                name="file-write-payload-shape",
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
            )

            governed_crew = engine.govern(crew)
            with suppress(GovernanceBlockedError, GovernanceHaltError):
                governed_crew.kickoff()

        span = next(
            span
            for entry in captured
            for span in entry["payload"].get("spans", [])
            if span.get("hook_type") == "file_operation" and span.get("name") == "file.write"
        )
        assert span["file_operation"] == "write"
        assert span["file_path"] == str(tmp_file)
        assert span["file_mode"] in {"w", "a", "x", "w+", "a+", "x+"}
    finally:
        tmp_file.unlink(missing_ok=True)


def test_post_block_task_skipped() -> None:
    """After a block, the next task must not run."""
    tmp_dir = _ensure_temp_dir()
    restricted_file = tmp_dir / "restricted_output.txt"
    post_block_file = tmp_dir / "post_block_canary.txt"

    try:
        agent = _make_agent("Post Block Tester")

        task1 = OpenBoxTask(
            name="trigger_block",
            activity_type="file_access",
            description=(f"Use the Write File tool to write 'trigger' to {restricted_file}."),
            expected_output="Confirmation.",
            agent=agent,
        )
        task2 = OpenBoxTask(
            name="post_block_canary",
            activity_type="file_access",
            description=(f"Use the Write File tool to write 'canary' to {post_block_file}."),
            expected_output="Confirmation.",
            agent=agent,
        )

        with create_openbox_engine(instrument_file_io=True, debug_log=True) as engine:
            crew = Crew(
                name="file-post-block-test",
                agents=[agent],
                tasks=[task1, task2],
                process=Process.sequential,
            )

            with pytest.raises(GovernanceBlockedError):
                governed_crew = engine.govern(crew)
                governed_crew.kickoff()

        assert not post_block_file.exists(), "Canary file exists — task 2 ran after block"
    finally:
        restricted_file.unlink(missing_ok=True)
        post_block_file.unlink(missing_ok=True)


def main() -> None:
    tests = [
        ("file_read_allowed", test_file_read_allowed),
        ("file_write_allowed", test_file_write_allowed),
        ("file_write_blocked", test_file_write_blocked),
        ("post_block_task_skipped", test_post_block_task_skipped),
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
    print("  FILE ACCESS GOVERNANCE TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
