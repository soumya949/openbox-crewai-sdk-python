from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

import pytest


@pytest.fixture(autouse=True, scope="session")
def _configure_crewai_storage(tmp_path_factory: pytest.TempPathFactory) -> None:
    home_dir = tmp_path_factory.mktemp("crewai-home")
    data_home = home_dir / ".local" / "share"
    data_home.mkdir(parents=True, exist_ok=True)

    old_home = os.environ.get("HOME")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    old_storage = os.environ.get("CREWAI_STORAGE_DIR")

    os.environ["HOME"] = str(home_dir)
    os.environ["XDG_DATA_HOME"] = str(data_home)
    os.environ["CREWAI_STORAGE_DIR"] = "openbox-crewai-sdk-tests"
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home

        if old_xdg is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = old_xdg

        if old_storage is None:
            os.environ.pop("CREWAI_STORAGE_DIR", None)
        else:
            os.environ["CREWAI_STORAGE_DIR"] = old_storage


@pytest.fixture
def capture_engine_evaluations(monkeypatch: pytest.MonkeyPatch):
    """Record every evaluate call any governed agent sends (all routes)."""
    from openbox._clients import AgentClient

    def _install(engine: Any) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []
        original_evaluate = AgentClient.evaluate
        original_aevaluate = AgentClient.aevaluate

        def wrapped_evaluate(self, payload):  # type: ignore[no-untyped-def]
            captured.append(
                {"mode": "sync", "payload": deepcopy(payload), "api_key": self.config.api_key}
            )
            return original_evaluate(self, payload)

        async def wrapped_aevaluate(self, payload):  # type: ignore[no-untyped-def]
            captured.append(
                {"mode": "async", "payload": deepcopy(payload), "api_key": self.config.api_key}
            )
            return await original_aevaluate(self, payload)

        monkeypatch.setattr(AgentClient, "evaluate", wrapped_evaluate)
        monkeypatch.setattr(AgentClient, "aevaluate", wrapped_aevaluate)
        return captured

    return _install
