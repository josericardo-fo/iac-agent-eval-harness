"""Uma trajetória do agente real (E3): alvo → agente → check, colateral → evento `final`.

O runner só orquestra; alvo, agente e condição são peças trocáveis (targets, agents, conditions).
O juiz roda depois (`agent-judge`), então `asked` e `success` ficam None no evento.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from iac_harness.agentic.agents import CodingAgent
from iac_harness.agentic.conditions import resolve_condition
from iac_harness.agentic.strata import strata_fields
from iac_harness.agentic.stream import state_ops
from iac_harness.agentic.targets import Target
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.modalities.execute import (
    ANSWER_FILENAME,
    INHERITED_TF_FILES,
    diff_snapshots,
    run_artifact,
    run_check,
    run_snapshot,
)
from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import MODALITIES
from iac_harness.toolchain import remove_terraform_dir
from iac_harness.workspace import _safe_name, pair_dir

AGENT_PROMPT_VERSION = "e3-agent-v1"
WORK_DIRNAME = "work"
# Override do provider no /work do agente (nome neutro: o agente não deve ver "harness").
AGENT_OVERRIDE_FILENAME = "zz_provider_override.tf"
# Mesmos campos de tokens das métricas do agente (agentic/metrics.TOKEN_FIELDS, sem o total).
TOKEN_FIELDS = ("input", "output", "cache_creation_input", "cache_read_input")
# Unidade de resume do agent-run.
AGENT_UNIT = ("task_id", "condition", "model", "rep")

ENV_NOTE = (
    "Environment: AWS region {region}. AWS credentials are already configured in the environment."
    "\nThe project directory is /work."
)
INITIAL_NOTE = (
    "/work contains main.tf, the Terraform configuration that created the existing resources,"
    "\nand its current terraform.tfstate."
)
# Entrega da resposta em answer.json, sem citar o harness (o agente só vê a tarefa).
ANSWER_NOTE = "Write the answer to the file /work/answer.json as JSON."


class AgentTermination(StrEnum):
    DONE = "DONE"  # o agente terminou com `result` sem erro
    MAX_TURNS = "MAX_TURNS"  # result com subtype error_max_turns
    TIMEOUT = "TIMEOUT"  # timeout de parede (timeout_s): container removido
    AGENT_ERROR = "AGENT_ERROR"  # sem `result`, ou result com erro
    CONDITION_LOAD_FAIL = "CONDITION_LOAD_FAIL"  # plugin da condição ausente no system/init
    INITIAL_FAIL = "INITIAL_FAIL"  # initial.tf não aplicou (falha do harness, não do agente)


# Terminações que são falha do harness: ficam fora das métricas do agente.
HARNESS_FAILURES = {AgentTermination.CONDITION_LOAD_FAIL, AgentTermination.INITIAL_FAIL}


@dataclass
class AgentRunContext:
    run_id: str
    runs_dir: Path
    agent: CodingAgent
    plugin_root: Path
    strata: dict[str, dict[str, Any]]
    max_chars: int = DEFAULT_MAX_CHARS
    lock: threading.Lock = field(default_factory=threading.Lock)  # recorder compartilhado


def shows_initial(task: ModalityTask) -> bool:
    """O agente recebe o initial.tf (como main.tf, com o tfstate) só em modify/delete.

    Mesma regra das modalidades: na consulta, o initial.tf entregaria a resposta; com
    `hide_initial`, o agente descobre o estado sozinho.
    """
    return (
        task.initial_tf is not None
        and task.category in ("modify", "delete")
        and not task.hide_initial
    )


def agent_prompt(task: ModalityTask) -> str:
    """prompt.md + linha de ambiente (+ initial.tf em /work, + answer.json se pedir resposta)."""
    parts = [task.prompt.rstrip(), ENV_NOTE.format(region=task.region)]
    if shows_initial(task):
        parts.append(INITIAL_NOTE)
    if task.wants_answer:
        parts.append(ANSWER_NOTE)
    return "\n\n".join(parts) + "\n"


def trajectory_id(task_id: str, condition: str, model: str, rep: int) -> str:
    """Chave da trajetória (liga o evento final ao veredito do juiz em judge.jsonl)."""
    return f"{task_id}|{condition}|{model}|{rep}"


def model_slug(model: str) -> str:
    return _safe_name(model).replace(" ", "-")


def unit_dir(runs_dir: Path, run_id: str, task_id: str, condition: str, model: str, rep: int):
    """runs/<run>/<task>/<rep>/<condição>/<modelo>/."""
    return pair_dir(runs_dir, run_id, task_id, rep) / condition / model_slug(model)


def _prepare_work(task: ModalityTask, target: Target, initial_path: Path | None, work: Path):
    """Monta o /work: vazio, ou initial.tf como main.tf + tfstate/lock + override do Floci."""
    work.mkdir(parents=True)
    files: dict[str, str] = {}
    if shows_initial(task) and initial_path is not None:
        files["main.tf"] = task.initial_tf or ""
        (work / "main.tf").write_text(files["main.tf"])
        for name in INHERITED_TF_FILES:
            if (initial_path / name).exists():
                shutil.copy(initial_path / name, work / name)
    override = target.executor.override_for(files)  # None na AWS real
    if override is not None:
        (work / AGENT_OVERRIDE_FILENAME).write_text(override)
    # O agente roda com outro uid (usuário `agent`): o /work precisa ser gravável por ele.
    for path in [work, *work.rglob("*")]:
        os.chmod(path, 0o777 if path.is_dir() else 0o666)


def _termination(result: Any, condition_loaded: bool = True) -> AgentTermination:
    if result.condition_load_fail or not condition_loaded:
        return AgentTermination.CONDITION_LOAD_FAIL
    if result.timed_out:
        return AgentTermination.TIMEOUT
    if result.subtype == "error_max_turns":
        return AgentTermination.MAX_TURNS
    if result.final_text is None or result.is_error:
        return AgentTermination.AGENT_ERROR
    return AgentTermination.DONE


def _tokens(usage: dict[str, Any]) -> dict[str, int]:
    tok = {f"{k}_tokens": int(usage.get(f"{k}_tokens") or 0) for k in TOKEN_FIELDS}
    tok["total_tokens"] = sum(tok.values())
    return tok


def run_agent_pair(
    task: ModalityTask,
    condition_name: str,
    model: str,
    rep: int,
    target: Target,
    recorder: Any,
    ctx: AgentRunContext,
) -> dict[str, Any]:
    """Uma trajetória: reset → initial → /work → retrato → agente → check → retrato → `final`."""
    # Proxy parado: aborta o run antes de gravar qualquer evento (senão viram AGENT_ERROR).
    target.check_proxy()
    start = time.monotonic()
    condition = resolve_condition(condition_name, ctx.plugin_root)
    udir = unit_dir(ctx.runs_dir, ctx.run_id, task.task_id, condition_name, model, rep)
    group = str(udir.relative_to(pair_dir(ctx.runs_dir, ctx.run_id, task.task_id, rep)))
    final: dict[str, Any] = {
        "event": "final", "trajectory_id": trajectory_id(task.task_id, condition_name, model, rep),
        "task_id": task.task_id, "category": task.category,
        "expect": task.expect, "condition": condition_name, "model": model, "rep": rep,
        "target": target.kind, "agent": ctx.agent.name, "prompt_version": AGENT_PROMPT_VERSION,
        **strata_fields(ctx.strata.get(task.task_id, {}), target.kind),
        "initial_ok": None, "check_pass": None, "check_details": {}, "asked": None,
        "judge_kind": None, "success": None, "collateral_ok": None, "collateral_details": {},
        "answer_present": None, "final_text": None, "num_turns": None, "cost_usd": None,
        "tool_counts": {}, "n_tool_calls": 0, "verified_state": None, "mutated": None,
        "verify_calls": 0, "permission_denials": 0,
        "exit_code": None, "agent_duration_s": None, "plugins_loaded": None,
        **_tokens({}),
    }  # fmt: skip
    try:
        target.reset()
        initial_path = None
        if task.initial_tf is not None:
            initial, initial_path = run_artifact(
                target.toolchain, target.executor, MODALITIES["terraform"], task.initial_tf,
                ctx.runs_dir, ctx.run_id, task.task_id, "initial", max_chars=ctx.max_chars,
                group=group, rep=rep,
            )  # fmt: skip
            final["initial_ok"] = initial.ok
            if not initial.ok:
                final.update(
                    termination=AgentTermination.INITIAL_FAIL.value,
                    collateral_details={"error": "initial.tf não aplicou",
                                        "output": initial.output},
                )  # fmt: skip
                return _finish(final, start, recorder, ctx)
        work = udir / WORK_DIRNAME
        _prepare_work(task, target, initial_path, work)
        before, before_err = (None, {})
        if task.collateral is not None:
            before, before_err = run_snapshot(target.toolchain, task)
        result = ctx.agent.run(agent_prompt(task), work, condition, model, target, udir)
        # Segunda guarda: o init final também precisa satisfazer a condição.
        termination = _termination(result, condition.loaded(result.init))
        ops = state_ops(result.tool_calls)
        final.update(
            termination=termination.value,
            final_text=result.final_text,
            num_turns=result.num_turns,
            cost_usd=result.cost_usd,
            tool_counts=result.tool_counts,
            n_tool_calls=len(result.tool_calls),
            verified_state=ops.verified,
            mutated=ops.mutated,
            verify_calls=ops.verify_calls,
            permission_denials=len(result.permission_denials),
            exit_code=result.exit_code,
            agent_duration_s=round(result.duration_s, 3),
            plugins_loaded=[p.get("name") for p in (result.init or {}).get("plugins") or []],
            **_tokens(result.usage),
        )
        if termination is AgentTermination.CONDITION_LOAD_FAIL:
            return _finish(final, start, recorder, ctx)
        final["answer_present"] = (work / ANSWER_FILENAME).is_file()
        final["check_pass"], final["check_details"] = run_check(
            target.toolchain, task, work, settle=target.kind == "aws"
        )
        if task.collateral is not None:
            after, after_err = run_snapshot(target.toolchain, task)
            if before is None or after is None:
                final["collateral_details"] = {"before_error": before_err or None,
                                               "after_error": after_err or None}  # fmt: skip
            else:
                diff = diff_snapshots(before, after)
                final.update(collateral_ok=not diff, collateral_details={"changed": diff})
        remove_terraform_dir(work, target.toolchain)  # provider copiado enche o disco
    finally:
        # AWS real: sweep contra o baseline logo após a trajetória (também se algo levantou).
        target.after_trajectory()
    return _finish(final, start, recorder, ctx)


def _finish(final: dict[str, Any], start: float, recorder: Any, ctx: AgentRunContext):
    final["duration_s"] = round(time.monotonic() - start, 3)
    with ctx.lock:
        recorder.record(final)
    return final
