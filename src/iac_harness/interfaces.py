"""Contratos mínimos entre o runner e as implementações concretas."""

from __future__ import annotations

from typing import Any, Protocol

from iac_harness.types import AgentReply, Task, Turn


class AgentBackend(Protocol):
    def generate(self, task: Task, history: list[Turn]) -> AgentReply: ...


class Recorder(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...
