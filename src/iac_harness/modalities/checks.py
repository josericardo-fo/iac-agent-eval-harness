"""Estado inicial, task scripts (setup, check, retrato) e verificação no Floci."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.modalities.execute import ANSWER_FILENAME, ExecResult, run_artifact, truncate
from iac_harness.modalities.loader import SETUP_FILENAME, UPSTREAM_DIRNAME, ModalityTask
from iac_harness.modalities.spec import MODALITIES, Modality
from iac_harness.toolchain import Toolchain
from iac_harness.verifiers.plan_rego import grade_config

CHECK_FILENAME = "check.py"
COLLATERAL_FILENAME = "collateral.py"
TFSTATE_FILENAME = "terraform.tfstate"
# Consistência eventual na AWS real: o check pós-solução é refeito até passar dentro da janela.
CHECK_SETTLE_WINDOW_S = 60
CHECK_SETTLE_INTERVAL_S = 10


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
    result, path = run_artifact(
        toolchain, executor, MODALITIES["terraform"], task.initial_tf, runs_dir, run_id,
        task.task_id, name, max_chars=max_chars, group=modality.name, rep=rep,
    )  # fmt: skip
    return run_setup(toolchain, task, result, max_chars), path


def run_setup(
    toolchain: Toolchain,
    task: ModalityTask,
    initial: ExecResult,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> ExecResult:
    """Roda o setup.py da tarefa depois do apply inicial (mesmo ambiente do check).

    Completa o estado que o initial.tf não expressa (objeto versionado, regra criada fora do
    Terraform). Sem setup.py, ou se o apply já falhou, devolve `initial` como veio; falha do
    setup vira falha do estado inicial (o `output` leva a saída do script).
    """
    if task.setup is None or not initial.ok:
        return initial
    proc = _run_task_script(toolchain, task, SETUP_FILENAME, task.setup, None)
    if isinstance(proc, str):
        output = f"falha ao executar o setup: {proc}"
    elif proc.returncode != 0:
        output = truncate(
            f"setup.py falhou ({proc.returncode}): {proc.stdout}\n{proc.stderr}", max_chars
        )
    else:
        return initial
    return ExecResult(False, output, raw=initial.raw + "\n" + output)


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
    final: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Roda o check.py num diretório temporário FORA do workspace.

    Com `final` (estado depois da resposta do usuário simulado), roda o `check_final.py` da
    tarefa se existir; sem ele, o `check.py`.

    Se `workspace` tiver answer.json (consulta), ele é copiado para o lado do check.
    Com `settle` no alvo aws, um check reprovado é refeito a cada CHECK_SETTLE_INTERVAL_S
    por até CHECK_SETTLE_WINDOW_S (consistência eventual); `check_attempts` vai nos detalhes.
    Só para checks pós-solução: o baseline deve reprovar e não pode ser refeito.
    """
    if not (settle and toolchain.target == "aws"):
        return _run_check_once(toolchain, task, workspace, final)
    max_attempts = 1 + CHECK_SETTLE_WINDOW_S // CHECK_SETTLE_INTERVAL_S
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            time.sleep(CHECK_SETTLE_INTERVAL_S)
        passed, details = _run_check_once(toolchain, task, workspace, final)
        if passed:
            break
    return passed, {**details, "check_attempts": attempt}


def _run_task_script(
    toolchain: Toolchain, task: ModalityTask, filename: str, code: str, workspace: Path | None
) -> subprocess.CompletedProcess | str:
    """Roda `python3 <filename> <region>` num diretório temporário FORA do workspace.

    Mesmo ambiente do check: upstream/ ao lado, answer.json e terraform.tfstate do workspace, se
    houver. Devolve o
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
        for name in (ANSWER_FILENAME, TFSTATE_FILENAME):
            # tfstate: checks de importação conferem o estado que o agente deixou no workspace.
            if workspace is not None and (workspace / name).is_file():
                shutil.copy(workspace / name, tmp_dir / name)
        try:
            return toolchain.run(["python3", filename, task.region], tmp_dir)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return str(exc)


def _run_check_once(
    toolchain: Toolchain, task: ModalityTask, workspace: Path | None, final: bool = False
) -> tuple[bool, dict[str, Any]]:
    if task.rego_policy is not None:  # tarefa de dataset (E16): oráculo plan + OPA, sem check.py
        return grade_config(
            toolchain, task.rego_policy, workspace, task.reference_resource_types, task.rego_rules
        )
    code = task.check_final if final and task.check_final is not None else task.check
    proc = _run_task_script(toolchain, task, CHECK_FILENAME, code, workspace)
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
