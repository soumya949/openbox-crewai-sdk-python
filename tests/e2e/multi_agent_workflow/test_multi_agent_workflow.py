"""E2E test for multi_agent_session_id propagation across a hierarchical Crew.

Verifies that when four agents (1 manager + 3 workers) run together inside a
governed Crew, every governance call carries the same `multi_agent_session_id`
in metadata — proving the SDK's "outer wins" guard correctly opens one
session at the Crew boundary and all delegated agents inherit it. Mirrors
the topology of the multi-agent workflow visualization stub:
  Supervisor → Research → Analysis → Writer.

All four agents already exist in OpenBox; this test reuses them:
  - OPENBOX_SENIOR_RESEARCHER — CrewAI E2E - Senior Researcher (acts as manager)
  - OPENBOX_RESEARCHER        — CrewAI E2E - Researcher
  - OPENBOX_DATA_ANALYST      — CrewAI E2E - Data Analyst
  - OPENBOX_WRITER            — CrewAI E2E - Writer

The CrewAI role label "Supervisor" is independent of the OpenBox agent
record's display name; events will show under "Senior Researcher" in the
dashboard.

Required env vars (see .env.example):
  OPENBOX_URL                          Core base URL
  OPENAI_API_KEY                       OpenAI API key
  OPENAI_MODEL_NAME                    e.g. openai/gpt-4o-mini
  OPENBOX_SENIOR_RESEARCHER_API_KEY    manager agent
  OPENBOX_RESEARCHER_API_KEY           researcher worker
  OPENBOX_DATA_ANALYST_API_KEY         analyst worker
  OPENBOX_WRITER_API_KEY               writer worker

Usage:
    python3 -m tests.e2e.multi_agent_workflow.test_multi_agent_workflow
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from crewai import LLM, Crew, Process
from crewai.tools import tool
from dotenv import load_dotenv

from openbox import OpenBoxAgent, OpenBoxEngine, OpenBoxTask, create_openbox_engine

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


SUPERVISOR_PREFIX = "OPENBOX_SENIOR_RESEARCHER"
RESEARCHER_PREFIX = "OPENBOX_RESEARCHER"
ANALYST_PREFIX = "OPENBOX_DATA_ANALYST"
WRITER_PREFIX = "OPENBOX_WRITER"


# -- LLM ---------------------------------------------------------------------


def _llm() -> LLM:
    return LLM(model=os.environ["OPENAI_MODEL_NAME"], temperature=0)


# -- Stub tools (avoid external API deps) ------------------------------------


@tool("web_search")
def web_search(query: str) -> str:
    """Stubbed web search returning canned Q3 2024 SaaS market data."""
    return (
        "Q3 2024 SaaS market grew 8.2% YoY (Gartner). "
        "Enterprise SaaS subsegment grew 11.3%, driven by AI adoption. "
        "APAC declined 4.1% due to currency pressure."
    )


@tool("calculate")
def calculate(operation: str, current: float, previous: float) -> dict:
    """Compute growth_rate or absolute_change between two numbers.

    operation: "growth_rate" returns both rate (%) and absolute change.
               "absolute_change" returns just the absolute change.
    """
    absolute_change = current - previous
    if operation == "growth_rate":
        return {
            "growth_rate": round((current - previous) / previous * 100, 2),
            "absolute_change": absolute_change,
        }
    if operation == "absolute_change":
        return {"absolute_change": absolute_change}
    raise ValueError(f"unsupported op: {operation}")


# -- Agent factories ---------------------------------------------------------


def _make_supervisor() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Supervisor",
        env_prefix=SUPERVISOR_PREFIX,
        goal=(
            "Delegate research, analysis, and writing to specialists; "
            "produce a final Q3 sales summary."
        ),
        backstory="Senior coordinator who routes work and synthesizes results.",
        allow_delegation=True,
        verbose=True,
        llm=_llm(),
    )


def _make_researcher() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Researcher",
        env_prefix=RESEARCHER_PREFIX,
        goal="Find Q3 market trends and competitor benchmarks",
        backstory="Market analyst with web access.",
        tools=[web_search],
        verbose=True,
        llm=_llm(),
    )


def _make_analyst() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Data Analyst",
        env_prefix=ANALYST_PREFIX,
        goal="Compute growth rates and identify anomalies in sales data",
        backstory="Quantitative analyst.",
        tools=[calculate],
        verbose=True,
        llm=_llm(),
    )


def _make_writer() -> OpenBoxAgent:
    return OpenBoxAgent(
        role="Writer",
        env_prefix=WRITER_PREFIX,
        goal="Produce a clean Markdown summary report",
        backstory="Technical writer.",
        verbose=True,
        llm=_llm(),
    )


# -- Governance-payload capture (mirrors collaboration test pattern) --------


@dataclass
class CapturedCall:
    payload: dict[str, Any]


@pytest.fixture
def captured_calls() -> Iterator[list[CapturedCall]]:
    from openbox._clients import AgentClient

    original = (AgentClient.evaluate, AgentClient.aevaluate)
    yield []
    AgentClient.evaluate, AgentClient.aevaluate = original  # type: ignore[method-assign]


def _capture_governance_calls(
    engine: OpenBoxEngine, calls: list[CapturedCall]
) -> None:
    """Wrap every agent client's evaluate/aevaluate to record outbound calls."""
    from openbox._clients import AgentClient

    original_evaluate = AgentClient.evaluate
    original_aevaluate = AgentClient.aevaluate

    def wrapped_evaluate(self, payload):  # type: ignore[no-untyped-def]
        calls.append(CapturedCall(payload=payload))
        return original_evaluate(self, payload)

    async def wrapped_aevaluate(self, payload):  # type: ignore[no-untyped-def]
        calls.append(CapturedCall(payload=payload))
        return await original_aevaluate(self, payload)

    AgentClient.evaluate = wrapped_evaluate  # type: ignore[method-assign]
    AgentClient.aevaluate = wrapped_aevaluate  # type: ignore[method-assign]


# -- Test --------------------------------------------------------------------


def test_session_id_propagates_across_hierarchical_crew(
    captured_calls: list[CapturedCall],
) -> None:
    """All governance events from a hierarchical crew share one multi_agent_session_id."""
    supervisor = _make_supervisor()
    researcher = _make_researcher()
    analyst = _make_analyst()
    writer = _make_writer()

    tasks = [
        OpenBoxTask(
            name="market_research",
            activity_type="research",
            description=(
                "Research Q3 2024 SaaS market growth and competitor benchmarks. "
                "Use the web_search tool."
            ),
            expected_output="Concise summary of industry growth rates.",
        ),
        OpenBoxTask(
            name="sales_analysis",
            activity_type="analysis",
            description=(
                "Compute Q3 revenue growth from $3,722,000 (Q2) to $4,250,000 (Q3). "
                "Use the calculate tool."
            ),
            expected_output="growth_rate and absolute_change.",
        ),
        OpenBoxTask(
            name="summary_report",
            activity_type="writing",
            description=(
                "Combine the market research and sales analysis into a "
                "one-paragraph Markdown summary."
            ),
            expected_output="Markdown summary.",
        ),
    ]

    with create_openbox_engine(debug_log=True) as engine:
        crew = Crew(
            name="q3-sales-report",
            agents=[researcher, analyst, writer],
            tasks=tasks,
            process=Process.hierarchical,
            manager_agent=supervisor,
            verbose=True,
        )
        _capture_governance_calls(engine, captured_calls)
        result = engine.govern(crew).kickoff()

    assert result is not None
    assert captured_calls, "no governance calls captured"

    session_ids = {
        c.payload.get("metadata", {}).get("multi_agent_session_id")
        for c in captured_calls
    }
    session_ids.discard(None)
    assert len(session_ids) == 1, (
        f"expected one shared multi_agent_session_id across all captured calls; "
        f"got {session_ids}"
    )


# -- Main --------------------------------------------------------------------


def main() -> None:
    tests = [
        (
            "session_id_propagates_across_hierarchical_crew",
            test_session_id_propagates_across_hierarchical_crew,
        ),
    ]

    results: dict[str, bool] = {}
    for name, fn in tests:
        try:
            fn([])
            results[name] = True
        except Exception as e:
            log.error("%s failed: %s", name, e)
            results[name] = False

    print(f"\n{'=' * 60}")
    print("  MULTI-AGENT WORKFLOW E2E TEST SUMMARY")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
