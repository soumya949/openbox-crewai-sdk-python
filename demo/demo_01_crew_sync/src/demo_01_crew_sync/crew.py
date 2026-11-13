from crewai import Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from openbox import OpenBoxAgent, OpenBoxTask


@CrewBase
class ResearchCrew:
    """Single governed agent — every governance request carries the agent's own credentials."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def researcher(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["researcher"],  # type: ignore[index]
            env_prefix="OPENBOX_RESEARCHER",
        )

    @task
    def quick_research(self) -> OpenBoxTask:
        return OpenBoxTask(
            config=self.tasks_config["quick_research"],  # type: ignore[index]
            activity_type="research",
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            name="crew-sync-demo",
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
        )
