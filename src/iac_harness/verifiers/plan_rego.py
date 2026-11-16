"""Oráculo do Ambig-IaC (E16): `terraform plan` dos arquivos finais do agente + OPA no `data`.

Os arquivos do /work do agente são copiados para um diretório limpo (sem estado, lock, `.terraform`
nem o override do agente), com o override do Floci do harness; o plan usa o `PlanVerifier` do
A/B/C. A regra de aprovação é a do avaliador do IaC-Eval (de onde vêm os `checks_rego`): `opa eval
data` e reprova se alguma folha do documento for `false` (aqui só booleanos; o IaC-Eval, em Python,
também reprovaria um `0`). Os pacotes variam por tarefa e não há regra `main`, por isso não se usa
o `OpaVerifier` (`data.terraform.policy.main`). Políticas sem `import rego.v1` são Rego v0: com o
OPA 1.x da toolchain, rodam com `--v0-compatible` (o IaC-Eval usava OPA 0.x).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain, remove_terraform_dir
from iac_harness.types import CheckStatus, TaskOracle
from iac_harness.verifiers.terraform import PLAN_JSON, PlanVerifier
from iac_harness.workspace import OVERRIDE_FILENAME, Workspace

# Mesmo nome de agentic.runner.AGENT_OVERRIDE_FILENAME (um teste confere; importar daria ciclo).
AGENT_OVERRIDE_FILENAME = "zz_provider_override.tf"
_SKIPPED_DIRS = {".terraform"}
_SKIPPED_FILES = {".terraform.lock.hcl", AGENT_OVERRIDE_FILENAME, OVERRIDE_FILENAME, "tfplan",
                  PLAN_JSON, "answer.json"}  # fmt: skip
MAX_ERROR_CHARS = 1500


def _skipped(name: str) -> bool:
    return name in _SKIPPED_FILES or name.endswith(".tfstate") or ".tfstate." in name


def _config_files(work: Path) -> list[Path]:
    """Arquivos a copiar (relativos ao /work), sem links e sem estado/override/.terraform."""
    found = []
    for dirpath, dirnames, filenames in os.walk(work):
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS
                       and not (Path(dirpath) / d).is_symlink()]  # fmt: skip
        for name in filenames:
            path = Path(dirpath) / name
            if not path.is_symlink() and not _skipped(name):
                found.append(path.relative_to(work))
    return sorted(found)


def opa_args(rego: str) -> list[str]:
    """`opa eval` do pacote inteiro (como o IaC-Eval), v0 salvo com `import rego.v1`."""
    compat = [] if "import rego.v1" in rego else ["--v0-compatible"]
    return ["opa", "eval", *compat, "-d", "policy.rego", "-i", PLAN_JSON, "data",
            "--format", "json"]  # fmt: skip


def false_rules(value: Any, prefix: str = "") -> list[str]:
    """Caminhos (a.b.c) das folhas `false` do documento `data` avaliado."""
    if value is False:
        return [prefix]
    if isinstance(value, dict):
        return [
            p for k, v in value.items() for p in false_rules(v, f"{prefix}.{k}" if prefix else k)
        ]
    if isinstance(value, list):
        return [p for v in value for p in false_rules(v, prefix)]
    return []


def grade_config(toolchain: Toolchain, rego: str, work: Path | None) -> tuple[bool, dict[str, Any]]:
    """(passou, detalhes); `reason`: no_tf | plan_failed | rego_pass | rego_fail | oracle_error."""
    files = _config_files(work) if work is not None and work.is_dir() else []
    if not any(f.suffix == ".tf" and len(f.parts) == 1 for f in files):
        return False, {"reason": "no_tf"}
    if toolchain.target != "emulator":
        return False, {"reason": "oracle_error", "error": "oráculo Ambig-IaC só no emulador"}
    with tempfile.TemporaryDirectory(prefix="iac-ambig-", ignore_cleanup_errors=True) as tmp:
        config, opa_dir = Path(tmp) / "config", Path(tmp) / "opa"
        try:
            return _grade(toolchain, rego, work, files, config, opa_dir)
        finally:
            remove_terraform_dir(config, toolchain)


def _grade(
    toolchain: Toolchain, rego: str, work: Path, files: list[Path], config: Path, opa_dir: Path
) -> tuple[bool, dict[str, Any]]:
    for rel in files:
        (config / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / rel, config / rel)
    tf = {f.name: (config / f).read_text(errors="replace") for f in files if len(f.parts) == 1
          and f.suffix == ".tf"}  # fmt: skip
    override = FlociExecutor(toolchain, endpoint=toolchain.endpoint).override_for(tf)
    (config / OVERRIDE_FILENAME).write_text(override)
    plan = PlanVerifier(toolchain).check(Workspace(config, ""), TaskOracle({}, rego))
    if plan.status is not CheckStatus.PASS:
        reason = "plan_failed" if plan.status is CheckStatus.FAIL else "oracle_error"
        return False, {"reason": reason, "error": plan.message[:MAX_ERROR_CHARS]}
    opa_dir.mkdir()
    (opa_dir / "policy.rego").write_text(rego)
    shutil.copyfile(config / PLAN_JSON, opa_dir / PLAN_JSON)
    try:
        proc = toolchain.run(opa_args(rego), opa_dir)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[:500])
        results = json.loads(proc.stdout).get("result") or []
        value = results[0]["expressions"][0]["value"] if results else {}
    except (subprocess.TimeoutExpired, OSError, RuntimeError, ValueError, KeyError) as exc:
        return False, {"reason": "oracle_error", "error": f"opa: {exc}"[:MAX_ERROR_CHARS]}
    failed = false_rules(value)
    return not failed, {"reason": "rego_fail" if failed else "rego_pass", "failed_rules": failed}
