#!/usr/bin/env python
"""Sync crew demo — 1 governed agent sending governance requests with its own identity.

Prerequisite: create the researcher agent in the OpenBox Platform and put its
API key and workload private key into .env as OPENBOX_RESEARCHER_API_KEY and
OPENBOX_RESEARCHER_WORKLOAD_PRIVATE_KEY.
"""

import logging

from dotenv import load_dotenv

from demo_01_crew_sync.crew import ResearchCrew
from openbox import create_openbox_engine

load_dotenv()

logging.basicConfig(level=logging.INFO)


def run():
    """Run the governed crew."""
    with create_openbox_engine() as engine:
        crew = ResearchCrew().crew()
        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        print(result)


if __name__ == "__main__":
    run()
