"""Comandos modality-* e ambig-prevalidate (tarefas × modalidades Terraform/CLI/boto3)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Container
from pathlib import Path
from typing import Any

from iac_harness.agents.anthropic_backend import DEFAULT_MAX_TOKENS, AnthropicBackend
from iac_harness.cli.common import (
    DATA_DIR,
    _allow_question,
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
from iac_harness.cli.tables import (
    _free_table,
    _modality_metrics_table,
    _modality_summary,
    _modality_table,
    _replay_table,
)
from iac_harness.datasets import ambig_iac
from iac_harness.datasets.iac_eval_v2 import IacEvalV2Adapter
from iac_harness.executors import final_cleanup
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import UNSUPPORTED_PATTERNS_VERSION, FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.metrics import load_events
from iac_harness.modalities import prompts as mod_prompts
from iac_harness.modalities.execute import truncate
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.metrics import (
    compare_free_to_baseline,
    compute_modality_metrics,
    compute_replay_metrics,
)
from iac_harness.modalities.replay import prevalidate_pair, replay_pair
from iac_harness.modalities.runner import (
    DEFAULT_MAX_ATTEMPTS,
    ModRunContext,
    run_pair,
)
from iac_harness.modalities.spec import FREE, MODALITIES
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.toolchain import (
    Toolchain,
)
from iac_harness.workspace import pair_dir

MODEL_BACKENDS = {"anthropic": AnthropicBackend}


# ---------- modality-prevalidate ----------


def cmd_modality_prevalidate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain, allow_real_aws=True)
    tasks, modalities = _select_modality_run(cfg, args.tasks)
    max_chars = cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS)
    runs_dir = Path(cfg["run"].get("runs_dir", "runs"))
    run_id = new_run_id("modality-prevalidate")
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "prevalidation_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "tasks": [t.task_id for t in tasks],
                "modalities": modalities,
                **_git_provenance(),
                "versions": {
                    **toolchain.versions(run_dir),
                    "toolchain_image": toolchain.image,
                    "unsupported_patterns": UNSUPPORTED_PATTERNS_VERSION,
                    **_sandbox_provenance(executor),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    recorder = JsonlRecorder(run_dir / "prevalidation.jsonl")
    rows = []
    try:
        for task in tasks:
            for name in modalities:
                executor.reset()
                row = prevalidate_pair(
                    task, MODALITIES[name], toolchain, executor, runs_dir, run_id, max_chars
                )
                recorder.record(row)
                rows.append(row)
                print(f"{task.task_id} × {name}: valid={row['valid']}", file=sys.stderr)
    finally:
        recorder.close()
        final_cleanup(executor)
    print(_modality_table(rows, modalities), file=sys.stderr)
    no_ref = sum(1 for r in rows if r.get("ref_missing"))
    print(
        f"válidas: {sum(1 for r in rows if r['valid'])}, "
        f"inválidas: {sum(1 for r in rows if r['valid'] is False)}, sem referência: {no_ref}",
        file=sys.stderr,
    )
    print(run_dir / "prevalidation.jsonl")
    return 0


# ---------- ambig-prevalidate (E16) ----------


def cmd_ambig_prevalidate(args: argparse.Namespace) -> int:
    """Gate do Ambig-IaC na ordem do sorteio até `--target-pass` aprovadas (ou `--tasks`)."""
    cfg = load_config(args.config)
    if cfg.get("modality", {}).get("dataset") != ambig_iac.DATASET:
        raise SystemExit(f"erro: a config precisa de [modality] dataset = {ambig_iac.DATASET!r}")
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    tasks = {t.task_id: t for t in _load_cfg_tasks(cfg)}
    if args.tasks:
        order = args.tasks
    elif args.order:
        order = json.loads(args.order.read_text())["order"]
    else:
        raise SystemExit("erro: informe --order <selection.json> ou --tasks")
    missing = set(order) - set(tasks)
    if missing:
        raise SystemExit(f"tarefas não encontradas: {sorted(missing)}")
    runs_dir = Path(cfg["run"].get("runs_dir", "runs"))
    run_dir = runs_dir / new_run_id("ambig-prevalidate")
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "prevalidation_config.json").write_text(
        json.dumps(
            {
                "config": cfg, "order": order, "target_pass": args.target_pass,
                "dataset": _task_source(cfg).provenance(), **_git_provenance(),
                "versions": {**toolchain.versions(run_dir), "toolchain_image": toolchain.image,
                             **_sandbox_provenance(executor)},
            },
            indent=2, ensure_ascii=False,
        )
    )  # fmt: skip
    recorder = JsonlRecorder(run_dir / "prevalidation.jsonl")
    passed = 0
    try:
        executor.reset()  # o plan só lê o Floci (data sources): uma conta limpa basta
        for task_id in order:
            if args.target_pass and passed >= args.target_pass:
                break
            row = ambig_iac.gate_task(toolchain, tasks[task_id], run_dir / task_id)
            recorder.record(row)
            passed += row["gate_pass"]
            print(f"{task_id}: {'ok' if row['gate_pass'] else row['reason']}", file=sys.stderr)
    finally:
        recorder.close()
        final_cleanup(executor)
    print(f"aprovadas: {passed}", file=sys.stderr)
    print(run_dir / "prevalidation.jsonl")
    return 0


# ---------- modality-replay ----------


REPLAY_CATEGORIES = ("modify", "query")


def _replay_plan(
    events: list[dict[str, Any]], tasks: dict[str, Any]
) -> list[tuple[dict[str, Any], dict[str, Any] | None, str | None]]:
    """(final, attempt da última tentativa ou None, motivo do pulo ou None), na ordem do run.

    Pula sem executar: INITIAL_FAIL (sem tentativa), FORMAT_FAIL e artefato ausente no disco.
    """
    attempts = {
        (e["task_id"], e["modality"], e["rep"], e["attempt"]): e
        for e in events
        if e.get("event") == "attempt"
    }
    plan = []
    for final in events:
        if final.get("event") != "final" or final["task_id"] not in tasks:
            continue
        key = (final["task_id"], final["modality"], final["rep"], final["attempts"])
        attempt = attempts.get(key)
        reason = None
        if attempt is None:
            reason = f"sem evento attempt ({final['termination']})"
        elif not attempt.get("format_ok", True):
            reason = "FORMAT_FAIL (sem artefato)"
        elif not (
            Path(attempt["artifact_path"]) / MODALITIES[final["modality"]].filename
        ).is_file():
            reason = "artefato ausente no disco"
        plan.append((final, attempt, reason))
    return plan


def cmd_modality_replay(args: argparse.Namespace) -> int:
    """Reexecuta no sandbox o artefato final de cada trajetória (sem LLM) e mede o colateral."""
    src = Path(args.src_run_dir)
    saved = json.loads((src / "run_config.json").read_text(encoding="utf-8"))
    cfg = saved["config"]
    # Replay só no Floci: reexecutar artefatos de agente na conta real não é aceito.
    if cfg.get("sandbox", {}).get("kind", FlociExecutor.KIND) == RealAwsExecutor.KIND:
        raise SystemExit("modality-replay só roda no Floci: o run de origem usou a AWS real")
    events = load_events(src / "events.jsonl")
    in_run = {e["task_id"] for e in events if e.get("event") == "final"}
    all_tasks = {t.task_id: t for t in load_tasks()}
    if args.tasks:
        unknown = set(args.tasks) - in_run
        if unknown:
            raise SystemExit(f"tarefas não encontradas no run: {sorted(unknown)}")
        no_snap = sorted(t for t in args.tasks if all_tasks[t].collateral is None)
        if no_snap:
            raise SystemExit(f"tarefas sem collateral.py: {no_snap}")
    wanted = set(args.tasks or in_run)
    tasks = {
        t: all_tasks[t]
        for t in sorted(wanted & in_run & set(all_tasks))
        if all_tasks[t].category in args.categories and all_tasks[t].collateral is not None
    }
    if not tasks:
        raise SystemExit("nenhuma tarefa com collateral.py nas categorias pedidas")
    plan = _replay_plan(events, tasks)
    max_chars = cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS)
    runs_dir = src.parent
    run_id = new_run_id(f"replay-{src.name}")
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    (run_dir / "run_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "src_run": str(src),
                "src_git_commit": saved.get("git_commit"),
                "tasks": sorted(tasks),
                "categories": list(args.categories),
                **_git_provenance(),
                "versions": {
                    **toolchain.versions(run_dir),
                    "toolchain_image": toolchain.image,
                    "unsupported_patterns": UNSUPPORTED_PATTERNS_VERSION,
                    **_sandbox_provenance(executor),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    recorder = JsonlRecorder(run_dir / "events.jsonl")
    try:
        for final, attempt, reason in plan:
            task = tasks[final["task_id"]]
            modality = MODALITIES[final["modality"]]
            event: dict[str, Any] = {
                "event": "replay", "run_id": run_id, "src_run": str(src),
                "task_id": task.task_id, "category": task.category, "modality": modality.name,
                "rep": final["rep"], "src_attempt": final["attempts"],
                "src_termination": final["termination"],
                "check_pass_original": final["check_pass"], "skipped_reason": reason,
                "initial_ok": None, "exec_ok": None, "output": "", "check_pass": None,
                "check_details": {}, "collateral_ok": None, "collateral_details": {},
            }  # fmt: skip
            if reason is None:
                content = (Path(attempt["artifact_path"]) / modality.filename).read_text()
                executor.reset()
                row = replay_pair(
                    task, modality, content, toolchain, executor, runs_dir, run_id,
                    rep=final["rep"], max_chars=max_chars,
                )  # fmt: skip
                event.update(row, output=truncate(row["output"], max_chars))
            recorder.record(event)
            print(
                f"{task.task_id} × {modality.name} rep {final['rep']}: "
                f"collateral_ok={event['collateral_ok']} check={event['check_pass']} "
                f"(orig {event['check_pass_original']})" + (f" pulado: {reason}" if reason else ""),
                file=sys.stderr,
            )
    finally:
        recorder.close()
        final_cleanup(executor)
    metrics = compute_replay_metrics(load_events(run_dir / "events.jsonl"))
    print(_replay_table(metrics), file=sys.stderr)
    print(run_dir)
    return 0


# ---------- modality-run ----------


MODALITY_UNIT = ("task_id", "modality", "rep")


def _modality_backends(cfg: dict[str, Any], modalities: list[str]) -> dict[str, Any]:
    """Um backend por modalidade: system prompt e repair próprios, versão mod_prompts."""
    allow_question = _allow_question(cfg)
    if modalities == [FREE.name]:
        # Forma livre: o histórico usa sempre a resposta bruta, então code_lang não é usado.
        return {
            FREE.name: _build_backend(
                cfg,
                system_prompt=mod_prompts.free_system_prompt(),
                repair_template=mod_prompts.FREE_REPAIR_TEMPLATE,
                prompt_version=mod_prompts.FREE_PROMPT_VERSION,
                code_lang="text",
            )
        }
    return {
        name: _build_backend(
            cfg,
            system_prompt=mod_prompts.system_prompt(MODALITIES[name], allow_question),
            repair_template=mod_prompts.repair_template(MODALITIES[name]),
            prompt_version=mod_prompts.prompt_version(allow_question),
            code_lang=mod_prompts.CODE_LANG[name],
        )
        for name in modalities
    }


def _modality_loop(
    cfg: dict[str, Any],
    tasks: list[Any],
    modalities: list[str],
    reps: int,
    runs_dir: Path,
    run_id: str,
    toolchain: Toolchain,
    executor: FlociExecutor,
    skip: Container[tuple] = frozenset(),
) -> None:
    """Rep externo: as modalidades de uma tarefa rodam lado a lado no tempo (menos deriva)."""
    ctx = ModRunContext(
        run_id=run_id,
        runs_dir=runs_dir,
        toolchain=toolchain,
        executor=executor,
        model_provider=cfg["agent"]["provider"],
        max_attempts=cfg.get("modality", {}).get("max_attempts", DEFAULT_MAX_ATTEMPTS),
        max_chars=cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS),
        allow_question=_allow_question(cfg),
        free_choice=FREE.name in modalities,
    )
    backends = _modality_backends(cfg, modalities)
    recorder = JsonlRecorder(runs_dir / run_id / "events.jsonl")
    try:
        for rep in range(1, reps + 1):
            for task in tasks:
                for name in modalities:
                    if (task.task_id, name, rep) in skip:
                        continue
                    modality = FREE if name == FREE.name else MODALITIES[name]
                    final = run_pair(task, modality, rep, backends[name], recorder, ctx)
                    print(
                        f"{task.task_id} × {name} rep {rep}: success={final['success']} "
                        f"check={final['check_pass']} "
                        f"{final['termination']} ({final['attempts']} tent.)",
                        file=sys.stderr,
                    )
    finally:
        recorder.close()
        final_cleanup(executor)  # modality-run e --resume passam por aqui


def cmd_modality_run(args: argparse.Namespace) -> int:
    if args.resume is not None:
        return cmd_modality_resume(args)
    if args.config is None:
        raise SystemExit("erro: informe --config (ou --resume RUN_DIR)")
    cfg = load_config(args.config)
    tasks, modalities = _select_modality_run(cfg, args.tasks, args.modalities)
    reps = args.reps or cfg["run"].get("reps", 1)
    _modality_backends(cfg, modalities)  # falha cedo (ex.: sem chave) antes de criar o run
    runs_dir = Path(cfg["run"].get("runs_dir", "runs"))
    run_id = new_run_id(cfg["run"]["name"])
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain, allow_real_aws=True)
    (run_dir / "run_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "reps": reps,
                "tasks": [t.task_id for t in tasks],
                "modalities": modalities,
                "prompt_version": mod_prompts.prompt_version(
                    _allow_question(cfg), FREE.name in modalities
                ),
                **_git_provenance(),
                "versions": {
                    **toolchain.versions(run_dir),
                    "toolchain_image": toolchain.image,
                    "unsupported_patterns": UNSUPPORTED_PATTERNS_VERSION,
                    **_sandbox_provenance(executor),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    _modality_loop(cfg, tasks, modalities, reps, runs_dir, run_id, toolchain, executor)
    print(_modality_summary(load_events(run_dir / "events.jsonl")), file=sys.stderr)
    print(run_dir)
    return 0


def cmd_modality_resume(args: argparse.Namespace) -> int:
    given = [
        f"--{o}" for o in ("config", "tasks", "modalities", "reps") if getattr(args, o) is not None
    ]
    if given:
        raise SystemExit(
            f"erro: --resume não aceita {', '.join(given)} (a configuração vem do run_config.json)"
        )
    run_dir = Path(args.resume)
    config_path, saved = _load_run_config(run_dir)
    cfg, reps, modalities = saved["config"], saved["reps"], saved["modalities"]
    tasks, _ = _select_modality_run(cfg, saved["tasks"], modalities) if saved["tasks"] else ([], [])
    runs_dir, run_id = run_dir.parent, run_dir.name

    def unit_dir(key: tuple) -> Path:
        task_id, modality, rep = key
        return pair_dir(runs_dir, run_id, task_id, rep) / modality

    complete, discarded = discard_incomplete(runs_dir, run_id, None, MODALITY_UNIT, unit_dir)
    planned = [(t.task_id, m, r) for r in range(1, reps + 1) for t in tasks for m in modalities]
    pending = [p for p in planned if p not in complete]
    for key in pending:  # diretório sem evento (queda antes do 1º registro)
        shutil.rmtree(unit_dir(key), ignore_errors=True)
    _record_resume(
        config_path, saved, run_id, len(planned) - len(pending), len(discarded), len(pending),
        "pairs",
    )  # fmt: skip
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain, allow_real_aws=True)
    _modality_loop(cfg, tasks, modalities, reps, runs_dir, run_id, toolchain, executor, complete)
    print(_modality_summary(load_events(run_dir / "events.jsonl")), file=sys.stderr)
    print(run_dir)
    return 0


def _modality_difficulty() -> dict[str, int]:
    """task_id → dificuldade do IaC-Eval v2, pelo `origin` do meta.toml; "authored" fica fora."""
    prefix = "iac-eval-v2:"
    origins = {
        t.task_id: t.origin[len(prefix) :] for t in load_tasks() if t.origin.startswith(prefix)
    }
    if not origins:
        return {}
    by_id = {task.task_id: task.difficulty for task, _ in IacEvalV2Adapter(DATA_DIR).load()}
    return {t: by_id[o] for t, o in origins.items() if o in by_id}


def cmd_modality_metrics(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    cfg = json.loads((run_dir / "run_config.json").read_text())["config"]
    prices = {m: tuple(p) for m, p in cfg.get("prices", {}).items()}
    events = load_events(run_dir / "events.jsonl")
    if any(e.get("event") == "replay" for e in events):
        # Run de modality-replay: resumo descritivo do colateral (sem bootstrap).
        replay = compute_replay_metrics(events)
        print(_replay_table(replay) if args.format == "table" else
              json.dumps(replay, indent=2, ensure_ascii=False))  # fmt: skip
        return 0
    excluded = sorted(set(args.exclude_tasks or ()))
    # guarda contra erro de digitação: só exclui tarefas que existem no run
    missing = set(excluded) - {e["task_id"] for e in events if "task_id" in e}
    if missing:
        raise SystemExit(f"tarefas não encontradas no run: {sorted(missing)}")
    events = [e for e in events if e.get("task_id") not in excluded]
    metrics = compute_modality_metrics(events, prices, difficulty=_modality_difficulty())
    metrics["excluded_tasks"] = excluded
    if args.baseline is not None:
        # E4: forma livre contra um run de forma fixa (as mesmas exclusões valem nos dois)
        base_events = load_events(Path(args.baseline) / "events.jsonl")
        base_events = [e for e in base_events if e.get("task_id") not in excluded]
        metrics["baseline"] = {
            "run_dir": str(args.baseline),
            **compare_free_to_baseline(events, base_events),
        }
    if args.format == "table":
        if excluded:
            print(f"tarefas excluídas: {', '.join(excluded)}")
        print(_modality_metrics_table(metrics))
        if "free" in metrics:
            print(_free_table(metrics))
    else:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


# ---------- backend do agente (modality-run) ----------


def _build_backend(cfg: dict[str, Any], **prompt_kwargs: Any) -> Any:
    """Backend do agente; montado só depois de validadas tarefas e condições.

    `prompt_kwargs` (system_prompt, repair_template, ...) só no teste de modalidades.
    """
    agent_cfg = cfg["agent"]
    return MODEL_BACKENDS[agent_cfg["provider"]](
        model=agent_cfg["model"],
        max_tokens=agent_cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        effort=agent_cfg.get("effort"),
        **prompt_kwargs,
    )
