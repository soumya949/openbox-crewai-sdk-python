"""E2E governance tests for async Postgres drivers (asyncpg, aiopg, psycopg 3)."""

import asyncio
import logging
import os
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


def _pg_kwargs() -> dict:
    return dict(
        host=os.environ["OPENBOX_DB_TESTER_PG_HOST"],
        port=int(os.environ["OPENBOX_DB_TESTER_PG_PORT"]),
        user=os.environ["OPENBOX_DB_TESTER_PG_USER"],
        password=os.environ["OPENBOX_DB_TESTER_PG_PASSWORD"],
        dbname=os.environ["OPENBOX_DB_TESTER_PG_DBNAME"],
    )


def _drop_table(name: str) -> None:
    conn = psycopg2.connect(**_pg_kwargs())
    try:
        cur = conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()
        cur.close()
    finally:
        conn.close()


def _table_exists(name: str) -> bool:
    conn = psycopg2.connect(**_pg_kwargs())
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass(%s)", (name,))
        result = cur.fetchone()
        cur.close()
        return bool(result and result[0])
    finally:
        conn.close()


@tool("Asyncpg Query")
def asyncpg_query(sql: str) -> str:
    """Run SQL via asyncpg."""
    import asyncpg

    async def _run() -> str:
        kw = _pg_kwargs()
        conn = await asyncpg.connect(
            host=kw["host"], port=kw["port"], user=kw["user"],
            password=kw["password"], database=kw["dbname"],
        )
        try:
            row = await conn.fetchrow(sql)
            return f"result: {row}"
        finally:
            await conn.close()

    return asyncio.run(_run())


@tool("Aiopg Query")
def aiopg_query(sql: str) -> str:
    """Run SQL via aiopg."""
    import aiopg

    async def _run() -> str:
        kw = _pg_kwargs()
        dsn = (
            f"host={kw['host']} port={kw['port']} user={kw['user']} "
            f"password={kw['password']} dbname={kw['dbname']}"
        )
        conn = await aiopg.connect(dsn)
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                try:
                    row = await cur.fetchone()
                except psycopg2.ProgrammingError:
                    row = None
            return f"result: {row}"
        finally:
            conn.close()

    return asyncio.run(_run())


@tool("Psycopg3 Async Query")
def psycopg3_async_query(sql: str) -> str:
    """Run SQL via psycopg 3 async."""
    import psycopg

    async def _run() -> str:
        kw = _pg_kwargs()
        async with await psycopg.AsyncConnection.connect(
            host=kw["host"], port=kw["port"], user=kw["user"],
            password=kw["password"], dbname=kw["dbname"],
        ) as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                try:
                    row = await cur.fetchone()
                except psycopg.ProgrammingError:
                    row = None
            return f"result: {row}"

    return asyncio.run(_run())


def _make_agent(role: str, tool_fn) -> OpenBoxAgent:
    return OpenBoxAgent(
        role=role,
        env_prefix=ENV_PREFIX,
        goal="Run the SQL the task describes, nothing more.",
        backstory="Async DB test agent.",
        llm=LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0),
        tools=[tool_fn],
    )


def test_asyncpg_read_allowed() -> None:
    agent = _make_agent("Asyncpg Read Tester", asyncpg_query)
    task = OpenBoxTask(
        name="asyncpg_read_allowed",
        activity_type="db_query",
        description="Use the Asyncpg Query tool to execute exactly: SELECT 1 AS hello",
        expected_output="The tool's result string.",
        agent=agent,
    )
    with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
        crew = Crew(
            name="asyncpg-read-allow", agents=[agent], tasks=[task],
            process=Process.sequential,
        )
        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        assert result is not None


def test_aiopg_read_allowed() -> None:
    agent = _make_agent("Aiopg Read Tester", aiopg_query)
    task = OpenBoxTask(
        name="aiopg_read_allowed",
        activity_type="db_query",
        description="Use the Aiopg Query tool to execute exactly: SELECT 1 AS hello",
        expected_output="The tool's result string.",
        agent=agent,
    )
    with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
        crew = Crew(
            name="aiopg-read-allow", agents=[agent], tasks=[task],
            process=Process.sequential,
        )
        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        assert result is not None


def test_psycopg3_async_read_allowed() -> None:
    agent = _make_agent("Psycopg3 Read Tester", psycopg3_async_query)
    task = OpenBoxTask(
        name="psycopg3_read_allowed",
        activity_type="db_query",
        description="Use the Psycopg3 Async Query tool to execute exactly: SELECT 1 AS hello",
        expected_output="The tool's result string.",
        agent=agent,
    )
    with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
        crew = Crew(
            name="psycopg3-read-allow", agents=[agent], tasks=[task],
            process=Process.sequential,
        )
        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        assert result is not None


def test_asyncpg_write_blocked() -> None:
    table = "openbox_async_scratch_blocked"
    _drop_table(table)
    agent = _make_agent("Asyncpg Block Tester", asyncpg_query)
    task = OpenBoxTask(
        name="asyncpg_write_blocked",
        activity_type="db_query",
        description=(
            f"Use the Asyncpg Query tool to execute exactly: CREATE TABLE {table} (id int)"
        ),
        expected_output="The tool's result string.",
        agent=agent,
    )
    with create_openbox_engine(instrument_databases=True, debug_log=True) as engine:
        crew = Crew(
            name="asyncpg-write-block", agents=[agent], tasks=[task],
            process=Process.sequential,
        )
        with pytest.raises(GovernanceHaltError) as exc_info:
            governed_crew = engine.govern(crew)
            governed_crew.kickoff()
        assert exc_info.value.verdict is not None
    assert not _table_exists(table), (
        f"Table {table!r} exists — asyncpg governance failed to block CREATE"
    )
