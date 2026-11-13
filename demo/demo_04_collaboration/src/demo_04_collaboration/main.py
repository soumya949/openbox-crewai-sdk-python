#!/usr/bin/env python
"""Collaboration demo — three governed agents with delegation enabled.

Prerequisite: create the three agents in the OpenBox Platform and put each
one's API key and workload private key into ``.env`` under the matching env
prefix (``OPENBOX_RESEARCHER_``, ``OPENBOX_WRITER_``,
``OPENBOX_EDITOR_``).
"""

import logging

from dotenv import load_dotenv

from demo_04_collaboration.crew import CollaborationCrew
from openbox import create_openbox_engine

load_dotenv()

logging.basicConfig(level=logging.WARNING)


def run():
    """Run the governed collaboration crew."""
    with create_openbox_engine() as engine:
        crew = CollaborationCrew().crew()
        governed_crew = engine.govern(crew)
        result = governed_crew.kickoff()
        print(result)


if __name__ == "__main__":
    run()
