"""Execução de artefatos por modalidade, estado inicial e check de estado no Floci."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS, TRUNCATION_MARK, strip_ansi
from iac_harness.modalities.loader import UPSTREAM_DIRNAME, ModalityTask
from iac_harness.modalities.spec import MODALITIES, Modality
from iac_harness.toolchain import Toolchain, remove_terraform_dir
from iac_harness.types import Artifact, CheckStatus
from iac_harness.workspace import create_workspace

# Arquivos do apply inicial herdados pelo workspace Terraform em modify/delete.
INHERITED_TF_FILES = ("terraform.tfstate", ".terraform.lock.hcl")
CHECK_FILENAME = "check.py"
COLLATERAL_FILENAME = "collateral.py"
ANSWER_FILENAME = "answer.json"
# Erro fixo de resposta ausente/inválida nas consultas (volta ao agente como erro de execução).
ANSWER_ERROR = {
    "terraform": 'The output "answer" was not produced by the apply.',
    "cli": f"{ANSWER_FILENAME} was not produced or is not valid JSON.",
    "boto3": f"{ANSWER_FILENAME} was not produced or is not valid JSON.",
}
# Consistência eventual na AWS real: o check pós-solução é refeito até passar dentro da janela.
CHECK_SETTLE_WINDOW_S = 60
CHECK_SETTLE_INTERVAL_S = 10


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
        # Mesmo motivo de runner.run_gates: o provider copiado pelo init enche o disco.
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


def last_json_line(stdout: str) -> tuple[str | None, Any]:
    """(última linha não vazia, valor JSON dela); linha None se não há saída.

    Levanta json.JSONDecodeError se a última linha não for JSON.
    """
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        return None, None
    return lines[-1], json.loads(lines[-1])


def parse_check_output(stdout: str) -> tuple[bool, dict[str, Any]]:
    """Última linha JSON `{"pass": bool, "details": {...}}`; qualquer outra coisa reprova."""
    try:
        line, data = last_json_line(stdout)
    except json.JSONDecodeError as exc:
        return False, {"error": f"saída do check não é JSON: {exc}", "output": stdout[-500:]}
    if line is None:
        return False, {"error": "check sem saída"}
    if not isinstance(data, dict) or not isinstance(data.get("pass"), bool):
        return False, {"error": "saída do check sem campo booleano 'pass'", "output": line}
    details = data.get("details")
    return data["pass"], details if isinstance(details, dict) else {"details": details}


def run_check(
    toolchain: Toolchain,
    task: ModalityTask,
    workspace: Path | None = None,
    settle: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Roda o check.py num diretório temporário FORA do workspace (como o OpaVerifier).

    Se `workspace` tiver answer.json (consulta), ele é copiado para o lado do check.
    Com `settle` no alvo aws, um check reprovado é refeito a cada CHECK_SETTLE_INTERVAL_S
    por até CHECK_SETTLE_WINDOW_S (consistência eventual); `check_attempts` vai nos detalhes.
    Só para checks pós-solução: o baseline deve reprovar e não pode ser refeito.
    """
    if not (settle and toolchain.target == "aws"):
        return _run_check_once(toolchain, task, workspace)
    max_attempts = 1 + CHECK_SETTLE_WINDOW_S // CHECK_SETTLE_INTERVAL_S
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            time.sleep(CHECK_SETTLE_INTERVAL_S)
        passed, details = _run_check_once(toolchain, task, workspace)
        if passed:
            break
    return passed, {**details, "check_attempts": attempt}


def _run_task_script(
    toolchain: Toolchain, task: ModalityTask, filename: str, code: str, workspace: Path | None
) -> subprocess.CompletedProcess | str:
    """Roda `python3 <filename> <region>` num diretório temporário FORA do workspace.

    Mesmo ambiente do check: upstream/ ao lado e answer.json do workspace, se houver. Devolve o
    processo, ou a mensagem de erro se nem foi possível executar.
    """
    with tempfile.TemporaryDirectory(prefix="iac-check-") as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / filename).write_text(code)
        if task.upstream:
            # Check original do dataset, usado pelo check.py adaptador da tarefa.
            (tmp_dir / UPSTREAM_DIRNAME).mkdir()
            for name, content in task.upstream.items():
                (tmp_dir / UPSTREAM_DIRNAME / name).write_text(content)
        if workspace is not None and (workspace / ANSWER_FILENAME).is_file():
            shutil.copy(workspace / ANSWER_FILENAME, tmp_dir / ANSWER_FILENAME)
        try:
            return toolchain.run(["python3", filename, task.region], tmp_dir)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return str(exc)


def _run_check_once(
    toolchain: Toolchain, task: ModalityTask, workspace: Path | None
) -> tuple[bool, dict[str, Any]]:
    proc = _run_task_script(toolchain, task, CHECK_FILENAME, task.check, workspace)
    if isinstance(proc, str):
        return False, {"error": f"falha ao executar o check: {proc}"}
    passed, details = parse_check_output(proc.stdout)
    if proc.returncode != 0:
        details = {**details, "returncode": proc.returncode, "stderr": proc.stderr[-500:]}
        passed = False
    return passed, details


def run_snapshot(
    toolchain: Toolchain, task: ModalityTask
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Retrato do estado pelo collateral.py da tarefa (mesmo contrato do check, sem settle).

    Última linha JSON `{"snapshot": {...}}`. Devolve (retrato, {}) ou (None, erro): falha do
    retrato não é efeito colateral, vira `collateral_ok` None.
    """
    proc = _run_task_script(toolchain, task, COLLATERAL_FILENAME, task.collateral, None)
    if isinstance(proc, str):
        return None, {"error": f"falha ao executar o retrato: {proc}"}
    try:
        _, data = last_json_line(proc.stdout)
    except json.JSONDecodeError:
        data = None
    if (
        proc.returncode != 0
        or not isinstance(data, dict)
        or not isinstance(data.get("snapshot"), dict)
    ):
        return None, {
            "error": "retrato inválido", "returncode": proc.returncode,
            "output": proc.stdout[-500:], "stderr": proc.stderr[-500:],
        }  # fmt: skip
    return data["snapshot"], {}


def diff_snapshots(before: Any, after: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    """Chaves (caminho com ponto) cujo valor mudou, com antes/depois; dicts descem, listas não."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: dict[str, dict[str, Any]] = {}
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(diff_snapshots(before.get(key), after.get(key), path))
        return out
    return {} if before == after else {prefix or "<root>": {"before": before, "after": after}}


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

    Quem chama faz o `executor.reset()` antes. Modalidade sem referência (`missing_references`)
    não aplica nada: `ref_missing` True e `valid` None (nem válida nem inválida).

    Gate invertido em `expect = "ask"` (pedido ambíguo): o check confere o estado intocado, então
    o initial deve passar, e a referência (um chute plausível) deve executar e reprovar.
    """
    row: dict[str, Any] = {
        "task_id": task.task_id, "category": task.category, "modality": modality.name,
        "expect": task.expect, "initial_ok": None, "initial_output": "", "baseline_pass": None,
        "baseline_details": {},
        "ref_exec_ok": None, "ref_output": "", "ref_unsupported_pattern": None,
        "ref_check_pass": None, "ref_check_details": {}, "ref_missing": False, "valid": False,
    }  # fmt: skip
    if modality.name in task.missing_references:
        row.update(ref_missing=True, valid=None)
        return row
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
    row["ref_check_pass"], row["ref_check_details"] = run_check(
        toolchain, task, ref_path, settle=True
    )
    if task.expect == "ask":
        row["valid"] = row["baseline_pass"] is True and ref.ok and row["ref_check_pass"] is False
    else:
        row["valid"] = row["baseline_pass"] is False and ref.ok and row["ref_check_pass"] is True
    return row


def replay_pair(
    task: ModalityTask,
    modality: Modality,
    content: str,
    toolchain: Toolchain,
    executor: FlociExecutor,
    runs_dir: Path,
    run_id: str,
    rep: int = 0,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Reexecuta um artefato (sem LLM): estado inicial → retrato → artefato → check → retrato.

    Mesmo fluxo de `prevalidate_pair`, com `content` no lugar da referência. Quem chama faz o
    `executor.reset()` antes. `collateral_ok` = retrato antes == depois; None se o retrato
    falhou (erro em `collateral_details`) ou se o estado inicial não aplicou.
    """
    row: dict[str, Any] = {
        "initial_ok": None, "exec_ok": None, "output": "", "check_pass": None,
        "check_details": {}, "collateral_ok": None, "collateral_details": {},
    }  # fmt: skip
    inherit = None
    if task.initial_tf is not None:
        initial, initial_path = apply_initial(
            toolchain, executor, task, modality, runs_dir, run_id, max_chars, rep=rep
        )
        row["initial_ok"] = initial.ok
        if not initial.ok:
            row["collateral_details"] = {"error": "initial.tf não aplicou",
                                         "output": initial.output}  # fmt: skip
            return row
        if inherits_state(task, modality):
            inherit = initial_path
    before, before_err = run_snapshot(toolchain, task)
    result, path = run_artifact(
        toolchain, executor, modality, content, runs_dir, run_id, task.task_id, "replay",
        inherit, max_chars, rep=rep,
    )  # fmt: skip
    result = with_answer(task, modality, result, path)
    row.update(exec_ok=result.ok, output=result.output)
    row["check_pass"], row["check_details"] = run_check(toolchain, task, path, settle=True)
    after, after_err = run_snapshot(toolchain, task)
    if before is None or after is None:
        row["collateral_details"] = {"before_error": before_err or None,
                                     "after_error": after_err or None}  # fmt: skip
        return row
    diff = diff_snapshots(before, after)
    row.update(collateral_ok=not diff, collateral_details={"changed": diff})
    return row
