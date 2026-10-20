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

# Arquivos do apply inicial herdados pelo workspace Terraform em modify/delete.
INHERITED_TF_FILES = ("terraform.tfstate", ".terraform.lock.hcl")
CHECK_FILENAME = "check.py"
ANSWER_FILENAME = "answer.json"
# Erro fixo de resposta ausente/inválida nas consultas (volta ao agente como erro de execução).
ANSWER_ERROR = {
    "terraform": 'The output "answer" was not produced by the apply.',
    "cli": f"{ANSWER_FILENAME} was not produced or is not valid JSON.",
    "boto3": f"{ANSWER_FILENAME} was not produced or is not valid JSON.",
}


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    output: str  # stdout+stderr truncados (o que vai ao agente)
    unsupported: bool = False  # casou UNSUPPORTED_PATTERNS (o Floci não implementa a operação)
    raw: str = ""  # stdout+stderr completos de todos os comandos, sem corte (para análise)


def truncate(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Mesma truncagem do feedback do agente (feedback.build_feedback)."""
    text = strip_ansi(text).strip()
    return text[:max_chars].rstrip() + TRUNCATION_MARK if len(text) > max_chars else text


def truncate_middle(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Guarda começo e fim (metade de max_chars em cada ponta), como o mini-swe-agent.

    O erro do Terraform/CLI costuma ficar no fim de uma saída longa.
    """
    text = strip_ansi(text).strip()
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n[... {omitted} characters omitted ...]\n{text[-half:]}"


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
                prefix = f"exit code {proc.returncode}\n"
                # Só a falha corta começo+fim: é a única saída que volta ao agente.
                message = truncate_middle(prefix + raw, max_chars)
                return ExecResult(False, message, unsupported, raw=prefix + "\n".join(outputs))
        full = "\n".join(outputs)
        return ExecResult(True, truncate(full, max_chars), raw=full)
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
    rep: int = 0,
) -> tuple[ExecResult, Path]:
    """Grava o artefato em runs/<run_id>/<task>/<rep>/<group>/<name>/ e o executa.

    `group` é a modalidade avaliada (padrão: a do artefato). Na modalidade terraform, injeta o
    override do Floci e, com `inherit_from`, copia o tfstate e o lock de um apply anterior
    (modificação de verdade, não recriação).
    """
    artifact = Artifact({modality.filename: content})
    override = executor.override_for(artifact.files) if modality.name == "terraform" else None
    ws = create_workspace(
        runs_dir, run_id, task_id, rep, group or modality.name, name, artifact, override
    )
    if inherit_from is not None and modality.name == "terraform":
        for filename in INHERITED_TF_FILES:
            if (inherit_from / filename).exists():
                shutil.copy(inherit_from / filename, ws.path / filename)
    return _run_commands(toolchain, modality.command, ws.path, max_chars), ws.path


def collect_answer(path: Path, modality: Modality) -> ExecResult:
    """Resposta da consulta em <path>/answer.json, depois de uma execução ok.

    Terraform: o valor do output "answer" (string com JSON é decodificada). Equivale a
    `terraform output -json answer`, mas lido do terraform.tfstate, porque o .terraform (com os
    plugins que o comando exige) já foi apagado. CLI/boto3: o script grava o arquivo.
    """
    answer = path / ANSWER_FILENAME
    error = ExecResult(False, ANSWER_ERROR[modality.name])
    if modality.name == "terraform":
        try:
            state = json.loads((path / "terraform.tfstate").read_text())
            value = state["outputs"]["answer"]["value"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return error
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass  # string comum: vira string JSON e o check decide
        answer.write_text(json.dumps(value))
    try:
        json.loads(answer.read_text())
    except (OSError, ValueError):
        return error
    return ExecResult(True, "")


def with_answer(
    task: ModalityTask, modality: Modality, result: ExecResult, path: Path
) -> ExecResult:
    """Na consulta com execução ok, coleta a resposta; se faltar, a execução vira falha."""
    if not (result.ok and task.category == "query"):
        return result
    answer = collect_answer(path, modality)
    if answer.ok:
        return result
    return ExecResult(False, answer.output, raw=result.raw + "\n" + answer.output)


def inherits_state(task: ModalityTask, modality: Modality) -> bool:
    """Workspace Terraform herda o tfstate do initial só em modify/delete.

    Na consulta, o apply só com data sources destruiria o estado inicial.
    """
    return modality.name == "terraform" and task.category in ("modify", "delete")


def apply_initial(
    toolchain: Toolchain,
    executor: FlociExecutor,
    task: ModalityTask,
    modality: Modality,
    runs_dir: Path,
    run_id: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    rep: int = 0,
    name: str = "initial",
) -> tuple[ExecResult, Path]:
    """Aplica o initial.tf com Terraform, no workspace próprio <modalidade>/<name>."""
    if task.initial_tf is None:
        raise ValueError(f"{task.task_id}: tarefa sem estado inicial")
    return run_artifact(
        toolchain, executor, MODALITIES["terraform"], task.initial_tf, runs_dir, run_id,
        task.task_id, name, max_chars=max_chars, group=modality.name, rep=rep,
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


def run_check(
    toolchain: Toolchain, task: ModalityTask, workspace: Path | None = None
) -> tuple[bool, dict[str, Any]]:
    """Roda o check.py num diretório temporário FORA do workspace (como o OpaVerifier).

    Se `workspace` tiver answer.json (consulta), ele é copiado para o lado do check.
    """
    with tempfile.TemporaryDirectory(prefix="iac-check-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / CHECK_FILENAME).write_text(task.check)
        if workspace is not None and (workspace / ANSWER_FILENAME).is_file():
            shutil.copy(workspace / ANSWER_FILENAME, tmp_dir / ANSWER_FILENAME)
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
        if inherits_state(task, modality):
            inherit = initial_path
    row["baseline_pass"], row["baseline_details"] = run_check(toolchain, task)
    ref, ref_path = run_artifact(
        toolchain, executor, modality, task.references[modality.name], runs_dir, run_id,
        task.task_id, "reference", inherit, max_chars,
    )  # fmt: skip
    ref = with_answer(task, modality, ref, ref_path)
    row.update(ref_exec_ok=ref.ok, ref_output=ref.output, ref_unsupported_pattern=ref.unsupported)
    row["ref_check_pass"], row["ref_check_details"] = run_check(toolchain, task, ref_path)
    row["valid"] = row["baseline_pass"] is False and ref.ok and row["ref_check_pass"] is True
    return row
