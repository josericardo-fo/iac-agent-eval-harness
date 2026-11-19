"""Comandos agent-*: o agente real (Claude Code headless, em container), juízes e métricas."""

from __future__ import annotations

import argparse
import csv
import json
import queue
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from iac_harness.agentic.agents import AGENTS, DEFAULT_AGENT_IMAGE, ClaudeCodeAgent, CodingAgent
from iac_harness.agentic.conditions import load_toolkits, resolve_toolkit
from iac_harness.agentic.judge import JUDGE_FILENAME, Judge, LlmJudge
from iac_harness.agentic.metrics import (
    JUDGE_SAMPLE_FIELDS,
    compare_runs,
    compute_agent_metrics,
    judge_agreement,
    judge_pair_agreement,
    judge_sample,
    merge_judgments,
)
from iac_harness.agentic.question_judge import (
    QUESTION_MAX_TOKENS,
    QuestionJudge,
    meta_header,
    select_asked,
)
from iac_harness.agentic.question_metrics import (
    DISAGREEMENT_FIELDS,
    DISAGREEMENTS_FILENAME,
    merge_questions,
    question_agreement,
    questions_report,
)
from iac_harness.agentic.runner import (
    AGENT_PROMPT_VERSION,
    AGENT_UNIT,
    HARNESS_FAILURES,
    AgentRunContext,
    agent_prompt,
    run_agent_pair,
)
from iac_harness.agentic.runner import unit_dir as agent_unit_dir
from iac_harness.agentic.strata import DEFAULT_STRATA_FILE, load_strata
from iac_harness.agentic.targets import FLOCI_WORKERS, Target
from iac_harness.agentic.user_sim import DEFAULT_MAX_REPLIES, USER_SIM_PROMPT_VERSION, UserSimulator
from iac_harness.cli.common import (
    _load_cfg_tasks,
    _load_run_config,
    _record_resume,
    _select_modality_run,
    _task_source,
    build_executor,
    build_toolchain,
    discard_incomplete,
    load_config,
    new_run_id,
)
from iac_harness.cli.provenance import _git_provenance, _sandbox_provenance
from iac_harness.cli.tables import _agent_metrics_table, _agent_summary
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import UNSUPPORTED_PATTERNS_VERSION
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.llm import TextModel, build_text_model, default_model
from iac_harness.metrics import load_events
from iac_harness.modalities.loader import TASKS_DIR as MODALITY_TASKS_DIR
from iac_harness.recorder.jsonl import JsonlRecorder

# Raiz dos plugins dos toolkits (toolkit/, third_party/).
AGENT_PLUGIN_ROOT = Path(__file__).resolve().parents[3]


def _agentic_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    ag = cfg.get("agentic")
    if not ag:
        raise SystemExit("erro: config sem a seção [agentic]")
    unknown = set(ag.get("conditions", [])) - set(load_toolkits())
    if unknown:
        raise SystemExit(f"condições desconhecidas: {sorted(unknown)}")
    return ag


def _agent_workers(cfg: dict[str, Any]) -> int:
    """W workers, um por Floci; o alvo aws roda em série (o sweep é da conta inteira)."""
    workers = int(cfg["agentic"].get("workers", 1))
    if cfg.get("sandbox", {}).get("kind") == RealAwsExecutor.KIND:
        if workers != 1:
            print(f"aviso: alvo aws força workers = 1 (config pedia {workers})", file=sys.stderr)
        return 1
    if not 1 <= workers <= len(FLOCI_WORKERS):
        raise SystemExit(f"erro: workers deve estar entre 1 e {len(FLOCI_WORKERS)}")
    return workers


def _worker_cfg(cfg: dict[str, Any], worker: int) -> dict[str, Any]:
    """Config do worker `worker` (0 = o Floci da config; 1.. = floci2.., compose agent-workers)."""
    if worker == 0:
        return cfg
    floci = FLOCI_WORKERS[worker]
    sandbox = {
        **cfg.get("sandbox", {}),
        "endpoint": floci.endpoint,
        "host_endpoint": floci.host_endpoint,
        "container": floci.container,
    }
    return {**cfg, "sandbox": sandbox}


def _build_target(cfg: dict[str, Any]) -> Target:
    return build_executor(cfg, build_toolchain(cfg), allow_real_aws=True).agent_target()


def _build_targets(cfg: dict[str, Any]) -> list[Target]:
    """Um alvo por worker, montados uma vez por comando."""
    return [_build_target(_worker_cfg(cfg, i)) for i in range(_agent_workers(cfg))]


def _build_agent(cfg: dict[str, Any]) -> CodingAgent:
    ag = cfg["agentic"]
    name = ag.get("agent", ClaudeCodeAgent.name)
    if name not in AGENTS:
        raise SystemExit(f"erro: agente desconhecido {name!r} (use um de {sorted(AGENTS)})")
    return AGENTS[name](
        image=ag.get("image", DEFAULT_AGENT_IMAGE),
        max_turns=ag.get("max_turns", 60),
        timeout_s=ag.get("timeout_s", 1200),
    )


def _build_judge(
    section: dict[str, Any],
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
) -> Judge:
    """Juiz da seção `[judge]` (`provider` e `model` obrigatórios, sem padrão escondido).

    `provider` sobrescreve o da config; se difere dele, o `model` da config não é herdado
    (vale o padrão do provedor escolhido na CLI, a menos que `model` seja dado).
    """
    section = _judge_section(section, model, provider, reasoning_effort)
    return LlmJudge(llm=_text_model(section, "judge"))


def _text_model(section: dict[str, Any], name: str) -> TextModel:
    """Modelo de texto da seção `[name]`; chave ausente ou provedor desconhecido encerra."""
    try:
        return build_text_model(section)
    except ValueError as exc:
        raise SystemExit(f"erro: [{name}] {exc}") from exc


def _judge_section(
    section: dict[str, Any],
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Seção `[judge]` com as opções da CLI por cima (regras em `_build_judge`)."""
    if provider and provider != section.get("provider"):
        section = {k: v for k, v in section.items() if k != "model"}
        model = model or default_model(provider)
    return {
        **section,
        **({"provider": provider} if provider else {}),
        **({"model": model} if model else {}),
        **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
    }


class UserSimSetup(NamedTuple):
    """Usuário simulado e o juiz inline que classifica a mensagem do agente."""

    simulator: UserSimulator
    judge: Judge


def _build_user_sim(cfg: dict[str, Any]) -> UserSimSetup | None:
    """Usuário simulado e o juiz inline que decide se o agente perguntou; None sem `[user_sim]`."""
    section = cfg.get("user_sim")
    if section is None:
        return None
    if section.get("max_replies", DEFAULT_MAX_REPLIES) < 1:
        raise SystemExit("erro: [user_sim] max_replies deve ser >= 1")
    llm = _text_model(section, "user_sim")
    sim = UserSimulator(
        llm,
        section.get("max_replies", DEFAULT_MAX_REPLIES),
        section.get("prompt_version", USER_SIM_PROMPT_VERSION),
    )
    return UserSimSetup(sim, _build_judge(cfg.get("judge", {})))


def _agent_plan(cfg: dict[str, Any], tasks: list[Any], reps: int) -> list[tuple]:
    """Unidades (tarefa, condição, modelo, rep); rep externo, como no modality-run."""
    ag = cfg["agentic"]
    return [
        (t, c, m, r)
        for r in range(1, reps + 1)
        for t in tasks
        for m in ag["models"]
        for c in ag["conditions"]
    ]


def _agent_loop(
    cfg: dict[str, Any], plan: list[tuple], runs_dir: Path, run_id: str, agent: CodingAgent,
    targets: list[Target], user_sim: UserSimSetup | None = None,
) -> None:  # fmt: skip
    """Distribui as unidades entre os workers (um alvo cada); o recorder é compartilhado."""
    ag = cfg["agentic"]
    strata = load_strata(Path(ag.get("strata_file", DEFAULT_STRATA_FILE)))
    for cond in ag["conditions"]:  # falha cedo: plugin ausente
        resolve_toolkit(cond, AGENT_PLUGIN_ROOT)
    recorder = JsonlRecorder(runs_dir / run_id / "events.jsonl")
    ctx = AgentRunContext(
        run_id=run_id, runs_dir=runs_dir, agent=agent,
        plugin_root=AGENT_PLUGIN_ROOT, strata=strata,
        max_chars=cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS),
        user_sim=user_sim.simulator if user_sim else None,
        sim_judge=user_sim.judge if user_sim else None,
    )  # fmt: skip
    pending: queue.SimpleQueue[tuple] = queue.SimpleQueue()
    for unit in plan:
        pending.put(unit)
    errors: list[BaseException] = []

    def worker(index: int, target: Target) -> None:
        try:
            while not errors:
                try:
                    task, cond, model, rep = pending.get_nowait()
                except queue.Empty:
                    return
                final = run_agent_pair(task, cond, model, rep, target, recorder, ctx)
                print(
                    f"[w{index + 1}] {task.task_id} × {cond} × {model} rep {rep}: "
                    f"check={final['check_pass']} {final['termination']} "
                    f"({final['duration_s']:.0f}s, US$ {final['cost_usd'] or 0:.4f})",
                    file=sys.stderr,
                )
        except BaseException as exc:  # um worker que cai para os demais (sandbox sujo)
            errors.append(exc)
            raise
        finally:
            target.cleanup()

    threads = [
        threading.Thread(target=worker, args=(i, t), name=f"agent-w{i + 1}")
        for i, t in enumerate(targets)
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        recorder.close()
    if errors:
        raise errors[0]


def _agent_run_config(
    cfg: dict[str, Any], tasks: list[Any], reps: int, run_dir: Path, agent: CodingAgent,
    targets: list[Target], user_sim: UserSimSetup | None = None,
) -> dict[str, Any]:  # fmt: skip
    toolchain, executor = targets[0].toolchain, targets[0].executor
    sim_provenance = (
        {"user_sim": user_sim.simulator.params, "user_sim_judge": user_sim.judge.params}
        if user_sim
        else {}
    )
    source_provenance = _task_source(cfg).provenance()
    dataset = {"dataset": source_provenance} if source_provenance else {}
    return {
        **sim_provenance,
        **dataset,
        "config": cfg,
        "reps": reps,
        "tasks": [t.task_id for t in tasks],
        "conditions": cfg["agentic"]["conditions"],
        "models": cfg["agentic"]["models"],
        "workers": len(targets),
        "prompt_version": AGENT_PROMPT_VERSION,
        **_git_provenance(),
        "versions": {
            **toolchain.versions(run_dir),
            "toolchain_image": toolchain.image,
            "unsupported_patterns": UNSUPPORTED_PATTERNS_VERSION,
            **agent.version(),
            **_sandbox_provenance(executor),
        },
    }


def cmd_agent_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    _agentic_cfg(cfg)
    tasks, _ = _select_modality_run(cfg, args.tasks, ["terraform"])
    reps = args.reps or cfg["run"].get("reps", 1)
    runs_dir = Path(cfg["run"].get("runs_dir", "runs"))
    run_id = new_run_id(cfg["run"]["name"])
    run_dir = runs_dir / run_id
    agent, targets, user_sim = _build_agent(cfg), _build_targets(cfg), _build_user_sim(cfg)
    run_dir.mkdir(parents=True, exist_ok=False)
    run_config = _agent_run_config(cfg, tasks, reps, run_dir, agent, targets, user_sim)
    (run_dir / "run_config.json").write_text(json.dumps(run_config, indent=2, ensure_ascii=False))
    _agent_loop(cfg, _agent_plan(cfg, tasks, reps), runs_dir, run_id, agent, targets, user_sim)
    print(_agent_summary(load_events(run_dir / "events.jsonl")), file=sys.stderr)
    print(run_dir)
    return 0


def _move_to_discarded(run_dir: Path, path: Path) -> bool:
    """Move `path` (se existir) para `<run>/discarded/<caminho relativo>`; True se moveu.

    Rename só exige escrita nos diretórios pai, então arquivos de outro dono (container) não
    atrapalham. Evita sobrescrever um descarte anterior acrescentando `.1`, `.2`, ...
    """
    if not path.exists():
        return False
    dest = run_dir / "discarded" / path.relative_to(run_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    final, n = dest, 0
    while final.exists():
        n += 1
        final = dest.with_name(f"{dest.name}.{n}")
    path.rename(final)
    return True


def cmd_agent_resume(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    config_path, saved = _load_run_config(run_dir)
    cfg, reps = saved["config"], saved["reps"]
    tasks, _ = _select_modality_run(cfg, saved["tasks"], ["terraform"])
    runs_dir, run_id = run_dir.parent, run_dir.name

    def udir(key: tuple) -> Path:
        task_id, cond, model, rep = key
        return agent_unit_dir(runs_dir, run_id, task_id, cond, model, rep)

    complete, discarded = discard_incomplete(runs_dir, run_id, None, AGENT_UNIT, udir)
    plan = _agent_plan(cfg, tasks, reps)
    pending = [u for u in plan if (u[0].task_id, u[1], u[2], u[3]) not in complete]
    # Diretório sem `final` (queda antes do 1º evento, ou resto que o rmtree não apagou por causa
    # do dono dos arquivos): vai para discarded/ em vez de apagar, e libera o caminho do worker.
    orphans = sum(
        _move_to_discarded(run_dir, udir((task.task_id, cond, model, rep)))
        for task, cond, model, rep in pending
    )
    _record_resume(
        config_path, saved, run_id, len(plan) - len(pending), len(discarded) + orphans,
        len(pending), "units",
    )  # fmt: skip
    _agent_loop(
        cfg, pending, runs_dir, run_id, _build_agent(cfg), _build_targets(cfg), _build_user_sim(cfg)
    )
    print(_agent_summary(load_events(run_dir / "events.jsonl")), file=sys.stderr)
    print(run_dir)
    return 0


JUDGE_SAMPLE_FILENAME = "judge_sample.csv"


def _mutated(e: dict[str, Any]) -> bool:
    """Se a trajetória mudou o estado AWS; evento antigo sem `mutated`: verified_state não None."""
    if "mutated" in e:
        return bool(e["mutated"])
    return e.get("verified_state") is not None


def _agent_judge_sample(run_dir: Path, n: int, seed: int) -> int:
    """Amostra para a leitura à mão: judge_sample.csv com `human_kind` vazio."""
    judge_path = run_dir / JUDGE_FILENAME
    if not judge_path.exists():
        raise SystemExit(f"erro: {judge_path} não encontrado (rode agent-judge antes)")
    rows = judge_sample(load_events(run_dir / "events.jsonl"), load_events(judge_path), n, seed)
    out = run_dir / JUDGE_SAMPLE_FILENAME
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, JUDGE_SAMPLE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} trajetórias amostradas (seed {seed})", file=sys.stderr)
    print(out)
    return 0


def _agent_judge_agreement(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    try:
        print(json.dumps(judge_agreement(rows), indent=2, ensure_ascii=False))
    except ValueError as exc:
        raise SystemExit(f"erro: {exc}") from exc
    return 0


def _agent_judge_compare(run_dir: Path, other_name: str) -> int:
    """Concordância entre judge.jsonl e outro arquivo de vereditos do mesmo run (sem LLM)."""
    a_path, b_path = run_dir / JUDGE_FILENAME, run_dir / other_name
    for path in (a_path, b_path):
        if not path.exists():
            raise SystemExit(f"erro: {path} não existe")
    events = [e for e in load_events(run_dir / "events.jsonl") if e.get("event") == "final"]
    result = judge_pair_agreement(events, load_events(a_path), load_events(b_path))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_agent_judge(args: argparse.Namespace) -> int:
    """Classifica a mensagem final de cada trajetória ainda sem veredito (judge.jsonl).

    `--sample N` amostra trajetórias já julgadas para a leitura à mão; `--agreement CSV` compara
    o juiz com a leitura à mão; `--compare-with ARQ` compara judge.jsonl com outro juiz. Nenhum
    dos três chama o juiz. `--out` grava os vereditos em outro arquivo (segundo juiz).
    """
    if args.agreement is not None:
        return _agent_judge_agreement(args.agreement)
    if args.run_dir is None:
        raise SystemExit("erro: informe o diretório do run (ou --agreement <csv>)")
    run_dir = Path(args.run_dir)
    if args.sample is not None:
        return _agent_judge_sample(run_dir, args.sample, args.seed)
    if args.compare_with is not None:
        return _agent_judge_compare(run_dir, args.compare_with)
    cfg = json.loads((run_dir / "run_config.json").read_text())["config"]
    judge = _build_judge(cfg.get("judge", {}), args.model, args.provider, args.reasoning_effort)
    judge_path = run_dir / args.out
    done = set()
    if judge_path.exists() and not args.redo:
        done = {j["trajectory_id"] for j in load_events(judge_path)}
    prompts = {t.task_id: agent_prompt(t) for t in _load_cfg_tasks(cfg)}
    finals = [e for e in load_events(run_dir / "events.jsonl") if e.get("event") == "final"]
    todo = [e for e in finals if e["trajectory_id"] not in done
            and e.get("termination") not in HARNESS_FAILURES]  # fmt: skip
    recorder = JsonlRecorder(judge_path)
    try:
        for e in todo:
            verdict = judge.classify(e.get("final_text"), prompts[e["task_id"]], _mutated(e))
            recorder.record(
                {
                    "trajectory_id": e["trajectory_id"],
                    "timestamp": datetime.now(UTC).isoformat(),
                    **judge.params,
                    **verdict,
                }
            )
            print(f"{e['trajectory_id']}: {verdict.get('kind')}", file=sys.stderr)
    finally:
        recorder.close()
    print(f"{len(todo)} classificadas, {len(done)} já tinham veredito", file=sys.stderr)
    print(judge_path)
    return 0


def cmd_agent_judge_questions(args: argparse.Namespace) -> int:
    """Nota a qualidade de cada pergunta (trajetória com `asked = true` no judge.jsonl).

    Retomável: pula as trajetórias que já têm linha no arquivo de saída.
    """
    run_dir = Path(args.run_dir)
    cfg = json.loads((run_dir / "run_config.json").read_text())["config"]
    section = _judge_section(cfg.get("judge", {}), args.model, args.provider)
    judge = QuestionJudge(_text_model({**section, "max_tokens": QUESTION_MAX_TOKENS}, "judge"))
    out_path = run_dir / args.out
    # linhas com erro são refeitas ao retomar (o relatório usa a última linha de cada id)
    done = ({j["trajectory_id"] for j in load_events(out_path) if not j.get("error")}
            if out_path.exists() else set())  # fmt: skip
    finals = [e for e in load_events(run_dir / "events.jsonl") if e.get("event") == "final"]
    todo = [e for e in select_asked(finals, load_events(run_dir / JUDGE_FILENAME))
            if e["trajectory_id"] not in done]  # fmt: skip
    if args.limit is not None:
        todo = todo[: args.limit]
    tasks_dir = Path(cfg.get("modality", {}).get("tasks_dir") or MODALITY_TASKS_DIR)
    tasks = {t.task_id: t for t in _load_cfg_tasks(cfg)}
    recorder = JsonlRecorder(out_path)
    try:
        for e in todo:
            task = tasks[e["task_id"]]
            verdict = judge.grade(
                request=agent_prompt(task),
                expect=task.expect,
                # tarefas de dataset não têm meta.toml
                meta_note=""
                if task.rego_policy is not None
                else meta_header((tasks_dir / task.task_id / "meta.toml").read_text()),
                user_card=task.user_card,
                question=e["final_text"],
            )
            recorder.record(
                {
                    "trajectory_id": e["trajectory_id"],
                    "timestamp": datetime.now(UTC).isoformat(),
                    **judge.params,
                    **verdict,
                }
            )
            print(f"{e['trajectory_id']}: {verdict['total']} {verdict['error'] or ''}",
                  file=sys.stderr)  # fmt: skip
    finally:
        recorder.close()
    print(f"{len(todo)} notadas, {len(done)} já tinham nota", file=sys.stderr)
    print(out_path)
    return 0


def cmd_agent_questions_report(args: argparse.Namespace) -> int:
    """Relatório das notas das perguntas (Markdown no stdout), sem LLM.

    Com `--compare A B`, grava os desacordos de 2 pontos em questions-disagreements.csv no run.
    """

    def grades(run_dir: Path, events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
        path = run_dir / name
        return merge_questions(events, load_events(path) if path.exists() else [], run_dir.name)

    runs = []
    for run_dir in args.run_dirs:
        events = load_events(run_dir / "events.jsonl")
        pair = None
        if args.compare:
            pair = (
                grades(run_dir, events, args.compare[0]),
                grades(run_dir, events, args.compare[1]),
            )
            with (run_dir / DISAGREEMENTS_FILENAME).open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, DISAGREEMENT_FIELDS)
                writer.writeheader()
                writer.writerows(question_agreement(*pair)["disagreements"])
        runs.append((run_dir.name, grades(run_dir, events, args.judge_file), pair))
    print(questions_report(runs, args.judge_file))
    return 0


def cmd_agent_metrics(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    events = load_events(run_dir / "events.jsonl")
    judge_path = run_dir / args.judge_file
    judgments = load_events(judge_path) if judge_path.exists() else []
    metrics = compute_agent_metrics(merge_judgments(events, judgments))
    if args.compare is not None:
        other = Path(args.compare)
        other_judge = other / JUDGE_FILENAME
        other_rows = merge_judgments(
            load_events(other / "events.jsonl"),
            load_events(other_judge) if other_judge.exists() else [],
        )
        metrics["compare"] = {
            "run_dir": str(args.compare),
            **compare_runs(merge_judgments(events, judgments), other_rows),
        }
    if args.format == "table":
        print(_agent_metrics_table(metrics))
    else:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0
