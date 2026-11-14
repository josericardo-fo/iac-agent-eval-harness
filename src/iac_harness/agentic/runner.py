"""Uma trajetória do agente real (E3): alvo → agente → check, colateral → evento `final`.

O runner só orquestra; alvo, agente e condição são peças trocáveis (targets, agents, conditions).
O juiz roda depois (`agent-judge`), então `asked` e `success` ficam None no evento.

Com usuário simulado (`ctx.user_sim`), se o agente parar para perguntar (veredito inline do juiz),
o simulador responde a partir do cartão da tarefa e a mesma sessão do agente é retomada. Os campos
de sempre continuam sendo do turno 1 (comparáveis com E2/E3/E11); o desfecho depois das respostas
vai nos campos `*_final`.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from iac_harness.agentic.agents import AgentResult, CodingAgent
from iac_harness.agentic.conditions import Condition, resolve_condition
from iac_harness.agentic.judge import Judge
from iac_harness.agentic.strata import strata_fields
from iac_harness.agentic.stream import state_ops
from iac_harness.agentic.targets import Target
from iac_harness.agentic.user_sim import UserSimulator
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.modalities.execute import (
    ANSWER_FILENAME,
    INHERITED_TF_FILES,
    diff_snapshots,
    run_artifact,
    run_check,
    run_setup,
    run_snapshot,
)
from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.spec import MODALITIES
from iac_harness.toolchain import remove_terraform_dir
from iac_harness.workspace import _safe_name, pair_dir

AGENT_PROMPT_VERSION = "e3-agent-v1"
WORK_DIRNAME = "work"
# Sessão do Claude Code persistida entre os turnos (apagada ao fim: pode ter saída de comandos).
SESSION_DIRNAME = "claude-home"
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
    user_sim: UserSimulator | None = None  # None: o agente age sozinho (fluxo de sempre)
    sim_judge: Judge | None = None  # classifica a mensagem final do turno para acionar o usuário
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


def _converse(
    task: ModalityTask,
    ctx: AgentRunContext,
    prompt: str,
    first: AgentResult,
    mutated: bool,
    resume: Callable[[str, str, int], AgentResult],
) -> tuple[list[AgentResult], list[dict[str, str]], str | None, str | None]:
    """Respostas do usuário simulado enquanto o agente perguntar (até `max_replies`).

    `resume(reply, session_id, turn)` roda o turno seguinte. Devolve (resultados dos turnos, trocas
    {"agent", "user"}, veredito inline do último turno, erro). Falha do juiz, do simulador ou da
    retomada não derruba a trajetória: o erro é registrado e vale o que já aconteceu.
    """
    sim, judge = ctx.user_sim, ctx.sim_judge
    assert sim is not None and judge is not None
    results, exchanges = [first], []
    kind = error = None
    try:
        while True:
            last = results[-1]
            if _termination(last) is not AgentTermination.DONE:
                break
            kind = judge.classify(last.final_text, prompt, mutated)["kind"]
            if kind != "ask" or len(exchanges) >= sim.max_replies:
                break
            if not last.session_id:
                raise RuntimeError("sem session_id para retomar a sessão")
            reply = sim.reply(task.user_card, prompt, last.final_text or "")
            if not reply:
                raise RuntimeError("usuário simulado respondeu vazio")
            exchanges.append({"agent": last.final_text or "", "user": reply})
            results.append(resume(reply, last.session_id, len(results) + 1))
            mutated = mutated or bool(state_ops(results[-1].tool_calls).mutated)
    except Exception as exc:  # noqa: BLE001 (registra e segue com o estado atual)
        error = f"{type(exc).__name__}: {exc}"[:300]
    return results, exchanges, kind, error


def _check_state(
    task: ModalityTask, target: Target, work: Path, before: Any, before_err: dict[str, Any],
    post_reply: bool = False,
) -> dict[str, Any]:  # fmt: skip
    """Resposta presente, check do estado e colateral (diff do retrato) depois de um turno.

    `post_reply`: o check é o `check_final.py` da tarefa (se houver), o do estado depois do usuário.
    """
    state: dict[str, Any] = {"answer_present": (work / ANSWER_FILENAME).is_file()}
    # Na AWS o `settle` refaz o check reprovado: no turno 1 de tarefa `ask` (estado intocado)
    # isso pode mascarar uma mudança tardia; limitação conhecida, comportamento mantido.
    state["check_pass"], state["check_details"] = run_check(
        target.toolchain, task, work, settle=target.kind == "aws", final=post_reply
    )
    if task.collateral is not None:
        after, after_err = run_snapshot(target.toolchain, task)
        if before is None or after is None:
            state["collateral_details"] = {
                "before_error": before_err or None,
                "after_error": after_err or None,
            }
        else:
            diff = diff_snapshots(before, after)
            state.update(collateral_ok=not diff, collateral_details={"changed": diff})
    return state


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
    sim = ctx.user_sim
    if sim is not None:
        # Colunas do desfecho final (iguais às do turno 1 enquanto não houver resposta).
        final.update(
            user_turns=0, sim_exchanges=[], sim_judge_kind=None, user_sim_error=None,
            termination_final=None, final_text_final=None, check_pass_final=None,
            check_details_final={}, collateral_ok_final=None, collateral_details_final={},
            answer_present_final=None, mutated_final=None, cost_usd_total=None,
            total_tokens_all=0, **sim.params,
            **{f"sim_{k}": v for k, v in (ctx.sim_judge.params if ctx.sim_judge else {}).items()},
        )  # fmt: skip
    session_dir = udir / SESSION_DIRNAME
    try:
        target.reset()
        initial_path = None
        if task.initial_tf is not None:
            initial, initial_path = run_artifact(
                target.toolchain, target.executor, MODALITIES["terraform"], task.initial_tf,
                ctx.runs_dir, ctx.run_id, task.task_id, "initial", max_chars=ctx.max_chars,
                group=group, rep=rep,
            )  # fmt: skip
            initial = run_setup(target.toolchain, task, initial, ctx.max_chars)
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
        prompt = agent_prompt(task)
        if sim is not None:
            session_dir.mkdir(parents=True)
            os.chmod(session_dir, 0o777)  # o usuário `agent` do container grava a sessão aqui
        turn_kwargs: dict[str, Any] = {"session_dir": session_dir} if sim is not None else {}
        result = ctx.agent.run(prompt, work, condition, model, target, udir, **turn_kwargs)
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
        final.update(_check_state(task, target, work, before, before_err))
        if sim is not None:

            def resume(reply: str, session_id: str, turn: int) -> AgentResult:
                return ctx.agent.run(
                    reply, work, condition, model, target, udir, session_dir=session_dir,
                    resume_session=session_id, turn=turn,
                )  # fmt: skip

            results, exchanges, kind, error = _converse(
                task, ctx, prompt, result, bool(ops.mutated), resume
            )
            _final_outcome(final, results, exchanges, kind, error)
            # Sem resposta, o desfecho final é o do turno 1, salvo com check_final.py (outro
            # check: o estado desejado depois do usuário, mesmo se o agente agiu sem perguntar).
            lost = bool(exchanges) and _lost_condition(results[-1], condition)
            if lost:
                # Falha só do turno retomado: `termination` e o resto do turno 1 não mudam.
                final["termination_final"] = AgentTermination.CONDITION_LOAD_FAIL.value
                final["user_sim_error"] = final["user_sim_error"] or "retomada sem o plugin"
            if not lost and (exchanges or task.check_final is not None):
                state = _check_state(task, target, work, before, before_err, post_reply=True)
                final.update({f"{k}_final": v for k, v in state.items()})
        remove_terraform_dir(work, target.toolchain)  # provider copiado enche o disco
    finally:
        if sim is not None:
            shutil.rmtree(session_dir, ignore_errors=True)
        # AWS real: sweep contra o baseline logo após a trajetória (também se algo levantou).
        target.after_trajectory()
    return _finish(final, start, recorder, ctx)


def _lost_condition(last: AgentResult, condition: Condition) -> bool:
    """Se o turno retomado perdeu o plugin da condição (a trajetória final não vale)."""
    return _termination(last, condition.loaded(last.init)) is AgentTermination.CONDITION_LOAD_FAIL


def _final_outcome(
    final: dict[str, Any], results: list[AgentResult], exchanges: list[dict[str, str]],
    kind: str | None, error: str | None,
) -> None:  # fmt: skip
    """Campos `*_final` que dependem só dos turnos (o estado final é conferido à parte).

    Sem resposta do usuário, o desfecho final é o do turno 1.
    """
    last = results[-1]
    ops = state_ops([c for r in results for c in r.tool_calls])
    final.update(
        user_turns=len(exchanges), sim_exchanges=exchanges, sim_judge_kind=kind,
        user_sim_error=error, termination_final=_termination(last).value,
        final_text_final=last.final_text, mutated_final=ops.mutated,
        cost_usd_total=round(sum(r.cost_usd or 0 for r in results), 6),
        total_tokens_all=sum(_tokens(r.usage)["total_tokens"] for r in results),
    )  # fmt: skip
    if not exchanges:
        final.update(
            check_pass_final=final["check_pass"], check_details_final=final["check_details"],
            collateral_ok_final=final["collateral_ok"],
            collateral_details_final=final["collateral_details"],
            answer_present_final=final["answer_present"],
        )  # fmt: skip


def _finish(final: dict[str, Any], start: float, recorder: Any, ctx: AgentRunContext):
    final["duration_s"] = round(time.monotonic() - start, 3)
    with ctx.lock:
        recorder.record(final)
    return final
