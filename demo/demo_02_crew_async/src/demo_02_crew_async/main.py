#!/usr/bin/env python
"""Async crew demo — same governed researcher as demo_01_crew_sync, driven via akickoff().

Prerequisite: provision an identity for the researcher agent in the OpenBox
Platform and paste the DID + private key into .env as OPENBOX_RESEARCHER_DID
and OPENBOX_RESEARCHER_PRIVATE_KEY.
"""

import asyncio
import logging

from dotenv import load_dotenv

from demo_02_crew_async.crew import ResearchCrew
from openbox import create_openbox_engine
from openbox.errors import GovernanceHaltError

load_dotenv()

logging.basicConfig(level=logging.WARNING)


async def _async_run():
    with create_openbox_engine() as engine:
        crew = ResearchCrew().crew()
        governed_crew = engine.govern(crew)
        try:
            result = await governed_crew.akickoff()
            print("\n" + "=" * 60)
            print("ASYNC CREW RESULT")
            print("=" * 60)
            print(result)
        except GovernanceHaltError as e:
            print(f"\nGovernance blocked execution: {e}")
            if e.reason:
                print(f"Reason: {e.reason}")
            if e.policy_id:
                print(f"Policy: {e.policy_id}")


def run():
    """Run the governed crew asynchronously (sync entry point for crewai run)."""
    asyncio.run(_async_run())


if __name__ == "__main__":
    run()
