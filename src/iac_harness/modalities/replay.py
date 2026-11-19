"""Pré-validação e replay de pares tarefa × modalidade (sem LLM)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.modalities.checks import apply_initial, diff_snapshots, run_check, run_snapshot
from iac_harness.modalities.execute import inherits_state, run_artifact, with_answer
from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import Modality
from iac_harness.toolchain import Toolchain


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
        "ref_check_pass": None, "ref_check_details": {}, "ref_final_pass": None,
        "ref_final_details": {}, "ref_missing": False, "valid": False,
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
        if task.check_final is not None:
            # A referência é a resposta certa depois da pergunta: deve passar o check final.
            row["ref_final_pass"], row["ref_final_details"] = run_check(
                toolchain, task, ref_path, settle=True, final=True
            )
        row["valid"] = (
            row["baseline_pass"] is True and ref.ok and row["ref_check_pass"] is False
            and row["ref_final_pass"] is not False
        )  # fmt: skip
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
