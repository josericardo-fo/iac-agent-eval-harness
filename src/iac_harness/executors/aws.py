"""Executor da AWS real: sem override, guarda de identidade e reset por limpeza de inventário."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from iac_harness.executors.aws_sweep import ORDER
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckStatus

__all__ = ["RealAwsExecutor"]

SWEEP_SCRIPT = Path(__file__).with_name("aws_sweep.py")
SWEEP_FILENAME = "aws_sweep.py"
BASELINE_FILENAME = "baseline.json"
# Baseline persistente (conta limpa): sobrevive a quedas, para o --resume apagar as sobras.
DEFAULT_BASELINE_FILE = "runs/aws-baseline.json"


def _last_json(proc: subprocess.CompletedProcess[str], what: str) -> Any:
    """Última linha JSON do stdout (ou o stdout inteiro, se o JSON ocupa várias linhas, como no
    `aws ... --output json`); levanta se o comando falhou ou a saída não é JSON."""
    lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        raise RuntimeError(f"{what} falhou ({proc.returncode}): {proc.stderr.strip()[-500:]}")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{what}: saída não é JSON ({exc}): {lines[-1][:500]}") from exc


class RealAwsExecutor(FlociExecutor):
    KIND = "aws"
    NAME = "AWS"

    def __init__(
        self,
        toolchain: Toolchain,
        expected_arn: str | None = None,
        baseline_file: str | Path = DEFAULT_BASELINE_FILE,
    ) -> None:
        # Sem endpoint nem container: não chama o __init__ do Floci (allowlist de emulador).
        if not expected_arn:
            raise ValueError("[sandbox] expected_arn é obrigatório com kind = 'aws'")
        if toolchain.target != "aws":
            raise ValueError(
                f"RealAwsExecutor exige toolchain com target 'aws': {toolchain.target!r}"
            )
        self.expected_arn = expected_arn
        self.region = toolchain.region
        self.endpoint = self.host_endpoint = self.container = ""
        self._tc = toolchain
        self._identity: dict[str, str] | None = None
        self.baseline_file = Path(baseline_file)
        self._baseline: dict[str, list[str]] | None = None

    def override_for(self, files: dict[str, str]) -> None:
        """Nenhum override: o Terraform fala com a AWS de verdade."""
        return None

    @staticmethod
    def classify_error(output: str) -> CheckStatus:
        """Na AWS real, "não suportado" é erro de verdade: nunca UNSUPPORTED."""
        return CheckStatus.FAIL

    def identity(self) -> dict[str, str]:
        """`sts get-caller-identity` na toolchain; recusa ARN diferente de `expected_arn`."""
        if self._identity is None:
            with tempfile.TemporaryDirectory(prefix="iac-aws-") as tmp:
                proc = self._tc.run(
                    ["aws", "sts", "get-caller-identity", "--output", "json"], Path(tmp)
                )
            data = _last_json(proc, "aws sts get-caller-identity")
            arn = data.get("Arn") if isinstance(data, dict) else None
            if arn != self.expected_arn:
                raise RuntimeError(
                    f"identidade AWS recusada: {arn!r} (esperado {self.expected_arn!r})"
                )
            self._identity = {"account": data.get("Account", ""), "arn": arn}
        return self._identity

    def _load_baseline(self, snapshot: bool) -> bool:
        """Carrega o baseline de `baseline_file`; sem arquivo e com `snapshot`, tira e grava.

        Baseline gravado antes de novas categorias do sweep (ORDER) levanta erro: categoria
        ausente valeria como vazia e o sweep apagaria tudo dela, e preenchê-la com um inventário
        da conta naquele momento tornaria baseline as sobras de uma conta suja. O operador
        estende o arquivo à mão, a partir de um inventário (só leitura) da conta limpa.

        Devolve se há baseline.
        """
        if self._baseline is None:
            if self.baseline_file.is_file():
                self._baseline = json.loads(self.baseline_file.read_text())
                self._check_baseline(self._baseline)
            elif snapshot:
                self._baseline = self._run_sweep(["inventory"], "inventário AWS")
                self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
                self.baseline_file.write_text(json.dumps(self._baseline, indent=2))
        return self._baseline is not None

    def _check_baseline(self, baseline: dict[str, list[str]]) -> None:
        missing = [c for c in ORDER if c not in baseline]
        if missing:
            raise RuntimeError(
                f"baseline {self.baseline_file} sem as categorias {missing}: estenda-o à mão a"
                " partir de um inventário (só leitura) da conta limpa (README, seção AWS real)"
            )

    def reset(self) -> None:
        """Apaga tudo o que não estava no baseline (arquivo; sem ele, tirado agora e gravado).

        Falha de propósito com exceção se sobrar algo fora do baseline, como o Floci com o
        sandbox sujo.
        """
        self.identity()
        self._load_baseline(snapshot=True)
        self._sweep()

    def cleanup(self) -> None:
        """Limpeza final do run: sweep contra o baseline, se houver um (não tira baseline novo)."""
        if self._load_baseline(snapshot=False):
            self.identity()
            self._sweep()

    def _sweep(self) -> None:
        result = self._run_sweep(["sweep", BASELINE_FILENAME], "limpeza AWS")
        print(f"aws sweep: {json.dumps(result, ensure_ascii=False)}", file=sys.stderr)
        remaining = {k: v for k, v in result.get("remaining", {}).items() if v}
        if remaining:
            raise RuntimeError(f"limpeza AWS incompleta, sobrou fora do baseline: {remaining}")

    def _run_sweep(self, args: list[str], what: str) -> Any:
        """Roda o aws_sweep.py (boto3 da imagem) num diretório temporário, com o baseline."""
        with tempfile.TemporaryDirectory(prefix="iac-aws-") as tmp:
            tmp_dir = Path(tmp)
            (tmp_dir / SWEEP_FILENAME).write_text(SWEEP_SCRIPT.read_text())
            (tmp_dir / BASELINE_FILENAME).write_text(json.dumps(self._baseline or {}))
            proc = self._tc.run(["python3", SWEEP_FILENAME, *args], tmp_dir)
        return _last_json(proc, what)
