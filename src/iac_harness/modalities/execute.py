"""Execução de artefatos por modalidade, estado inicial e check de estado no Floci."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS, TRUNCATION_MARK, strip_ansi
from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import MODALITIES, Modality
from iac_harness.toolchain import Toolchain
from iac_harness.types import Artifact, CheckStatus
from iac_harness.workspace import create_workspace

# Arquivos do apply inicial herdados pelo workspace Terraform nas tarefas de modificação.
INHERITED_TF_FILES = ("terraform.tfstate", ".terraform.lock.hcl")
CHECK_FILENAME = "check.py"


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    output: str  # stdout+stderr truncados
    unsupported: bool = False  # casou UNSUPPORTED_PATTERNS (o Floci não implementa a operação)


def truncate(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Mesma truncagem do feedback do agente (feedback.build_feedback)."""
    text = strip_ansi(text).strip()
    return text[:max_chars].rstrip() + TRUNCATION_MARK if len(text) > max_chars else text


def _run_commands(
    toolchain: Toolchain, commands: tuple[tuple[str, ...], ...], path: Path, max_chars: int
) -> ExecResult:
    """Roda os comandos em sequência e para no primeiro que falhar."""
    outputs: list[str] = []
    try:
        for args in commands:
            try:
                proc = toolchain.run(list(args), path)
            except (subprocess.TimeoutExpired, OSError) as exc:
                return ExecResult(False, f"falha ao executar {args[0]}: {exc}")
            raw = proc.stdout + proc.stderr
            outputs.append(raw)
            if proc.returncode != 0:
                unsupported = FlociExecutor.classify_error(raw) is CheckStatus.UNSUPPORTED
                return ExecResult(False, truncate(raw, max_chars), unsupported)
        return ExecResult(True, truncate("\n".join(outputs), max_chars))
    finally:
        # Mesmo motivo de runner.run_gates: o provider copiado pelo init enche o disco.
        shutil.rmtree(path / ".terraform", ignore_errors=True)


def run_artifact(
    toolchain: Toolchain,
    executor: FlociExecutor,
    modality: Modality,
    content: str,
    runs_dir: Path,
    run_id: str,
    task_id: str,
    name: str,
    inherit_from: Path | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    group: str | None = None,
) -> tuple[ExecResult, Path]:
    """Grava o artefato em runs/<run_id>/<task>/0/<group>/<name>/ e o executa.

    `group` é a modalidade avaliada (padrão: a do artefato). Na modalidade terraform, injeta o
    override do Floci e, com `inherit_from`, copia o tfstate e o lock de um apply anterior
    (modificação de verdade, não recriação).
    """
    artifact = Artifact({modality.filename: content})
    override = executor.override_for(artifact.files) if modality.name == "terraform" else None
    ws = create_workspace(
        runs_dir, run_id, task_id, 0, group or modality.name, name, artifact, override
    )
    if inherit_from is not None and modality.name == "terraform":
        for filename in INHERITED_TF_FILES:
            if (inherit_from / filename).exists():
                shutil.copy(inherit_from / filename, ws.path / filename)
    return _run_commands(toolchain, modality.command, ws.path, max_chars), ws.path


def apply_initial(
    toolchain: Toolchain,
    executor: FlociExecutor,
    task: ModalityTask,
    modality: Modality,
    runs_dir: Path,
    run_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> tuple[ExecResult, Path]:
    """Aplica o initial.tf com Terraform, no workspace próprio <modalidade>/initial."""
    if task.initial_tf is None:
        raise ValueError(f"{task.task_id}: tarefa sem estado inicial")
    return run_artifact(
        toolchain, executor, MODALITIES["terraform"], task.initial_tf, runs_dir, run_id,
        task.task_id, "initial", max_chars=max_chars, group=modality.name,
    )  # fmt: skip


def parse_check_output(stdout: str) -> tuple[bool, dict[str, Any]]:
    """Última linha JSON `{"pass": bool, "details": {...}}`; qualquer outra coisa reprova."""
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        return False, {"error": "check sem saída"}
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return False, {"error": f"saída do check não é JSON: {exc}", "output": stdout[-500:]}
    if not isinstance(data, dict) or not isinstance(data.get("pass"), bool):
        return False, {"error": "saída do check sem campo booleano 'pass'", "output": lines[-1]}
    details = data.get("details")
    return data["pass"], details if isinstance(details, dict) else {"details": details}


def run_check(toolchain: Toolchain, task: ModalityTask) -> tuple[bool, dict[str, Any]]:
    """Roda o check.py num diretório temporário FORA do workspace (como o OpaVerifier)."""
    with tempfile.TemporaryDirectory(prefix="iac-check-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / CHECK_FILENAME).write_text(task.check)
        try:
            proc = toolchain.run(["python3", CHECK_FILENAME, task.region], tmp_dir)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, {"error": f"falha ao executar o check: {exc}"}
    passed, details = parse_check_output(proc.stdout)
    if proc.returncode != 0:
        details = {**details, "returncode": proc.returncode, "stderr": proc.stderr[-500:]}
        passed = False
    return passed, details


def prevalidate_pair(
    task: ModalityTask,
    modality: Modality,
    toolchain: Toolchain,
    executor: FlociExecutor,
    runs_dir: Path,
    run_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Uma tarefa × modalidade: estado inicial → check (deve reprovar) → referência → check.

    Quem chama faz o `executor.reset()` antes.
    """
    row: dict[str, Any] = {
        "task_id": task.task_id, "category": task.category, "modality": modality.name,
        "initial_ok": None, "initial_output": "", "baseline_pass": None, "baseline_details": {},
        "ref_exec_ok": None, "ref_output": "", "ref_unsupported_pattern": None,
        "ref_check_pass": None, "ref_check_details": {}, "valid": False,
    }  # fmt: skip
    inherit = None
    if task.initial_tf is not None:
        initial, initial_path = apply_initial(
            toolchain, executor, task, modality, runs_dir, run_id, max_chars
        )
        row.update(initial_ok=initial.ok, initial_output=initial.output)
        if not initial.ok:
            return row  # sem estado inicial não há o que pré-validar
        inherit = initial_path if modality.name == "terraform" else None
    row["baseline_pass"], row["baseline_details"] = run_check(toolchain, task)
    ref, _ = run_artifact(
        toolchain, executor, modality, task.references[modality.name], runs_dir, run_id,
        task.task_id, "reference", inherit, max_chars,
    )  # fmt: skip
    row.update(ref_exec_ok=ref.ok, ref_output=ref.output, ref_unsupported_pattern=ref.unsupported)
    row["ref_check_pass"], row["ref_check_details"] = run_check(toolchain, task)
    row["valid"] = row["baseline_pass"] is False and ref.ok and row["ref_check_pass"] is True
    return row
