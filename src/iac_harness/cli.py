"""CLI: modality-* | ambig-prevalidate | agent-* | demo-timeline. Mapeamentos simples."""

from __future__ import annotations

import argparse
import csv
import json
import queue
import shutil
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable, Container
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from iac_harness.agentic.agents import DEFAULT_AGENT_IMAGE, ClaudeCodeAgent
from iac_harness.agentic.conditions import REGISTRY as AGENT_CONDITIONS
from iac_harness.agentic.conditions import resolve_condition
from iac_harness.agentic.judge import JUDGE_FILENAME, Judge, LlmJudge
from iac_harness.agentic.metrics import (
    JUDGE_SAMPLE_FIELDS,
    by_condition_model,
    compare_runs,
    compute_agent_metrics,
    judge_agreement,
    judge_pair_agreement,
    judge_sample,
    merge_judgments,
)
from iac_harness.agentic.question_judge import (
    QUESTION_MAX_TOKENS,
    QUESTIONS_FILENAME,
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
from iac_harness.agentic.targets import FLOCI_WORKERS, AwsTarget, FlociTarget, Target
from iac_harness.agentic.user_sim import (
    DEFAULT_MAX_REPLIES,
    USER_SIM_PROMPT_VERSION,
    UserSimulator,
)
from iac_harness.agents.anthropic_backend import DEFAULT_MAX_TOKENS, AnthropicBackend
from iac_harness.datasets import ambig_iac
from iac_harness.datasets.iac_eval_v2 import IacEvalV2Adapter
from iac_harness.demo.timeline import build_timeline, dump_timeline
from iac_harness.executors import final_cleanup
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import UNSUPPORTED_PATTERNS_VERSION, FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.llm import build_text_model
from iac_harness.metrics import load_events
from iac_harness.modalities import prompts as mod_prompts
from iac_harness.modalities.execute import (
    prevalidate_pair,
    replay_pair,
    truncate,
)
from iac_harness.modalities.loader import TASKS_DIR as MODALITY_TASKS_DIR
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.metrics import (
    compare_free_to_baseline,
    compute_modality_metrics,
    compute_replay_metrics,
)
from iac_harness.modalities.runner import (
    DEFAULT_MAX_ATTEMPTS,
    ModRunContext,
    run_pair,
)
from iac_harness.modalities.spec import FREE, MODALITIES
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.toolchain import (
    DEFAULT_ENDPOINT,
    DEFAULT_REGION,
    DEFAULT_TIMEOUT_S,
    Toolchain,
)
from iac_harness.workspace import pair_dir

BACKENDS = {"anthropic": AnthropicBackend}
EXECUTORS: dict[str, type[FlociExecutor]] = {
    cls.KIND: cls for cls in (FlociExecutor, RealAwsExecutor)
}
DATA_DIR = Path("data")
# E3: raiz dos plugins das condições (toolkit/, third_party/).
AGENT_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


# ---------- montagem ----------


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def build_toolchain(cfg: dict[str, Any]) -> Toolchain:
    tc = cfg.get("toolchain", {})
    sb = cfg.get("sandbox", {})
    return Toolchain(
        mode=tc.get("mode", "docker"),
        image=tc.get("image", Toolchain().image),
        endpoint=sb.get("endpoint", DEFAULT_ENDPOINT),
        region=sb.get("region", DEFAULT_REGION),
        timeout_s=tc.get("timeout_s", DEFAULT_TIMEOUT_S),
        # `[sandbox] kind = "aws"`: credenciais IAC_AWS_*, sem endpoint, rede com internet.
        target="aws" if sb.get("kind") == RealAwsExecutor.KIND else "emulator",
    )


def build_executor(
    cfg: dict[str, Any], toolchain: Toolchain, *, allow_real_aws: bool = False
) -> FlociExecutor:
    """`allow_real_aws` só nos comandos de modalidade, que fazem a limpeza final num `finally`."""
    sb = cfg.get("sandbox", {})
    kind = sb.get("kind", FlociExecutor.KIND)
    if kind not in EXECUTORS:
        raise SystemExit(f"[sandbox] kind desconhecido: {kind!r} (use um de {sorted(EXECUTORS)})")
    if kind == RealAwsExecutor.KIND:
        if not allow_real_aws:
            raise SystemExit(
                f"[sandbox] kind = {kind!r} só é aceito pelos comandos modality-* "
                "(este comando não faz a limpeza final da conta)"
            )
        kwargs = {k: sb[k] for k in ("expected_arn", "baseline_file") if k in sb}
        return RealAwsExecutor(toolchain, **kwargs)
    kwargs = {k: sb[k] for k in ("endpoint", "host_endpoint", "container") if k in sb}
    return EXECUTORS[kind](toolchain, **kwargs)


def new_run_id(name: str) -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{name}"


# ---------- procedência ----------


def _query(args: list[str]) -> str | None:
    """Saída de um comando externo; `None` se vazia e "erro: ..." se falhar (nunca levanta)."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"erro: {exc}"
    if proc.returncode != 0:
        return f"erro: {proc.stderr.strip()[:200]}"
    return proc.stdout.strip() or None


def _git_provenance() -> dict[str, Any]:
    commit = _query(["git", "rev-parse", "HEAD"])
    if commit is None or commit.startswith("erro:"):
        # Repositório sem commit (ou git indisponível): sem revisão para registrar.
        commit = None
    status = _query(["git", "status", "--porcelain"])
    dirty = None if status is not None and status.startswith("erro:") else status is not None
    return {"git_commit": commit, "git_dirty": dirty}


def _emulator_image(container: str) -> tuple[str | None, str | None, str | None]:
    """Imagem, ID e RepoDigests do container do emulador (tolerante a falha, como `_query`)."""
    image = _query(["docker", "inspect", "--format", "{{.Config.Image}}", container])
    image_id = _query(["docker", "inspect", "--format", "{{.Image}}", container])
    digests = None
    if image_id and not image_id.startswith("erro:"):
        digests = _query(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id]
        )
    return image, image_id, digests


def _sandbox_provenance(executor: FlociExecutor) -> dict[str, Any]:
    """Emulador usado no run de modalidades: tipo (`[sandbox] kind`), imagem e ID.

    Na AWS real: conta, ARN e região (a guarda de identidade roda aqui, antes de tudo).
    """
    if isinstance(executor, RealAwsExecutor):
        identity = executor.identity()
        return {
            "sandbox_kind": executor.KIND,
            "aws_account": identity["account"],
            "aws_arn": identity["arn"],
            "aws_region": executor.region,
        }
    image, image_id, digests = _emulator_image(executor.container)
    return {
        "sandbox_kind": executor.KIND,
        "sandbox_image": image,
        "sandbox_image_id": image_id,
        "sandbox_repo_digests": digests,
    }


# ---------- modality-prevalidate ----------


def _modality_table(rows: list[dict[str, Any]], modalities: list[str]) -> str:
    """Tabela tarefa × modalidade: baseline_pass/ref_exec_ok/ref_check_pass (1/0/-)."""

    def flag(value: Any) -> str:
        return "-" if value is None else str(int(bool(value)))

    cells = {
        (r["task_id"], r["modality"]): (
            "sem ref" if r.get("ref_missing")
            else flag(r["baseline_pass"]) + flag(r["ref_exec_ok"]) + flag(r["ref_check_pass"])
            + ("" if r["valid"] else " !")
        )
        for r in rows
    }  # fmt: skip
    tasks = list(dict.fromkeys(r["task_id"] for r in rows))
    width = max([len(t) for t in tasks] + [4])
    lines = [
        "células: baseline_pass ref_exec_ok ref_check_pass (esperado 011, ou 110 nas tarefas"
        " expect=ask; '!' = inválida; 'sem ref' = modalidade sem referência)",
        f"{'task':<{width}}  " + "  ".join(f"{m:<10}" for m in modalities),
    ]
    for t in tasks:
        lines.append(
            f"{t:<{width}}  " + "  ".join(f"{cells.get((t, m), ''):<10}" for m in modalities)
        )
    return "\n".join(lines)


def _prompt_field(cfg: dict[str, Any]) -> str:
    """Campo do enunciado do Ambig-IaC (`[modality] prompt_field`; teto = `prompt_original`)."""
    return cfg.get("modality", {}).get("prompt_field", "prompt")


def _load_cfg_tasks(cfg: dict[str, Any]) -> list[Any]:
    """Tarefas do catálogo, de `[modality] tasks_dir` (ex.: tarefas de usuário simulado) ou de
    `[modality] dataset` (E16: `"ambig-iac"`, linhas do JSONL em data/)."""
    dataset = cfg.get("modality", {}).get("dataset")
    if dataset is not None:
        if dataset != ambig_iac.DATASET:
            raise SystemExit(f"erro: dataset desconhecido {dataset!r} (use {ambig_iac.DATASET!r})")
        tasks = ambig_iac.load_tasks(DATA_DIR, _prompt_field(cfg))
        rules_file = cfg["modality"].get("rules_file")
        if rules_file is None:
            return tasks
        rules = json.loads(Path(rules_file).read_text())["amendment"]["discriminating_rules"]
        return [
            replace(t, rego_rules=tuple(rules[t.task_id])) if t.task_id in rules else t
            for t in tasks
        ]
    tasks_dir = cfg.get("modality", {}).get("tasks_dir")
    return load_tasks(Path(tasks_dir)) if tasks_dir else load_tasks()


def _select_modality_run(
    cfg: dict[str, Any], tasks_arg: list[str] | None, modalities_arg: list[str] | None = None
) -> tuple[list[Any], list[str]]:
    """Tarefas e modalidades: argumento da CLI > seção [modality] > todas.

    `free` (E4) é a forma livre: roda sozinha e sem `allow_question` (uma variável por vez).
    """
    mod_cfg = cfg.get("modality", {})
    tasks = _load_cfg_tasks(cfg)
    wanted = tasks_arg or mod_cfg.get("tasks")
    if wanted:
        missing = set(wanted) - {t.task_id for t in tasks}
        if missing:
            raise SystemExit(f"tarefas não encontradas: {sorted(missing)}")
        tasks = [t for t in tasks if t.task_id in set(wanted)]
    modalities = modalities_arg or mod_cfg.get("modalities", list(MODALITIES))
    unknown = set(modalities) - set(MODALITIES) - {FREE.name}
    if unknown:
        raise SystemExit(f"modalidades desconhecidas: {sorted(unknown)}")
    if FREE.name in modalities:
        if len(modalities) > 1:
            raise SystemExit("erro: a modalidade free roda sozinha (sem outras no mesmo run)")
        if _allow_question(cfg):
            raise SystemExit("erro: free não combina com allow_question (uma variável por vez)")
    return tasks, modalities


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
                "dataset": ambig_iac.provenance(_prompt_field(cfg)), **_git_provenance(),
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


def _replay_table(metrics: dict[str, Any]) -> str:
    """collateral_ok e concordância do check por categoria × modalidade."""
    header = (
        f"{'categoria':<9} {'modalidade':<10} {'n':>3} {'colat ok':>8} {'colat X':>7} "
        f"{'colat ?':>7} {'check=orig':>10}"
    )
    lines = [header, "-" * len(header)]
    rows = [
        (cat, mod, m) for cat, mods in metrics["by_category"].items() for mod, m in mods.items()
    ]
    rows += [("total", mod, m) for mod, m in metrics["by_modality"].items()]
    for cat, mod, m in rows:
        c = m["collateral_ok"]
        agree = f"{m['check_agreement']['k']}/{m['check_agreement']['n']}"
        lines.append(
            f"{cat:<9} {mod:<10} {m['n']:>3} {c['true']:>8} {c['false']:>7} {c['none']:>7} "
            f"{agree:>10}"
        )
    lines.append(f"pulados: {metrics['n_skipped']} {metrics['skipped']}")
    for item in metrics["collateral_false"]:
        lines.append(f"colateral: {item['task_id']} × {item['modality']} rep {item['rep']}: "
                     f"{', '.join(item['changed'])}")  # fmt: skip
    return "\n".join(lines)


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


def _allow_question(cfg: dict[str, Any]) -> bool:
    """`[modality] allow_question` (E2): o agente pode perguntar em vez de agir."""
    return bool(cfg.get("modality", {}).get("allow_question", False))


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


def _short_rate(r: dict[str, Any]) -> str:
    """k/n [IC de Wilson curto, em %]."""
    lo, hi = r["wilson"]
    return f"{r['k']}/{r['n']} [{lo * 100:.0f}-{hi * 100:.0f}%]"


def _modality_summary(events: list[dict[str, Any]]) -> str:
    metrics = compute_modality_metrics(events)
    lines = [f"{metrics['n_trajectories']} trajetórias"]
    for modality, blocks in metrics["modalities"].items():
        for category, m in blocks["by_category"].items():
            first = f"{m['first_attempt_success']['k']}/{m['first_attempt_success']['n']}"
            state = f"{m['state_pass']['k']}/{m['state_pass']['n']}"
            lines.append(
                f"{modality:<10} {category:<7} sucesso {_short_rate(m['success']):<17} "
                f"1ª tent. {first:<6} estado {state:<6} "
                f"tentativas {m['mean_attempts']:.2f}  tokens {m['tokens']['mean_total']:.0f}  "
                f"{m['termination']}"
            )
    return "\n".join(lines)


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


def _load_run_config(run_dir: Path) -> tuple[Path, dict[str, Any]]:
    """(caminho, conteúdo) do run_config.json de um run a retomar."""
    config_path = run_dir / "run_config.json"
    if not config_path.is_file():
        raise SystemExit(f"erro: {config_path} não encontrado")
    return config_path, json.loads(config_path.read_text(encoding="utf-8"))


def _record_resume(
    config_path: Path, saved: dict[str, Any], run_id: str, skipped: int, discarded: int,
    pending: int, unit: str,
) -> None:  # fmt: skip
    """Anexa a entrada `resumes` (skipped_<unit>, discarded_<unit>) e avisa no stderr."""
    saved.setdefault("resumes", []).append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            **_git_provenance(),
            f"skipped_{unit}": skipped,
            f"discarded_{unit}": discarded,
        }
    )
    config_path.write_text(json.dumps(saved, indent=2, ensure_ascii=False))
    print(
        f"resume {run_id}: {skipped} completos pulados, "
        f"{discarded} incompletos descartados, {pending} a rodar",
        file=sys.stderr,
    )


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


def _modality_metrics_table(metrics: dict[str, Any]) -> str:
    """Tabela legível modalidade × categoria (e total por modalidade)."""
    header = (
        f"{'modalidade':<10} {'categoria':<9} {'sucesso [IC 95%]':<20} {'1ª tentativa':<13} "
        f"{'tentativas':>10} {'tokens':>8} {'custo US$':>10}"
    )
    lines = [header, "-" * len(header)]
    for modality, blocks in metrics["modalities"].items():
        for category, m in [*blocks["by_category"].items(), ("total", blocks["overall"])]:
            first = f"{m['first_attempt_success']['k']}/{m['first_attempt_success']['n']}"
            lines.append(
                f"{modality:<10} {category:<9} {_short_rate(m['success']):<20} "
                f"{first:<13} {m['mean_attempts']:>10.2f} "
                f"{m['tokens']['mean_total']:>8.0f} {m['cost_usd']:>10.4f}"
            )
    return "\n".join(lines)


def _free_table(metrics: dict[str, Any]) -> str:
    """Forma livre (E4): escolhas por categoria e, com --baseline, a comparação de tokens."""
    lines = ["", "forma livre: 1ª escolha / última escolha / troca / acordo R11"]
    free = metrics["free"]
    for category, b in [*free["by_category"].items(), ("total", free["overall"])]:
        agree = b["r11_agreement"]
        lines.append(
            f"{category:<9} {b['chosen_first']}  {b['chosen']}  "
            f"troca {b['switched']['k']}/{b['switched']['n']}  "
            f"R11 {agree['k']}/{agree['n']}"
        )
    base = metrics.get("baseline")
    if base:
        lines += [
            "",
            f"livre × baseline {base['run_dir']} ({base['n_tasks']} tarefas; "
            "razão = livre / forma fixa, média geométrica dos tokens médios por tarefa)",
        ]

        def fmt(r: dict[str, Any]) -> str:
            if r["ratio_geomean"] is None:
                return "n/d"
            lo, hi = r["ratio_ci95"]
            return f"{r['ratio_geomean']:.2f} [{lo:.2f}, {hi:.2f}]"

        for category, b in [*base["by_category"].items(), ("total", base["overall"])]:
            for k in ("total", "output"):
                vs_cheapest = b["ratio_vs_cheapest"][k]
                vs_priciest = b["ratio_vs_most_expensive"][k]
                chose = b["chose_cheapest"][k]
                lines.append(
                    f"{category:<9} {k:<6} escolheu a mais barata {chose['k']}/{chose['n']}  "
                    f"vs mais barata {fmt(vs_cheapest)}  vs mais cara {fmt(vs_priciest)}"
                )
            lines.append(f"{category:<9} sucesso livre × melhor fixa {b['success_vs_best_fixed']}")
        if base["overall"]["undecided"]:
            lines.append(f"sem escolha majoritária: {', '.join(base['overall']['undecided'])}")
    return "\n".join(lines)


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


# ---------- agent-* (E3: agente real em container) ----------


def _agentic_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    ag = cfg.get("agentic")
    if not ag:
        raise SystemExit("erro: config sem a seção [agentic]")
    unknown = set(ag.get("conditions", [])) - set(AGENT_CONDITIONS)
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
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain, allow_real_aws=True)
    if isinstance(executor, RealAwsExecutor):
        return AwsTarget(toolchain, executor)
    return FlociTarget(toolchain, executor)


def _build_targets(cfg: dict[str, Any]) -> list[Target]:
    """Um alvo por worker, montados uma vez por comando."""
    return [_build_target(_worker_cfg(cfg, i)) for i in range(_agent_workers(cfg))]


def _build_agent(cfg: dict[str, Any]) -> ClaudeCodeAgent:
    ag = cfg["agentic"]
    if ag.get("agent", ClaudeCodeAgent.name) != ClaudeCodeAgent.name:
        raise SystemExit(
            f"erro: agente desconhecido {ag['agent']!r} (use {ClaudeCodeAgent.name!r})"
        )
    return ClaudeCodeAgent(
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
    """Juiz da seção `[judge]` (`provider`, `model`); sem `provider` é o Anthropic de sempre.

    `provider` sobrescreve o da config; se difere dele, o `model` da config não é herdado
    (vale o padrão do provedor, a menos que `model` seja dado).
    """
    section = _judge_section(section, model, provider, reasoning_effort)
    return LlmJudge(llm=build_text_model(section, "anthropic"))


def _judge_section(
    section: dict[str, Any],
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Seção `[judge]` com as opções da CLI por cima (regras em `_build_judge`)."""
    if provider and provider != section.get("provider", "anthropic"):
        section = {k: v for k, v in section.items() if k != "model"}
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
    llm = build_text_model(section, "codex")
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
    cfg: dict[str, Any], plan: list[tuple], runs_dir: Path, run_id: str, agent: ClaudeCodeAgent,
    targets: list[Target], user_sim: UserSimSetup | None = None,
) -> None:  # fmt: skip
    """Distribui as unidades entre os workers (um alvo cada); o recorder é compartilhado."""
    ag = cfg["agentic"]
    strata = load_strata(Path(ag.get("strata_file", DEFAULT_STRATA_FILE)))
    for cond in ag["conditions"]:  # falha cedo: plugin ausente
        resolve_condition(cond, AGENT_PLUGIN_ROOT)
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


def _agent_summary(events: list[dict[str, Any]]) -> str:
    finals = [e for e in events if e.get("event") == "final"]
    lines = [f"{len(finals)} trajetórias"]
    for key, b in by_condition_model(finals).items():
        lines.append(
            f"{key:<28} estado {b['state_pass']['k']}/{b['state_pass']['n']}  "
            f"tokens {b['tokens']['mean_total'] or 0:.0f}  US$ {b['cost_usd']:.4f}  "
            f"{b['termination']}"
        )
    return "\n".join(lines)


def _agent_run_config(
    cfg: dict[str, Any], tasks: list[Any], reps: int, run_dir: Path, agent: ClaudeCodeAgent,
    targets: list[Target], user_sim: UserSimSetup | None = None,
) -> dict[str, Any]:  # fmt: skip
    toolchain, executor = targets[0].toolchain, targets[0].executor
    sim_provenance = (
        {"user_sim": user_sim.simulator.params, "user_sim_judge": user_sim.judge.params}
        if user_sim
        else {}
    )
    dataset = (
        {"dataset": ambig_iac.provenance(_prompt_field(cfg))}
        if cfg.get("modality", {}).get("dataset")
        else {}
    )
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
    """E15: nota a qualidade de cada pergunta (trajetória com `asked = true` no judge.jsonl).

    Retomável: pula as trajetórias que já têm linha no arquivo de saída.
    """
    run_dir = Path(args.run_dir)
    cfg = json.loads((run_dir / "run_config.json").read_text())["config"]
    section = _judge_section(cfg.get("judge", {}), args.model, args.provider)
    judge = QuestionJudge(build_text_model({**section, "max_tokens": QUESTION_MAX_TOKENS},
                                           "anthropic"))  # fmt: skip
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
                # tarefas de dataset (E16) não têm meta.toml
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
    """E15: relatório das notas das perguntas (Markdown no stdout), sem LLM.

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


def _agent_metrics_table(metrics: dict[str, Any]) -> str:
    header = (
        f"{'condição|modelo':<28} {'sucesso [IC 95%]':<20} {'estado':<9} {'perguntou':<10} "
        f"{'colateral':<10} {'releu':<8} {'tokens':>9} {'custo US$':>10}"
    )
    lines = [
        f"{metrics['n_trajectories']} trajetórias, {metrics['n_unjudged']} sem juiz, "
        f"falhas do harness {metrics['harness_failures']}",
        header,
        "-" * len(header),
    ]

    def frac(r: dict[str, Any]) -> str:
        return f"{r['k']}/{r['n']}"

    for key, b in metrics["by_condition_model"].items():
        lines.append(
            f"{key:<28} {_short_rate(b['success']):<20} {frac(b['state_pass']):<9} "
            f"{frac(b['asked']):<10} {frac(b['collateral_ok']):<10} "
            f"{frac(b['verified_state']):<8} {b['tokens']['mean_total'] or 0:>9.0f} "
            f"{b['cost_usd']:>10.4f}"
        )
        if "user_sim" in b:
            u = b["user_sim"]
            lines.append(
                f"  usuário simulado: respondeu {frac(u['replied'])}, "
                f"estado final {frac(u['state_pass_final'])}, "
                f"sucesso final {frac(u['success_final'])}, erros {u['errors']}"
            )
    for model, cmps in metrics["vs_none"].items():
        lines += ["", f"{model}: condição × none (sucesso; McNemar pela maioria das reps)"]
        for c in cmps["success"]:
            lo, hi = c["diff_ci95"]
            lines.append(
                f"  {c['pair']:<22} n={c['n_pairs']:<3} só none {c['only_first']:<3} "
                f"só cond {c['only_second']:<3} p={c['mcnemar_p']:.3f} holm={c['holm_p']:.3f} "
                f"dif {c['diff_mean']:+.2f} [{lo:+.2f}, {hi:+.2f}]"
            )
        for cond, harm in metrics["easy_harm"][model].items():
            lines.append(
                f"  piora nas fáceis {cond:<12} {frac(harm['harmed'])} "
                f"{', '.join(harm['harmed_tasks'])}"
            )
        for cond, rows in metrics["per_task"][model].items():
            lines.append(f"  por tarefa: none × {cond} (maioria das reps; razão de tokens)")
            for r in rows:
                ratio = "n/d" if r["tokens_ratio"] is None else f"{r['tokens_ratio']:.2f}"
                lines.append(
                    f"    {r['task_id']:<28} none {_mark(r['none_success'])} "
                    f"cond {_mark(r['cond_success'])} tokens ×{ratio}"
                )
    for model, scores in metrics.get("partial_scores", {}).items():
        lines += ["", f"{model}: notas parciais do oráculo (média por tarefa)"]
        lines += [
            f"  {cond:<14} rego_frac {_fmt_score(s['rego_frac'])} "
            f"type_f1 {_fmt_score(s['type_f1'])}"
            for cond, s in scores["mean"].items()
        ]
        lines += [
            f"  {c['metric']:<10} {c['pair']:<32} n={c['n_pairs']:<3} "
            f"dif {c['diff_mean']:+.3f} [{c['diff_ci95'][0]:+.3f}, {c['diff_ci95'][1]:+.3f}]"
            for c in scores["paired"]
        ]
    cmp = metrics.get("compare")
    if cmp:
        lines += [
            "",
            f"concordância × {cmp['run_dir']}: {frac(cmp['agreement'])} "
            f"(tarefa, condição, modelo); só neste {cmp['only_first']}, "
            f"só no outro {cmp['only_second']}",
        ]
        lines += [
            f"  {d['task_id']} {d['condition']} {d['model']}: "
            f"{_mark(d['first'])} × {_mark(d['second'])}"
            for d in cmp["discordant"]
        ]
    return "\n".join(lines)


def _fmt_score(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.3f}"


def _mark(ok: bool) -> str:
    return "ok" if ok else "--"


def cmd_demo_timeline(args: argparse.Namespace) -> int:
    try:
        timeline = build_timeline(args.unit_dir, args.scene)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"erro: {exc}") from exc
    text = dump_timeline(timeline) + "\n"
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


# ---------- backend do agente (modality-run) ----------


def _build_backend(cfg: dict[str, Any], **prompt_kwargs: Any) -> Any:
    """Backend do agente; montado só depois de validadas tarefas e condições.

    `prompt_kwargs` (system_prompt, repair_template, ...) só no teste de modalidades.
    """
    agent_cfg = cfg["agent"]
    return BACKENDS[agent_cfg["provider"]](
        model=agent_cfg["model"],
        max_tokens=agent_cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        effort=agent_cfg.get("effort"),
        **prompt_kwargs,
    )


# ---------- resume (modality-run --resume e agent-resume) ----------


def pair_completion(
    events: list[dict[str, Any]],
    conditions: list[str] | None,
    fields: tuple[str, ...] = ("task_id", "rep"),
) -> tuple[set[tuple], set[tuple]]:
    """(completos, incompletos) por unidade `fields`.

    Completo = tem um evento `final` para cada condição; com `conditions=None` (modalidades,
    sem condição, ou agente, cuja condição já está em `fields`), basta um evento `final`.
    """
    finals: dict[tuple, set[str | None]] = {}
    for e in events:
        done = finals.setdefault(tuple(e[f] for f in fields), set())
        if e["event"] == "final":
            done.add(e.get("condition"))
    if conditions is None:
        complete = {k for k, done in finals.items() if done}
    else:
        complete = {k for k, done in finals.items() if set(conditions) <= done}
    return complete, set(finals) - complete


def discard_incomplete(
    runs_dir: Path,
    run_id: str,
    conditions: list[str] | None,
    fields: tuple[str, ...] = ("task_id", "rep"),
    unit_dir: Callable[[tuple], Path] | None = None,
) -> tuple[set[tuple], set[tuple]]:
    """Move os eventos dos pares incompletos para events.discarded.jsonl e apaga seus artefatos.

    `fields` e `unit_dir` definem a unidade (padrão: (task, rep) em pair_dir); ver
    `pair_completion` para `conditions`.

    events.jsonl é reescrito só com os pares completos (linhas originais preservadas). Uma
    última linha truncada (queda no meio da gravação) também vai para o discarded; linha
    inválida em outro ponto indica corrupção e aborta. Devolve (completos, descartados).
    """
    run_dir = runs_dir / run_id
    events_path = run_dir / "events.jsonl"
    text = events_path.read_text(encoding="utf-8") if events_path.exists() else ""
    lines = [line for line in text.splitlines() if line.strip()]
    events: list[dict[str, Any]] = []
    truncated: list[str] = []
    for i, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            if i < len(lines) - 1:
                raise SystemExit(
                    f"erro: {events_path} corrompido (linha {i + 1} não é JSON válido: {exc})"
                ) from None
            truncated = [line]
            print(f"aviso: última linha de {events_path} truncada; movida para o discarded",
                  file=sys.stderr)  # fmt: skip
    lines = lines[: len(events)]
    complete, incomplete = pair_completion(events, conditions, fields)
    keyed = [(tuple(e[f] for f in fields), ln) for ln, e in zip(lines, events, strict=True)]
    keep = [ln for key, ln in keyed if key in complete]
    drop = [ln for key, ln in keyed if key in incomplete] + truncated
    if drop:
        # Discarded antes do replace, deduplicando por linha: se cair entre os dois passos, o
        # próximo resume reencontra as mesmas linhas em events.jsonl e não as duplica. A ordem
        # inversa (replace primeiro) perderia esses eventos de vez numa queda no meio.
        discarded_path = run_dir / "events.discarded.jsonl"
        seen: set[str] = set()
        if discarded_path.exists():
            seen = set(discarded_path.read_text(encoding="utf-8").splitlines())
        with discarded_path.open("a", encoding="utf-8") as fh:
            fh.writelines(ln + "\n" for ln in drop if ln not in seen)
    new_text = "".join(ln + "\n" for ln in keep)
    if new_text != text:  # também corrige um final sem "\n", que o próximo append colaria
        tmp = events_path.with_suffix(".jsonl.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(events_path)
    for key in incomplete:
        path = unit_dir(key) if unit_dir else pair_dir(runs_dir, run_id, *key)
        shutil.rmtree(path, ignore_errors=True)
    return complete, incomplete


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iac-harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mpre = sub.add_parser(
        "modality-prevalidate",
        help="checa referência e estado inicial de cada tarefa × modalidade (Terraform/CLI/boto3)",
    )
    p_mpre.add_argument("--config", type=Path, required=True)
    p_mpre.add_argument("--tasks", nargs="+")
    p_mpre.set_defaults(func=cmd_modality_prevalidate)

    p_apre = sub.add_parser(
        "ambig-prevalidate",
        help="E16: gate do Ambig-IaC (referência passa no oráculo, main.tf vazio reprova)",
    )
    p_apre.add_argument("--config", type=Path, required=True)
    p_apre.add_argument("--order", type=Path, help="selection.json com a ordem do sorteio")
    p_apre.add_argument("--tasks", nargs="+", help="tarefas avulsas (no lugar de --order)")
    p_apre.add_argument("--target-pass", type=int, help="para ao juntar N aprovadas")
    p_apre.set_defaults(func=cmd_ambig_prevalidate)

    p_mrun = sub.add_parser(
        "modality-run", help="roda o agente nas modalidades (Terraform/CLI/boto3)"
    )
    p_mrun.add_argument("--config", type=Path)
    p_mrun.add_argument("--tasks", nargs="+")
    p_mrun.add_argument("--modalities", nargs="+", choices=list(MODALITIES))
    p_mrun.add_argument("--reps", type=int)
    p_mrun.add_argument(
        "--resume",
        type=Path,
        metavar="RUN_DIR",
        help="retoma um run interrompido: pula (tarefa, modalidade, rep) com evento final",
    )
    p_mrun.set_defaults(func=cmd_modality_run)

    p_mrep = sub.add_parser(
        "modality-replay",
        help="reexecuta sem LLM o artefato final de cada trajetória e mede o efeito colateral",
    )
    p_mrep.add_argument("src_run_dir", type=Path, help="run dir de um modality-run")
    p_mrep.add_argument("--tasks", nargs="+")
    p_mrep.add_argument(
        "--categories",
        type=lambda v: tuple(v.split(",")),
        default=REPLAY_CATEGORIES,
        help="categorias separadas por vírgula (padrão: modify,query)",
    )
    p_mrep.set_defaults(func=cmd_modality_replay)

    p_mmet = sub.add_parser(
        "modality-metrics", help="métricas de um modality-run (ou resumo de um modality-replay)"
    )
    p_mmet.add_argument("run_dir", type=Path)
    p_mmet.add_argument("--format", choices=("json", "table"), default="json")
    p_mmet.add_argument(
        "--exclude-tasks",
        nargs="+",
        metavar="TASK",
        help="remove essas tarefas (task_id) antes de calcular as métricas",
    )
    p_mmet.add_argument(
        "--baseline",
        type=Path,
        metavar="RUN_DIR",
        help="run de forma fixa para comparar com um run da forma livre (E4)",
    )
    p_mmet.set_defaults(func=cmd_modality_metrics)

    p_arun = sub.add_parser(
        "agent-run", help="E3: roda o Claude Code de verdade (headless, em container) nas tarefas"
    )
    p_arun.add_argument("--config", type=Path, required=True)
    p_arun.add_argument("--tasks", nargs="+")
    p_arun.add_argument("--reps", type=int)
    p_arun.set_defaults(func=cmd_agent_run)

    p_ares = sub.add_parser(
        "agent-resume",
        help="retoma um agent-run: pula (tarefa, condição, modelo, rep) com evento final",
    )
    p_ares.add_argument("run_dir", type=Path)
    p_ares.set_defaults(func=cmd_agent_resume)

    p_ajud = sub.add_parser(
        "agent-judge", help="juiz LLM: a mensagem final do agente pergunta algo? (judge.jsonl)"
    )
    p_ajud.add_argument("run_dir", type=Path, nargs="?")
    p_ajud.add_argument("--model", help="modelo do juiz (padrão: [judge] model da config)")
    p_ajud.add_argument("--redo", action="store_true", help="reclassifica todas as trajetórias")
    p_ajud.add_argument(
        "--sample", type=int, help="grava judge_sample.csv com N trajetórias para leitura à mão"
    )
    p_ajud.add_argument("--seed", type=int, default=0, help="semente da amostra (--sample)")
    p_ajud.add_argument(
        "--agreement", type=Path, help="judge_sample.csv preenchido: concordância juiz × humano"
    )
    p_ajud.add_argument(
        "--provider", choices=("anthropic", "codex"), help="provedor do juiz (padrão: [judge])"
    )
    p_ajud.add_argument("--reasoning-effort", help="esforço de raciocínio do juiz (codex)")
    p_ajud.add_argument(
        "--out",
        default=JUDGE_FILENAME,
        help="arquivo de vereditos no run_dir (padrão: judge.jsonl)",
    )
    p_ajud.add_argument(
        "--compare-with",
        metavar="ARQUIVO",
        help="outro arquivo de vereditos no run_dir: concordância com judge.jsonl (sem LLM)",
    )
    p_ajud.set_defaults(func=cmd_agent_judge)

    p_ajq = sub.add_parser(
        "agent-judge-questions",
        help="E15: juiz LLM da qualidade das perguntas (asked=true) -> judge-questions.jsonl",
    )
    p_ajq.add_argument("run_dir", type=Path)
    p_ajq.add_argument(
        "--provider", choices=("anthropic", "codex"), help="provedor do juiz (padrão: [judge])"
    )
    p_ajq.add_argument("--model", help="modelo do juiz (padrão: o do provedor / [judge])")
    p_ajq.add_argument(
        "--out",
        default=QUESTIONS_FILENAME,
        help=f"arquivo no run_dir (padrão: {QUESTIONS_FILENAME})",
    )
    p_ajq.add_argument("--limit", type=int, help="nota no máximo N trajetórias (smoke)")
    p_ajq.set_defaults(func=cmd_agent_judge_questions)

    p_aqr = sub.add_parser(
        "agent-questions-report",
        help="E15: notas das perguntas por condição, toolkit × none, kappa entre juízes",
    )
    p_aqr.add_argument("run_dirs", type=Path, nargs="+")
    p_aqr.add_argument(
        "--judge-file", default=QUESTIONS_FILENAME, help=f"notas (padrão: {QUESTIONS_FILENAME})"
    )
    p_aqr.add_argument(
        "--compare", nargs=2, metavar=("A", "B"),
        help="dois arquivos de notas no run_dir: kappa ponderado e desacordos de 2 pontos",
    )  # fmt: skip
    p_aqr.set_defaults(func=cmd_agent_questions_report)

    p_amet = sub.add_parser(
        "agent-metrics", help="métricas de um agent-run (condição × modelo, estratos, alvo)"
    )
    p_amet.add_argument("run_dir", type=Path)
    p_amet.add_argument("--format", choices=("json", "table"), default="json")
    p_amet.add_argument(
        "--judge-file", default=JUDGE_FILENAME, help="arquivo de vereditos (padrão: judge.jsonl)"
    )
    p_amet.add_argument(
        "--compare", type=Path, help="outro agent-run (ex.: AWS real): concordância do sucesso"
    )
    p_amet.set_defaults(func=cmd_agent_metrics)

    p_demo = sub.add_parser(
        "demo-timeline", help="trajetória gravada -> timeline.json para o vídeo da demo"
    )
    p_demo.add_argument("unit_dir", type=Path, help="runs/<run>/<task>/<rep>/<cond>/<modelo>")
    p_demo.add_argument("-o", "--output", type=Path, help="arquivo de saída (padrão: stdout)")
    p_demo.add_argument("--scene", help="nome da cena (padrão: task_id)")
    p_demo.set_defaults(func=cmd_demo_timeline)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
