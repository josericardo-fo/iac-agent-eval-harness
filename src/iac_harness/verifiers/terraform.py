"""Verificador baseado em terraform: plan via Floci (com o override) e erros do CLI normalizados."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from iac_harness.feedback import strip_ansi
from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckResult, CheckStatus, Stage, TaskOracle
from iac_harness.workspace import Workspace

# Bloco de erro do CLI: "Error: resumo" seguido das linhas até o próximo bloco.
_ERROR_BLOCK = re.compile(r"(?:^|\n)(?:│ )?(Error: .*?)(?=\n(?:│ )?Error: |\Z)", re.DOTALL)
PLAN_JSON = "plan.json"
PLAN_FILE = "tfplan"


def _clean(text: str) -> str:
    return strip_ansi(text)


def error_blocks(stderr: str) -> str:
    """Normaliza a saída de erro do CLI: só os blocos `Error:`, sem a moldura de caixa."""
    text = _clean(stderr)
    blocks = [
        re.sub(r"^[│╷╵ ]+", "", b, flags=re.MULTILINE).strip() for b in _ERROR_BLOCK.findall(text)
    ]
    blocks = [b for b in blocks if b]
    return "\n\n".join(blocks) if blocks else text.strip()


def _run(toolchain: Toolchain, args: list[str], path: Path) -> subprocess.CompletedProcess[str]:
    return toolchain.run(args, path)


def _init(toolchain: Toolchain, path: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        toolchain, ["terraform", "init", "-backend=false", "-input=false", "-no-color"], path
    )


class PlanVerifier:
    """`plan` contra o Floci (o override é injetado no workspace); gera tfplan e plan.json."""

    stage = Stage.PLAN

    def __init__(self, toolchain: Toolchain) -> None:
        self._tc = toolchain

    def check(self, workspace: Workspace, oracle: TaskOracle) -> CheckResult:
        start = time.monotonic()

        def result(status: CheckStatus, message: str = "", raw: str = "", **meta) -> CheckResult:
            return CheckResult(self.stage, status, message, raw, meta, time.monotonic() - start)

        path = workspace.path
        try:
            if not (path / ".terraform").exists():
                init = _init(self._tc, path)
                if init.returncode != 0:
                    return result(
                        CheckStatus.FAIL, error_blocks(init.stderr), init.stdout + init.stderr
                    )
            plan = _run(
                self._tc,
                ["terraform", "plan", f"-out={PLAN_FILE}", "-input=false", "-no-color"],
                path,
            )
            raw = plan.stdout + plan.stderr
            if plan.returncode != 0:
                return result(CheckStatus.FAIL, error_blocks(plan.stderr), raw)
            show = _run(self._tc, ["terraform", "show", "-json", PLAN_FILE], path)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return result(CheckStatus.ERROR, f"falha ao executar terraform: {exc}")
        if show.returncode != 0:
            return result(CheckStatus.ERROR, error_blocks(show.stderr), raw + show.stderr)
        (path / PLAN_JSON).write_text(show.stdout)
        return result(CheckStatus.PASS, raw=raw)
