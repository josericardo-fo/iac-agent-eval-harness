"""Contratos mínimos entre o runner e as implementações concretas."""

from __future__ import annotations

from typing import Any, Protocol

from iac_harness.types import AgentReply, Task, Turn


class ModelBackend(Protocol):
    """Provedor de modelo do pipeline de modalidades (não confundir com o `executors.Backend`)."""

    def generate(self, task: Task, history: list[Turn]) -> AgentReply: ...


class Recorder(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...
