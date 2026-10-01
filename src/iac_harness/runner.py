"""Runner de uma tarefa: tentativa 1 compartilhada, ramos por condição, avaliação final comum."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from iac_harness.agents.prompts import render_repair
from iac_harness.conditions import Condition
from iac_harness.feedback import DEFAULT_MAX_CHARS, build_feedback, classify
from iac_harness.interfaces import AgentBackend, Executor, Recorder, Verifier
from iac_harness.types import (
    AgentReply,
    Artifact,
    AttemptRecord,
    CheckResult,
    CheckStatus,
    FinalRecord,
    FinalStatus,
    FirstFailure,
    Stage,
    Task,
    TaskOracle,
    Termination,
    Turn,
)
from iac_harness.workspace import RESPONSE_FILENAME, Workspace, create_workspace

# Ordem da cadeia de avaliação comum.
COMMON_CHAIN = (Stage.VALIDATE, Stage.PLAN, Stage.OPA, Stage.SANDBOX)


class SandboxGate:
    """Adapta o Executor a Verifier; o reset antes de cada apply isola as execuções."""

    stage = Stage.SANDBOX

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def check(self, workspace: Workspace, oracle: TaskOracle) -> CheckResult:
        self._executor.reset()
        return self._executor.apply(workspace)


@dataclass(frozen=True)
class RunContext:
    run_id: str
    runs_dir: Path
    dataset_version: str
    model_provider: str = "anthropic"
    max_feedback_chars: int = DEFAULT_MAX_CHARS
    # Override de provider: texto fixo ou função do artefato (precisa ver os aliases do agente).
    override: str | Callable[[Artifact], str] | None = None

    def override_for(self, artifact: Artifact) -> str | None:
        return self.override(artifact) if callable(self.override) else self.override


def run_gates(
    stages: Sequence[Stage],
    gates: dict[Stage, Verifier],
    workspace: Workspace,
    oracle: TaskOracle,
) -> tuple[dict[str, str], CheckResult | None, dict[str, float]]:
    """Roda os gates em ordem e para na primeira falha; os demais ficam SKIPPED.

    Devolve (status por estágio, resultado que falhou, duração por estágio executado).
    """
    statuses = {s.value: CheckStatus.SKIPPED.value for s in COMMON_CHAIN}
    durations: dict[str, float] = {}
    for stage in stages:
        result = gates[stage].check(workspace, oracle)
        statuses[stage.value] = result.status.value
        durations[stage.value] = result.duration_s
        if result.status is not CheckStatus.PASS:
            return statuses, result, durations
    return statuses, None, durations


def first_failure_of(failing: CheckResult | None) -> FirstFailure:
    """Vocabulário único de first_failure (tentativas e avaliação final)."""
    if failing is None:
        return FirstFailure.PASS
    if failing.status is CheckStatus.UNSUPPORTED:
        return FirstFailure.SANDBOX_UNSUPPORTED
    if failing.status is CheckStatus.ERROR:
        return FirstFailure.HARNESS_ERROR  # falha de infraestrutura, não do agente
    return FirstFailure(f"{failing.stage.value}_FAIL")


def evaluate_final(
    ctx: RunContext,
    task: Task,
    oracle: TaskOracle,
    rep: int,
    condition: str,
    reply: AgentReply,
    gates: dict[Stage, Verifier],
    stages: Sequence[Stage] = COMMON_CHAIN,
) -> tuple[dict[str, str], FirstFailure, CheckResult | None, Workspace]:
    """Cadeia comum, idêntica para todas as condições (completa, salvo `stages` reduzido)."""
    ws = create_workspace(
        ctx.runs_dir,
        ctx.run_id,
        task.task_id,
        rep,
        condition,
        "final",
        reply.artifact,
        ctx.override_for(reply.artifact),
    )
    statuses, failing, _ = run_gates(stages, gates, ws, oracle)
    return statuses, first_failure_of(failing), failing, ws


def _run_condition(
    ctx: RunContext,
    task: Task,
    oracle: TaskOracle,
    rep: int,
    condition: Condition,
    shared: AgentReply,
    backend: AgentBackend,
    gates: dict[Stage, Verifier],
    recorder: Recorder,
) -> tuple[AgentReply, int, Termination, int, int]:
    """Loop de repair; devolve (resposta final, nº de tentativas, motivo, tokens de repair in/out).

    Os tokens da tentativa 1 (compartilhada) não entram: são registrados à parte.
    """
    history: list[Turn] = []
    reply = shared
    tokens_in = tokens_out = 0
    reason = Termination.BUDGET_EXHAUSTED
    attempt = 0
    for attempt in range(1, condition.max_attempts + 1):
        if attempt > 1:
            reply = backend.generate(task, history)
        if attempt > 1:
            tokens_in += reply.usage.input_tokens
            tokens_out += reply.usage.output_tokens
        ws = create_workspace(
            ctx.runs_dir,
            ctx.run_id,
            task.task_id,
            rep,
            condition.name,
            f"attempt_{attempt}",
            reply.artifact,
            ctx.override_for(reply.artifact),
        )
        (ws.path / RESPONSE_FILENAME).write_text(reply.raw_text)
        statuses, failing, durations = run_gates(condition.loop_stages, gates, ws, oracle)

        termination: Termination | None = None
        feedback = None
        feedback_sent: str | None = None
        if failing is None:
            termination = Termination.PASSED if condition.loop_stages else Termination.NO_FEEDBACK
        elif failing.status is CheckStatus.UNSUPPORTED:
            termination = Termination.SANDBOX_UNSUPPORTED  # gate indisponível: sem repair
        elif failing.status is CheckStatus.ERROR:
            termination = Termination.GATE_ERROR
        elif attempt == condition.max_attempts:
            termination = Termination.BUDGET_EXHAUSTED
        else:
            feedback = build_feedback(failing, ctx.max_feedback_chars)
            assert feedback is not None
            feedback_sent = render_repair(reply.artifact.files, feedback)

        recorder.record(
            AttemptRecord(
                run_id=ctx.run_id,
                task_id=task.task_id,
                condition=condition.name,
                rep=rep,
                difficulty=task.difficulty,
                dataset_version=ctx.dataset_version,
                model_provider=ctx.model_provider,
                model_id=reply.model_id,
                params=reply.params,
                request_id=reply.request_id,
                attempt_index=attempt,
                shared=attempt == 1,
                statuses=statuses,
                first_failure_stage=first_failure_of(failing).value,
                first_failure_status=failing.status.value if failing else None,
                diagnostic=failing.message if failing else "",
                diagnostic_chars=len(failing.message) if failing else 0,
                error_class=classify(failing) if failing else None,
                termination_reason=termination.value if termination else None,
                feedback_sent=feedback_sent,
                durations=durations,
                input_tokens=reply.usage.input_tokens,
                output_tokens=reply.usage.output_tokens,
                total_tokens=reply.usage.total_tokens,
                artifact_path=str(ws.path),
                artifact_hash=ws.artifact_hash,
                response_path=str(ws.path / RESPONSE_FILENAME),
            ).to_event()
        )
        if termination is not None:
            reason = termination
            break
        assert feedback is not None
        history.append(Turn(reply.artifact, feedback))
    return reply, attempt, reason, tokens_in, tokens_out


def run_task(
    task: Task,
    oracle: TaskOracle,
    rep: int,
    conditions: Sequence[Condition],
    backend: AgentBackend,
    gates: dict[Stage, Verifier],
    recorder: Recorder,
    ctx: RunContext,
    *,
    harness_valid: bool = True,
    sandbox_supported: bool = True,
) -> list[FinalRecord]:
    """Executa todas as condições de (task, rep) a partir de uma tentativa 1 compartilhada."""
    shared = backend.generate(task, [])
    finals: list[FinalRecord] = []
    # Sem referência que rode no Floci, o emulador não é instrumento confiável nesta tarefa:
    # o gate SANDBOX sai do loop (C vira B) e o apply não roda na avaliação final.
    final_stages = COMMON_CHAIN
    if not sandbox_supported:
        final_stages = tuple(s for s in COMMON_CHAIN if s is not Stage.SANDBOX)
    for condition in conditions:
        if not sandbox_supported:
            condition = replace(
                condition,
                loop_stages=tuple(s for s in condition.loop_stages if s is not Stage.SANDBOX),
            )
        reply, attempts, reason, t_in, t_out = _run_condition(
            ctx, task, oracle, rep, condition, shared, backend, gates, recorder
        )
        statuses, first_stage, failing, ws = evaluate_final(
            ctx, task, oracle, rep, condition.name, reply, gates, final_stages
        )
        if not sandbox_supported and first_stage is FirstFailure.PASS:
            first_stage = FirstFailure.SANDBOX_UNSUPPORTED  # pré-deploy ok, sem gate de sandbox
        if not harness_valid:
            final_status = FinalStatus.HARNESS_INVALID
        elif first_stage is FirstFailure.PASS:
            final_status = FinalStatus.PASS
        elif first_stage is FirstFailure.SANDBOX_UNSUPPORTED:
            final_status = FinalStatus.SANDBOX_UNSUPPORTED
        elif first_stage is FirstFailure.HARNESS_ERROR:
            final_status = FinalStatus.HARNESS_ERROR
        else:
            final_status = FinalStatus.FAIL
        record = FinalRecord(
            run_id=ctx.run_id,
            task_id=task.task_id,
            condition=condition.name,
            rep=rep,
            difficulty=task.difficulty,
            dataset_version=ctx.dataset_version,
            model_provider=ctx.model_provider,
            model_id=reply.model_id,
            params=reply.params,
            attempts=attempts,
            termination_reason=reason.value,
            statuses=statuses,
            first_failure_stage=first_stage.value,
            diagnostic=failing.message if failing else "",
            error_class=classify(failing) if failing else None,
            shared_input_tokens=shared.usage.input_tokens,
            shared_output_tokens=shared.usage.output_tokens,
            repair_input_tokens=t_in,
            repair_output_tokens=t_out,
            artifact_path=str(ws.path),
            artifact_hash=ws.artifact_hash,
            final_status=final_status.value,
            sandbox_supported=sandbox_supported,
        )
        recorder.record(record.to_event())
        finals.append(record)
    return finals
