"""E2E governance tests for psycopg2 against a live Postgres."""

import logging
import os
from contextlib import suppress
from pathlib import Path

import psycopg2
import pytest
from crewai import LLM, Crew, Process
from crewai.tools import tool
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxTask, create_openbox_engine
from openbox.errors import GovernanceHaltError

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

ENV_PREFIX = "OPENBOX_DB_TESTER"


def _pg_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ["OPENBOX_DB_TESTER_PG_HOST"],
        port=int(os.environ["OPENBOX_DB_TESTER_PG_PORT"]),
        user=os.environ["OPENBOX_DB_TESTER_PG_USER"],
        password=os.environ["OPENBOX_DB_TESTER_PG_PASSWORD"],
        dbname=os.environ["OPENBOX_DB_TESTER_PG_DBNAME"],
    )


@tool("Run SQL")
def run_sql(sql: str) -> str:
    """Run SQL."""
    conn = _pg_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        try:
            row = cur.fetchone()
        except psycopg2.ProgrammingError:
            row = None
        cur.close()
        return f"result: {row}"
    finally:
        conn.close()


def _drop_table(name: str) -> None:
    conn = _pg_conn()
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _table_exists(name: str) -> bool:
    conn = _pg_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (name,))
        result = cur.fetchone()
        cur.close()
        return bool(result and result[0])
    finally:
        conn.close()


def _make_agent(role: str) -> OpenBoxAgent:
    return OpenBoxAgent(
        role=role,
        env_prefix=ENV_PREFIX,
        goal="Run the SQL the task describes, nothing more.",
        backstory="DB test agent.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        tools=[run_sql],
    )


def test_db_read_allowed() -> None:
    agent = _make_agent("DB Read Tester")

    task = OpenBoxTask(
        name="db_read_allowed",
        activity_type="db_query",
        description="Use the Run SQL tool to execute exactly this query: SELECT 1 AS hello",
        expected_output="The tool's result string.",
        agent=agent,
    )

    with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
        crew = Crew(
            name="db-read-allow-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        assert result is not None


def test_db_write_blocked() -> None:
    table = "openbox_scratch_blocked"
    _drop_table(table)

    agent = _make_agent("DB Block Tester")
    task = OpenBoxTask(
        name="db_write_blocked",
        activity_type="db_query",
        description=(
            f"Use the Run SQL tool to execute exactly this query: "
            f"CREATE TABLE {table} (id int)"
        ),
        expected_output="The tool's result string.",
        agent=agent,
    )

    with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
        crew = Crew(
            name="db-write-block-test",
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
        )

        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()

        assert exc_info.value.verdict is not None

    assert not _table_exists(table), (
        f"Table {table!r} exists — write governance failed to block CREATE"
    )


def test_db_write_payload_shape(capture_engine_evaluations) -> None:
    """The SDK should send a started-stage db_query span with top-level DB fields."""
    table = "openbox_payload_shape_blocked"
    _drop_table(table)
    try:
        agent = _make_agent("DB Payload Tester")
        task = OpenBoxTask(
            name="db_write_payload_shape",
            activity_type="db_query",
            description=(
                f"Use the Run SQL tool to execute exactly this query: "
                f"CREATE TABLE {table} (id int)"
            ),
            expected_output="The tool's result string.",
            agent=agent,
        )

        with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
            captured = capture_engine_evaluations(engine)
            crew = Crew(
                name="db-write-payload-shape",
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
            )

            governed_crew = engine.govern(crew)
            with suppress(GovernanceHaltError):
                governed_crew.kickoff()

        span = next(
            span
            for entry in captured
            for span in entry["payload"].get("spans", [])
            if span.get("hook_type") == "db_query" and span.get("stage") == "started"
        )
        assert span["db_system"] == "postgresql"
        assert span["db_operation"] == "CREATE"
        assert f"CREATE TABLE {table}" in span["db_statement"]
    finally:
        _drop_table(table)
