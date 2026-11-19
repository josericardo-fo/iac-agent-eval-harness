"""Backends (Floci, AWS real): o contrato comum e a limpeza final comum a todos."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from iac_harness.agentic.targets import Target
    from iac_harness.types import CheckStatus


class Backend(Protocol):
    """Onde a nuvem roda, para os dois pipelines (modalidades e agente).

    Cada nuvem é uma classe (`FlociExecutor`, `RealAwsExecutor`); `cli.common.EXECUTORS` as
    registra pelo `KIND`.
    """

    KIND: ClassVar[str]  # valor de `[sandbox] kind` e `sandbox_kind` da procedência
    NAME: ClassVar[str]  # nome nas mensagens
    TOOLCHAIN_TARGET: ClassVar[str]  # alvo da toolchain: credenciais, endpoint e rede

    def reset(self) -> None:
        """Estado inicial limpo; levanta se não conseguir (sandbox sujo não passa)."""
        ...

    def cleanup(self) -> None:
        """Limpeza final do run (via `final_cleanup`, num `finally`)."""
        ...

    def override_for(self, files: dict[str, str]) -> str | None:
        """Override de provider que aponta o Terraform para o endpoint; None = sem override."""
        ...

    def classify_error(self, output: str) -> CheckStatus: ...

    def provenance(self) -> dict[str, Any]:
        """Procedência gravada em `versions` (`sandbox_kind` e o que identifica o backend)."""
        ...

    def agent_target(self) -> Target:
        """O backend visto pelo container do agente: env/credenciais, redes, proxy, reset."""
        ...


def final_cleanup(executor: Any) -> None:
    """Limpeza ao fim do run (chamada num `finally`): na AWS real, apaga as sobras do último par.

    Se a limpeza falhar, o erro vai ao stderr; só sobe quando não há exceção original a preservar.
    """
    pending = sys.exc_info()[1] is not None
    try:
        executor.cleanup()
    except Exception as exc:
        print(f"falha na limpeza final ({executor.NAME}): {exc}", file=sys.stderr)
        if not pending:
            raise
