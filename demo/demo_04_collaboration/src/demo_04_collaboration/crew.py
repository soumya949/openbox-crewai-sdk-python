from crewai import Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from openbox import OpenBoxAgent, OpenBoxTask


@CrewBase
class CollaborationCrew:
    """Three governed agents with allow_delegation=True.

    The Content Writer owns the task and delegates research to the Research
    Specialist and editorial review to the Content Editor. Every
    ``execute_task`` call — including the delegated sub-tasks routed through
    CrewAI's DelegateWorkTool — is intercepted by OpenBox governance and
    sent with its agent's own credentials.
    """

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def researcher(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["researcher"],  # type: ignore[index]
            env_prefix="OPENBOX_RESEARCHER",
            allow_delegation=True,
        )

    @agent
    def writer(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["writer"],  # type: ignore[index]
            env_prefix="OPENBOX_WRITER",
            allow_delegation=True,
        )

    @agent
    def editor(self) -> OpenBoxAgent:
        return OpenBoxAgent(
            config=self.agents_config["editor"],  # type: ignore[index]
            env_prefix="OPENBOX_EDITOR",
            allow_delegation=True,
        )

    @task
    def collaborative_article(self) -> OpenBoxTask:
        return OpenBoxTask(
            config=self.tasks_config["collaborative_article"],  # type: ignore[index]
            activity_type="content_creation",
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            name="collaboration-demo",
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
        )
