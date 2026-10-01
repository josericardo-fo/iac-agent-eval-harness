"""Contratos mínimos entre o runner e as implementações concretas."""

from __future__ import annotations

from typing import Any, Protocol

from iac_harness.types import AgentReply, CheckResult, Stage, Task, TaskOracle, Turn
from iac_harness.workspace import Workspace


class AgentBackend(Protocol):
    def generate(self, task: Task, history: list[Turn]) -> AgentReply: ...


class DatasetAdapter(Protocol):
    def load(self) -> list[tuple[Task, TaskOracle]]: ...


class Verifier(Protocol):
    stage: Stage

    def check(self, workspace: Workspace, oracle: TaskOracle) -> CheckResult: ...


class Executor(Protocol):
    def apply(self, workspace: Workspace) -> CheckResult: ...

    def reset(self) -> None: ...


class Recorder(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...
