"""Runner do teste de modalidades: script inteiro + correção pelo erro de execução.

Loop por (tarefa, modalidade, rep), até `max_attempts`. Cada tentativa recomeça do estado limpo
(reset + initial.tf). O agente só vê o erro de execução (inclusive o de operação não suportada
pelo Floci, que também recebe correção); a nota (check.py) roda uma vez no fim, sobre o estado da
última execução, e nunca volta ao agente. `success` = última execução ok e check aprovado.

Com `allow_question` (E2), o agente pode entregar question.md no lugar do arquivo: nada executa e
a trajetória termina com ASKED. Aí `success` depende do `expect` da tarefa: "ask" = perguntou;
"act" = execução ok e check aprovado (perguntar reprova).

Com `free_choice` (E4), a modalidade é a pseudo-modalidade `free`: em cada tentativa o agente
escolhe a ferramenta pelo arquivo que entrega (exatamente um de main.tf, run.sh, main.py), e a
tentativa executa com a modalidade escolhida. A escolha vai nos eventos (`chosen`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from iac_harness.executors.floci import FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.interfaces import AgentBackend, Recorder
from iac_harness.modalities.execute import (
    ANSWER_FILENAME,
    ExecResult,
    apply_initial,
    inherits_state,
    run_artifact,
    run_check,
    truncate,
    with_answer,
)
from iac_harness.modalities.loader import ModalityTask
from iac_harness.modalities.prompts import (
    FORMAT_ERROR,
    FREE_FORMAT_ERROR,
    QUESTION_FILENAME,
    free_user_prompt,
    prompt_version,
    user_prompt,
)
from iac_harness.modalities.spec import MODALITIES, Modality
from iac_harness.toolchain import Toolchain
from iac_harness.types import Artifact, Feedback, Stage, Task, Turn
from iac_harness.workspace import RESPONSE_FILENAME, create_workspace

DEFAULT_MAX_ATTEMPTS = 3
# Provisioner local-exec no Terraform: o agente roda comandos (CLI/scripts) por fora do provider.
LOCAL_EXEC_RE = re.compile(r'provisioner\s+"local-exec"')
# Bloco da pergunta (E2): além de title="question.md" (que o parse_files já lê), aceita o nome do
# arquivo como info string (```question.md, com ou sem title=). Fica aqui, e não no parse_files,
# para não mudar como main.tf/run.sh/main.py são lidos (o formato medido nos outros runs).
QUESTION_BLOCK_RE = re.compile(
    r"```(?:question\.md(?=[ \t\n])"
    r"|[\w-]*[ \t]+(?:title|filename|file)\s*=\s*[\"']question\.md[\"'])[^\n]*\n(.*?)```",
    re.DOTALL,
)


class ModTermination(StrEnum):
    EXEC_OK = "EXEC_OK"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"  # última tentativa falhou na execução
    FORMAT_FAIL = "FORMAT_FAIL"  # última tentativa sem o arquivo esperado
    INITIAL_FAIL = "INITIAL_FAIL"  # o initial.tf não aplicou (falha do harness, não do agente)
    ASKED = "ASKED"  # o agente entregou question.md (só com allow_question); nada executou


@dataclass(frozen=True)
class ModRunContext:
    run_id: str
    runs_dir: Path
    toolchain: Toolchain
    executor: FlociExecutor
    model_provider: str = "anthropic"
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_chars: int = DEFAULT_MAX_CHARS
    allow_question: bool = False  # aceita question.md como desfecho (prompt mod-v3-ask)
    free_choice: bool = False  # forma livre (E4): modalidade `free`, prompt mod-v3-free


def _prepare(
    ctx: ModRunContext, task: ModalityTask, modality: Modality, rep: int, attempt: int
) -> tuple[ExecResult | None, Path | None]:
    """Reset + estado inicial; devolve (resultado do initial ou None, dir a herdar ou None)."""
    ctx.executor.reset()
    if task.initial_tf is None:
        return None, None
    initial, path = apply_initial(
        ctx.toolchain, ctx.executor, task, modality, ctx.runs_dir, ctx.run_id, ctx.max_chars,
        rep=rep, name=f"initial_{attempt}",
    )  # fmt: skip
    # Na forma livre, o dir a herdar vale para o caso de a escolha ser Terraform (run_artifact
    # ignora `inherit_from` nas outras modalidades).
    target = MODALITIES["terraform"] if ctx.free_choice else modality
    return initial, (path if inherits_state(task, target) else None)


def uses_local_exec(modality: Modality, content: str | None) -> bool | None:
    """Se o main.tf tem bloco `provisioner "local-exec"`; None fora do Terraform ou sem arquivo."""
    if modality.name != "terraform" or content is None:
        return None
    return bool(LOCAL_EXEC_RE.search(content))


def choose_modality(files: dict[str, str]) -> tuple[Modality | None, str | None]:
    """Forma livre: a modalidade do único arquivo de código entregue; (None, None) se zero ou >1."""
    found = [m for m in MODALITIES.values() if m.filename in files]
    if len(found) != 1:
        return None, None
    return found[0], files[found[0].filename]


def parse_question(text: str) -> str | None:
    """Corpo do bloco question.md na resposta bruta (o último, se houver vários); None se não há."""
    found = QUESTION_BLOCK_RE.findall(text)
    return found[-1] if found else None


def _answer(path: Path, max_chars: int) -> str | None:
    """answer.json da tentativa (truncado), para análise; None se não existe."""
    answer = path / ANSWER_FILENAME
    return truncate(answer.read_text(), max_chars) if answer.is_file() else None


def run_pair(
    task: ModalityTask,
    modality: Modality,
    rep: int,
    backend: AgentBackend,
    recorder: Recorder,
    ctx: ModRunContext,
) -> dict[str, Any]:
    """Uma trajetória (tarefa, modalidade, rep); grava eventos `attempt` e `final`."""
    prompt = free_user_prompt(task) if ctx.free_choice else user_prompt(task, modality)
    agent_task = Task(task.task_id, prompt, 0, {"category": task.category})
    history: list[Turn] = []
    base = {
        "run_id": ctx.run_id, "task_id": task.task_id, "category": task.category,
        "modality": modality.name, "rep": rep, "model_provider": ctx.model_provider,
    }  # fmt: skip
    tokens_in = tokens_out = 0
    termination = ModTermination.BUDGET_EXHAUSTED
    attempt = 0
    model_id = params = None
    exec_ok = False  # result.ok da última tentativa executada
    question: str | None = None
    both_files = False
    choices: list[str | None] = []  # forma livre: modalidade escolhida em cada tentativa
    for attempt in range(1, ctx.max_attempts + 1):
        start = time.monotonic()
        initial, inherit = _prepare(ctx, task, modality, rep, attempt)
        if initial is not None and not initial.ok:
            recorder.record({**base, "event": "initial_fail", "attempt": attempt,
                             "output": initial.output})  # fmt: skip
            termination = ModTermination.INITIAL_FAIL
            break
        reply = backend.generate(agent_task, history)
        model_id, params = reply.model_id, reply.params
        tokens_in += reply.usage.input_tokens
        tokens_out += reply.usage.output_tokens
        if ctx.free_choice:
            chosen, content = choose_modality(reply.artifact.files)
            choices.append(chosen.name if chosen else None)
        else:
            chosen, content = modality, reply.artifact.files.get(modality.filename)
        name = f"attempt_{attempt}"
        asked_now = reply.artifact.files.get(QUESTION_FILENAME)
        if asked_now is None and ctx.allow_question:
            asked_now = parse_question(reply.raw_text)
        if ctx.allow_question and asked_now is not None:
            # Pergunta: vale mesmo com o arquivo junto (both_files); nada executa e a trajetória
            # termina. O _prepare desta tentativa já resetou e reaplicou o initial, então o check
            # avalia o estado inicial.
            question = asked_now
            both_files = content is not None
            ws = create_workspace(
                ctx.runs_dir, ctx.run_id, task.task_id, rep, modality.name, name,
                Artifact({QUESTION_FILENAME: question}),
            )  # fmt: skip
            path = ws.path
            (path / RESPONSE_FILENAME).write_text(reply.raw_text)
            recorder.record(
                {
                    **base,
                    "event": "attempt",
                    "attempt": attempt,
                    "model_id": reply.model_id,
                    "request_id": reply.request_id,
                    "params": reply.params,
                    "format_ok": True,
                    "exec_ok": None,
                    "asked": True,
                    "question": question,
                    "both_files": both_files,
                    "unsupported": False,
                    "uses_local_exec": uses_local_exec(modality, content),
                    "output": "",
                    "raw_output": "",
                    "feedback_sent": None,
                    "termination": ModTermination.ASKED.value,
                    "input_tokens": reply.usage.input_tokens,
                    "output_tokens": reply.usage.output_tokens,
                    "duration_s": round(time.monotonic() - start, 3),
                    "artifact_path": str(path),
                    "artifact_hash": None,
                    "answer": None,
                }
            )
            exec_ok = False
            termination = ModTermination.ASKED
            break
        if content is None:
            # Falha de formato: nada executa; o workspace guarda só a resposta bruta.
            ws = create_workspace(
                ctx.runs_dir, ctx.run_id, task.task_id, rep, modality.name, name, Artifact({})
            )
            path, artifact_hash = ws.path, None
            error = FREE_FORMAT_ERROR if ctx.free_choice else FORMAT_ERROR
            result = ExecResult(False, error.format(filename=modality.filename))
            format_fail = True
        else:
            result, path = run_artifact(
                ctx.toolchain, ctx.executor, chosen, content, ctx.runs_dir, ctx.run_id,
                task.task_id, name, inherit, ctx.max_chars, group=modality.name, rep=rep,
            )  # fmt: skip
            artifact_hash = Artifact({chosen.filename: content}).sha256
            format_fail = False
            result = with_answer(task, chosen, result, path)
        exec_ok = result.ok
        (path / RESPONSE_FILENAME).write_text(reply.raw_text)

        step: ModTermination | None = None
        if result.ok:
            step = ModTermination.EXEC_OK
        elif attempt == ctx.max_attempts:
            step = ModTermination.FORMAT_FAIL if format_fail else ModTermination.BUDGET_EXHAUSTED
        feedback_sent = None
        if step is None:
            # Só o erro de execução (ou de formato) volta ao agente, nunca o check.
            feedback = Feedback(
                Stage.SANDBOX, "Format" if format_fail else "Execution", result.output
            )
            history.append(
                Turn(
                    Artifact({chosen.filename: content}) if content is not None else Artifact({}),
                    feedback,
                    # Forma livre: sempre a resposta bruta (a linguagem do bloco não é fixa).
                    raw_text=reply.raw_text if content is None or ctx.free_choice else None,
                )
            )
            feedback_sent = result.output
        recorder.record(
            {
                **base,
                "event": "attempt",
                "attempt": attempt,
                "model_id": reply.model_id,
                "request_id": reply.request_id,
                "params": reply.params,
                "format_ok": not format_fail,
                "exec_ok": result.ok,
                "unsupported": result.unsupported,
                "uses_local_exec": uses_local_exec(chosen or modality, content),
                "output": result.output,
                "raw_output": result.raw,
                "feedback_sent": feedback_sent,
                "termination": step.value if step else None,
                "input_tokens": reply.usage.input_tokens,
                "output_tokens": reply.usage.output_tokens,
                "duration_s": round(time.monotonic() - start, 3),
                "artifact_path": str(path),
                "artifact_hash": artifact_hash,
                "answer": (_answer(path, ctx.max_chars) if task.wants_answer else None),
                # E4: a escolha só na forma livre (os outros runs não mudam)
                **({"chosen": choices[-1]} if ctx.free_choice else {}),
            }
        )
        if step is not None:
            termination = step
            break

    # Nota: uma vez, sobre o estado da última execução (o Floci não foi resetado desde então).
    if termination is ModTermination.INITIAL_FAIL:
        check_pass, details = False, {"error": "initial.tf não aplicou"}
    else:
        check_pass, details = run_check(ctx.toolchain, task, path, settle=True)
    asked = termination is ModTermination.ASKED
    success = asked if task.expect == "ask" else (exec_ok and check_pass)
    final = {
        **base,
        "event": "final",
        "model_id": model_id,
        "params": params,
        "prompt_version": prompt_version(ctx.allow_question, ctx.free_choice),
        "attempts": attempt,
        "termination": termination.value,
        "exec_ok": exec_ok,
        "check_pass": check_pass,
        "success": success,
        "check_details": details,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
    }
    if ctx.allow_question:
        # Campos do E2 só com a pergunta liberada: os finals dos outros runs não mudam.
        final.update(expect=task.expect, asked=asked, question=question, both_files=both_files)
    if ctx.free_choice:
        # Campos do E4 só na forma livre: os finals dos outros runs não mudam.
        picked = [c for c in choices if c is not None]
        final.update(
            chosen=choices[-1] if choices else None,
            chosen_first=choices[0] if choices else None,
            switched=len(set(picked)) > 1,
        )
    recorder.record(final)
    return final
