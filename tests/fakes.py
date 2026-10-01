"""Fakes para testar o runner sem Docker, rede nem SDK."""

from __future__ import annotations

from iac_harness.types import (
    AgentReply,
    Artifact,
    CheckResult,
    CheckStatus,
    Stage,
    Task,
    TaskOracle,
    Turn,
    Usage,
)
from iac_harness.workspace import Workspace


class FakeBackend:
    """Gera main.tf com o número da chamada; guarda o histórico recebido em cada chamada."""

    def __init__(self) -> None:
        self.histories: list[list[Turn]] = []

    @property
    def calls(self) -> int:
        return len(self.histories)

    def generate(self, task: Task, history: list[Turn]) -> AgentReply:
        self.histories.append(list(history))
        body = f"# tentativa {len(self.histories)}\n"
        return AgentReply(
            artifact=Artifact({"main.tf": body}),
            usage=Usage(10, 5),
            model_id="fake-model",
            request_id=f"req-{len(self.histories)}",
            params={"temperature": None},
        )


class FakeVerifier:
    """Falha enquanto o conteúdo do main.tf não atingir `pass_from` (número da tentativa)."""

    def __init__(
        self,
        stage: Stage,
        pass_from: int = 1,
        fail_status: CheckStatus = CheckStatus.FAIL,
        message: str = "erro",
    ) -> None:
        self.stage = stage
        self.pass_from = pass_from
        self.fail_status = fail_status
        self.message = message
        self.calls = 0

    def check(self, workspace: Workspace, oracle: TaskOracle) -> CheckResult:
        self.calls += 1
        n = int((workspace.path / "main.tf").read_text().split()[-1])
        if n >= self.pass_from:
            return CheckResult(self.stage, CheckStatus.PASS)
        return CheckResult(self.stage, self.fail_status, message=self.message, raw=self.message)


class FakeExecutor:
    """Executor com a mesma regra do FakeVerifier; conta resets."""

    def __init__(
        self, pass_from: int = 1, fail_status: CheckStatus = CheckStatus.FAIL, message: str = "x"
    ) -> None:
        self._inner = FakeVerifier(Stage.SANDBOX, pass_from, fail_status, message)
        self.resets = 0

    @property
    def applies(self) -> int:
        return self._inner.calls

    def apply(self, workspace: Workspace) -> CheckResult:
        return self._inner.check(workspace, TaskOracle({}, ""))

    def reset(self) -> None:
        self.resets += 1
