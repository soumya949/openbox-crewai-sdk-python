#!/usr/bin/env python
"""Governed CrewAI Flow with 2 crews, 3 agents.

Each crew is a separate @CrewBase class with its own config directory. The
Flow orchestrates them via @start / @listen, and the OpenBox engine wraps
both crews so every governance event is signed with each agent's DID.

Prerequisite: provision an identity for each of the three agents
(senior_researcher, data_analyst, technical_writer) in the OpenBox
Platform and paste the DIDs + private keys into ``.env``.
"""

import asyncio
import logging

from crewai.flow.flow import Flow, listen, start
from dotenv import load_dotenv
from pydantic import BaseModel

from demo_03_flow.crews.research_crew.research_crew import ResearchCrew
from demo_03_flow.crews.writing_crew.writing_crew import WritingCrew
from openbox import OpenBoxEngine, create_openbox_engine, create_openbox_flow
from openbox.errors import GovernanceHaltError

load_dotenv()

logging.basicConfig(level=logging.WARNING)


# ── State ────────────────────────────────────────────────────────────────────


class ResearchState(BaseModel):
    topic: str = "The impact of AI on healthcare"
    research_results: str = ""
    final_report: str = ""


# ── Flow ─────────────────────────────────────────────────────────────────────


class ResearchWritingFlow(Flow[ResearchState]):
    """Multi-step flow: Research Crew → Writing Crew."""

    def __init__(self, engine: OpenBoxEngine) -> None:
        super().__init__()
        self._openbox_engine = engine

    @start()
    def research_phase(self):
        """Kick off research crew — Senior Researcher + Data Analyst."""
        crew = ResearchCrew().crew()
        governed_crew = self._openbox_engine.govern(crew)
        result = governed_crew.kickoff(inputs={"topic": self.state.topic})
        self.state.research_results = result.raw
        return result

    @listen(research_phase)
    def writing_phase(self):
        """Kick off writing crew with research results."""
        crew = WritingCrew().crew()
        governed_crew = self._openbox_engine.govern(crew)
        result = governed_crew.kickoff(inputs={"findings": self.state.research_results})
        self.state.final_report = result.raw
        return result


# ── Async variant ────────────────────────────────────────────────────────────


class AsyncResearchWritingFlow(Flow[ResearchState]):
    """Async multi-step flow: Research Crew -> Writing Crew."""

    def __init__(self, engine: OpenBoxEngine) -> None:
        super().__init__()
        self._openbox_engine = engine

    @start()
    async def research_phase(self):
        crew = ResearchCrew().crew()
        governed_crew = self._openbox_engine.govern(crew)
        result = await governed_crew.akickoff(inputs={"topic": self.state.topic})
        self.state.research_results = result.raw
        return result

    @listen(research_phase)
    async def writing_phase(self):
        crew = WritingCrew().crew()
        governed_crew = self._openbox_engine.govern(crew)
        result = await governed_crew.akickoff(inputs={"findings": self.state.research_results})
        self.state.final_report = result.raw
        return result


# ── Entry points ─────────────────────────────────────────────────────────────


def kickoff():
    """Run the flow synchronously."""
    with create_openbox_engine() as engine:
        flow = create_openbox_flow(ResearchWritingFlow, engine=engine)

        try:
            flow.kickoff()
            print("\n" + "=" * 60)
            print("FLOW RESULT")
            print("=" * 60)
            print(f"Topic: {flow.state.topic}")
            print(f"\nFinal Report:\n{flow.state.final_report}")
        except GovernanceHaltError as e:
            print(f"\nGovernance blocked execution: {e}")
            if e.reason:
                print(f"Reason: {e.reason}")
            if e.policy_id:
                print(f"Policy: {e.policy_id}")


async def _async_main():
    with create_openbox_engine() as engine:
        flow = create_openbox_flow(AsyncResearchWritingFlow, engine=engine)

        try:
            await flow.akickoff()
            print("\n" + "=" * 60)
            print("ASYNC FLOW RESULT")
            print("=" * 60)
            print(f"Topic: {flow.state.topic}")
            print(f"\nFinal Report:\n{flow.state.final_report}")
        except GovernanceHaltError as e:
            print(f"\nGovernance blocked execution: {e}")
            if e.reason:
                print(f"Reason: {e.reason}")
            if e.policy_id:
                print(f"Policy: {e.policy_id}")


def kickoff_async():
    """Run the flow asynchronously (sync entry point for crewai run)."""
    asyncio.run(_async_main())


def plot():
    """Generate a visual diagram of the flow."""
    with create_openbox_engine() as engine:
        flow = create_openbox_flow(ResearchWritingFlow, engine=engine)
        flow.plot("demo_03_flow")


if __name__ == "__main__":
    kickoff()
