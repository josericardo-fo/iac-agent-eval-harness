"""Verificador OPA: intenção via `data.terraform.policy.main`, com diagnóstico controlado.

A política fica num diretório temporário FORA do workspace do agente; o texto só chega ao
feedback com policy_visibility="full".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckResult, CheckStatus, Stage, TaskOracle
from iac_harness.verifiers.terraform import PLAN_JSON
from iac_harness.workspace import Workspace

VISIBILITIES = ("rule_names", "none", "full")
PACKAGE_QUERY = "data.terraform.policy"
_IGNORED = {"main", "default"}
_RULE_HEAD = re.compile(r"^(\w+)\s+(?:if\b|:=|=)")
_DEFAULT_RULE = re.compile(r"^default\s+(\w+)\s*(?::=|=)")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|`[^`]*`')


def extract_rule_names(rego: str) -> list[str]:
    """Regras sem argumento definidas no nível do módulo (exceto `main`), na ordem do texto.

    Funções `nome(args)` não casam (exigem `if`/`:=`/`=` logo após o nome) e variáveis locais
    dentro de corpos `{}` são ignoradas pelo controle de profundidade.
    """
    names: list[str] = []
    depth = 0
    for line in rego.splitlines():
        code = _STRING.sub('""', line.split("#", 1)[0])
        if depth == 0:
            m = _DEFAULT_RULE.match(code) or _RULE_HEAD.match(code)
            if m and m.group(1) not in _IGNORED and m.group(1) not in names:
                names.append(m.group(1))
        depth += code.count("{") - code.count("}")
        depth = max(depth, 0)
    return names


class OpaVerifier:
    stage = Stage.OPA

    def __init__(self, toolchain: Toolchain, policy_visibility: str = "rule_names") -> None:
        if policy_visibility not in VISIBILITIES:
            raise ValueError(f"policy_visibility inválido: {policy_visibility!r}")
        self._tc = toolchain
        self.policy_visibility = policy_visibility

    def _eval(self, query: str, cwd: Path) -> dict:
        proc = self._tc.run(
            ["opa", "eval", "-d", "policy.rego", "-i", PLAN_JSON, query, "--format", "json"], cwd
        )
        if proc.returncode != 0:
            raise RuntimeError(f"opa eval falhou: {(proc.stderr or proc.stdout).strip()[:500]}")
        return json.loads(proc.stdout)

    @staticmethod
    def _value(output: dict):
        """Valor da expressão; None quando indefinida (sem chave `result`)."""
        results = output.get("result")
        return results[0]["expressions"][0]["value"] if results else None

    def check(self, workspace: Workspace, oracle: TaskOracle) -> CheckResult:
        start = time.monotonic()

        def result(status: CheckStatus, message: str = "", raw: str = "", **meta) -> CheckResult:
            return CheckResult(self.stage, status, message, raw, meta, time.monotonic() - start)

        plan = workspace.path / PLAN_JSON
        if not plan.exists():
            return result(CheckStatus.ERROR, f"{PLAN_JSON} ausente: o plan não rodou")
        with tempfile.TemporaryDirectory(prefix="iac-opa-") as tmp:
            tmp_dir = Path(tmp)
            (tmp_dir / "policy.rego").write_text(oracle.rego_policy)
            shutil.copy(plan, tmp_dir / PLAN_JSON)
            try:
                main = self._value(self._eval(f"{PACKAGE_QUERY}.main", tmp_dir))
                if main is True:
                    return result(CheckStatus.PASS)
                # main indefinido ou falso = FAIL.
                message = self._failure_message(oracle, tmp_dir)
            except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError) as exc:
                return result(CheckStatus.ERROR, f"falha ao executar opa: {exc}")
        return result(CheckStatus.FAIL, message, meta_visibility=self.policy_visibility)

    def _failure_message(self, oracle: TaskOracle, cwd: Path) -> str:
        none_msg = "The plan does not satisfy the intent specification."
        if self.policy_visibility == "none":
            return none_msg
        if self.policy_visibility == "full":
            return f"{none_msg}\n\nPolicy (Rego):\n{oracle.rego_policy}"
        names = extract_rule_names(oracle.rego_policy)
        if not names:
            return none_msg
        # Uma avaliação do pacote inteiro: regras indefinidas não aparecem no documento.
        doc = self._value(self._eval(PACKAGE_QUERY, cwd)) or {}
        # Regras de valor não booleano (conjuntos etc.) estão definidas: só falso/indefinido conta.
        unmet = [n for n in names if doc.get(n) is None or doc.get(n) is False]
        if not unmet:
            return none_msg
        return f"Unsatisfied intent rules: {', '.join(unmet)}"
