"""Execução de artefatos por modalidade: comandos, resposta de consulta e herança de estado."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from iac_harness.executors.floci import FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS, TRUNCATION_MARK, strip_ansi
from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import Modality
from iac_harness.toolchain import Toolchain, remove_terraform_dir
from iac_harness.types import Artifact, CheckStatus
from iac_harness.workspace import create_workspace

# Arquivos do apply inicial herdados pelo workspace Terraform em modify/delete.
INHERITED_TF_FILES = ("terraform.tfstate", ".terraform.lock.hcl")
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
    """Mesma truncagem do feedback do agente do pipeline legado (tag v1.0.0)."""
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
    toolchain: Toolchain,
    classify: Callable[[str], CheckStatus],
    commands: tuple[tuple[str, ...], ...],
    path: Path,
    max_chars: int,
) -> ExecResult:
    """Roda os comandos em sequência e para no primeiro que falhar.

    `classify` é o classify_error do executor em uso (na AWS real nada vira UNSUPPORTED).
    """
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
                unsupported = classify(raw) is CheckStatus.UNSUPPORTED
                prefix = f"exit code {proc.returncode}\n"
                # Só a falha corta começo+fim: é a única saída que volta ao agente.
                message = truncate_middle(prefix + raw, max_chars)
                return ExecResult(False, message, unsupported, raw=prefix + "\n".join(outputs))
        full = "\n".join(outputs)
        return ExecResult(True, truncate(full, max_chars), raw=full)
    finally:
        # O provider copiado pelo init enche o disco.
        remove_terraform_dir(path, toolchain)


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
    result = _run_commands(toolchain, executor.classify_error, modality.command, ws.path, max_chars)
    return result, ws.path


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
    """Na consulta (ou com `answer`) e execução ok, coleta a resposta; se faltar, vira falha."""
    if not (result.ok and task.wants_answer):
        return result
    answer = collect_answer(path, modality)
    if answer.ok:
        return result
    return ExecResult(False, answer.output, raw=result.raw + "\n" + answer.output)


def inherits_state(task: ModalityTask, modality: Modality) -> bool:
    """Workspace Terraform herda o tfstate do initial só em modify/delete.

    Na consulta, o apply só com data sources destruiria o estado inicial. Com `hide_initial`, o
    agente não vê o initial.tf; herdar o tfstate faria o apply dele destruir o estado inicial.
    """
    return (
        modality.name == "terraform"
        and task.category in ("modify", "delete")
        and not task.hide_initial
    )
