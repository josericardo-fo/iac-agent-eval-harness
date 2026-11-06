"""CLI: prevalidate | run | metrics | modality-* | agent-*. Mapeamentos simples, sem registry."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import tomllib
from collections.abc import Callable, Container
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iac_harness.agentic.agents import DEFAULT_AGENT_IMAGE, ClaudeCodeAgent
from iac_harness.agentic.conditions import REGISTRY as AGENT_CONDITIONS
from iac_harness.agentic.conditions import resolve_condition
from iac_harness.agentic.judge import DEFAULT_JUDGE_MODEL, JUDGE_FILENAME, LlmJudge
from iac_harness.agentic.metrics import (
    JUDGE_SAMPLE_FIELDS,
    by_condition_model,
    compare_runs,
    compute_agent_metrics,
    judge_agreement,
    judge_sample,
    merge_judgments,
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
from iac_harness.agents.anthropic_backend import DEFAULT_MAX_TOKENS, AnthropicBackend, parse_files
from iac_harness.agents.prompts import PROMPT_VERSION
from iac_harness.conditions import A, B, C, Condition
from iac_harness.datasets.iac_eval_v2 import IacEvalV2Adapter
from iac_harness.datasets.multi_iac_eval import MultiIacEvalAdapter
from iac_harness.demo.timeline import build_timeline, dump_timeline
from iac_harness.executors import final_cleanup
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import UNSUPPORTED_PATTERNS_VERSION, FlociExecutor
from iac_harness.executors.localstack import LocalStackExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.metrics import compute_metrics, load_events
from iac_harness.modalities import prompts as mod_prompts
from iac_harness.modalities.execute import (
    apply_initial,
    prevalidate_pair,
    replay_pair,
    run_check,
    truncate,
)
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.metrics import (
    compare_free_to_baseline,
    compute_modality_metrics,
    compute_replay_metrics,
)
from iac_harness.modalities.runner import (
    DEFAULT_MAX_ATTEMPTS,
    ModRunContext,
    ModTermination,
    parse_question,
    run_pair,
    uses_local_exec,
)
from iac_harness.modalities.spec import FREE, MODALITIES
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.runner import COMMON_CHAIN, RunContext, SandboxGate, run_gates, run_task
from iac_harness.toolchain import DEFAULT_ENDPOINT, DEFAULT_REGION, DEFAULT_TIMEOUT_S, Toolchain
from iac_harness.types import Artifact, CheckStatus, Stage, Task, TaskOracle
from iac_harness.verifiers.opa import OpaVerifier
from iac_harness.verifiers.terraform import PLAN_FILE, PlanVerifier, ValidateVerifier, error_blocks
from iac_harness.workspace import OVERRIDE_FILENAME, RESPONSE_FILENAME, create_workspace, pair_dir

CONDITIONS: dict[str, Condition] = {"A": A, "B": B, "C": C}
BACKENDS = {"anthropic": AnthropicBackend}
EXECUTORS: dict[str, type[FlociExecutor]] = {
    cls.KIND: cls for cls in (FlociExecutor, LocalStackExecutor, RealAwsExecutor)
}
DATA_DIR = Path("data")
MIRROR_DIR = "/opt/terraform-providers"  # filesystem mirror dentro da imagem da toolchain
_RESOURCE = re.compile(r"\b(aws(?:cc)?_\w+)\b")
# E3: raiz dos plugins das condições (toolkit/, third_party/).
AGENT_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_WITH_RESOURCE = re.compile(r"\bwith (aws(?:cc)?_\w+)\.")  # linha \"with aws_x.nome,\" do erro


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


def build_gates(cfg: dict[str, Any], toolchain: Toolchain, executor: FlociExecutor) -> dict:
    visibility = cfg.get("feedback", {}).get("policy_visibility", "rule_names")
    return {
        Stage.VALIDATE: ValidateVerifier(toolchain),
        Stage.PLAN: PlanVerifier(toolchain),
        Stage.OPA: OpaVerifier(toolchain, visibility),
        Stage.SANDBOX: SandboxGate(executor),
    }


def select_tasks(
    pairs: list[tuple[Task, TaskOracle]], tasks: list[str] | None, limit: int | None
) -> list[tuple[Task, TaskOracle]]:
    if tasks:
        wanted = set(tasks)
        pairs = [p for p in pairs if p[0].task_id in wanted]
        missing = wanted - {p[0].task_id for p in pairs}
        if missing:
            raise SystemExit(f"tarefas não encontradas: {sorted(missing)}")
    return pairs[:limit] if limit else pairs


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


def _mirror_providers(toolchain: Toolchain, workdir: Path) -> list[str] | str:
    """Nomes dos .zip de providers do mirror da toolchain."""
    try:
        proc = toolchain.run(["find", MIRROR_DIR, "-name", "*.zip"], workdir, timeout_s=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"erro: {exc}"
    if proc.returncode != 0:
        return f"erro: {proc.stderr.strip()[:200]}"
    return sorted(line.rsplit("/", 1)[-1] for line in proc.stdout.split() if line)


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


def collect_provenance(
    toolchain: Toolchain,
    executor: FlociExecutor,
    adapter: IacEvalV2Adapter,
    run_dir: Path,
) -> dict[str, Any]:
    """Bloco de procedência do `run_config.json`/`prevalidation_config.json`.

    Cada consulta externa é tolerante a falha: grava `None` ou "erro: ..." sem quebrar o comando.
    """
    toolchain_id = None
    if toolchain.mode == "docker":
        toolchain_id = _query(
            ["docker", "image", "inspect", "--format", "{{.Id}}", toolchain.image]
        )
    # Chaves floci_* mantidas por compatibilidade com os runs A/B/C; `sandbox_kind` diz o emulador.
    floci_image, floci_image_id, floci_digests = _emulator_image(executor.container)
    return {
        **_git_provenance(),
        "versions": {
            **toolchain.versions(run_dir),
            "toolchain_image": toolchain.image,
            "toolchain_image_id": toolchain_id,
            "providers": _mirror_providers(toolchain, run_dir),
            "sandbox_kind": executor.KIND,
            "floci_image": floci_image,
            "floci_image_id": floci_image_id,
            "floci_repo_digests": floci_digests,
            "dataset_revision": adapter.version,
            "unsupported_patterns": UNSUPPORTED_PATTERNS_VERSION,
        },
        "prompt_version": PROMPT_VERSION,
    }


# ---------- prevalidate ----------


def _incompatibility(raw: str) -> str | None:
    """Recurso/serviço que causou a incompatibilidade (heurística sobre o erro bruto)."""
    m = _WITH_RESOURCE.search(raw) or _RESOURCE.search(raw)
    return m.group(1) if m else None


def prevalidate_task(
    task: Task,
    oracle: TaskOracle,
    gates: dict,
    executor: FlociExecutor,
    runs_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    artifact = Artifact(dict(oracle.reference_files))
    ws = create_workspace(
        runs_dir, run_id, task.task_id, 0, "reference", "final", artifact,
        executor.override_for(artifact.files),
    )  # fmt: skip
    statuses, failing, _ = run_gates(COMMON_CHAIN, gates, ws, oracle)
    pre_ok = all(statuses[s.value] == CheckStatus.PASS for s in COMMON_CHAIN[:3])
    return {
        "task_id": task.task_id,
        "difficulty": task.difficulty,
        "statuses": statuses,
        "harness_valid": pre_ok,
        "sandbox_supported": pre_ok and statuses[Stage.SANDBOX] == CheckStatus.PASS,
        "failed_stage": failing.stage.value if failing else None,
        "error": (failing.message[:1000] if failing else ""),
        "incompatibility": (
            _incompatibility(failing.raw) if failing and failing.stage is Stage.SANDBOX else None
        ),
        "sandbox_unsupported_pattern": bool(failing and failing.status is CheckStatus.UNSUPPORTED),
    }


def cmd_prevalidate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    gates = build_gates(cfg, toolchain, executor)
    adapter = IacEvalV2Adapter(DATA_DIR, cfg["dataset"]["revision"])
    pairs = select_tasks(adapter.load(), args.tasks, args.limit)
    runs_dir = Path(cfg["run"].get("runs_dir", "runs"))
    run_id = new_run_id("prevalidate")
    (runs_dir / run_id).mkdir(parents=True, exist_ok=False)
    (runs_dir / run_id / "prevalidation_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "tasks": [t.task_id for t, _ in pairs],
                **collect_provenance(toolchain, executor, adapter, runs_dir / run_id),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    recorder = JsonlRecorder(runs_dir / run_id / "prevalidation.jsonl")
    rows = []
    try:
        for task, oracle in pairs:
            executor.reset()
            row = prevalidate_task(task, oracle, gates, executor, runs_dir, run_id)
            recorder.record(row)
            rows.append(row)
            print(f"{task.task_id}: {row['statuses']}", file=sys.stderr)
    finally:
        recorder.close()
    n = len(rows)
    summary = {
        "run_id": run_id,
        "tasks": n,
        "harness_valid": sum(r["harness_valid"] for r in rows),
        "sandbox_supported": sum(r["sandbox_supported"] for r in rows),
        "floci_coverage": (sum(r["sandbox_supported"] for r in rows) / n) if n else None,
        "harness_invalid_tasks": [r["task_id"] for r in rows if not r["harness_valid"]],
    }
    (runs_dir / run_id / "prevalidation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


# ---------- prevalidate-miac ----------

# Multi-IaC-Eval não tem política OPA: a cadeia pula esse estágio.
MIAC_CHAIN = (Stage.VALIDATE, Stage.PLAN, Stage.SANDBOX)
_PLAN_SUMMARY = re.compile(r"Plan: (\d+) to add, (\d+) to change, (\d+) to destroy")
_UPDATE_RESOURCE = re.compile(r"UpdateResource|UnsupportedOperation")


def prevalidate_config(
    triple_id: str,
    variant: str,
    text: str,
    gates: dict,
    executor: FlociExecutor,
    runs_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    """Uma config (initial ou expected) pela cadeia validate → plan → apply no Floci."""
    executor.reset()  # o plan também consulta o Floci: começa de uma conta limpa
    artifact = Artifact({"main.tf": text})
    ws = create_workspace(
        runs_dir, run_id, triple_id, 0, variant, "final", artifact,
        executor.override_for(artifact.files),
    )  # fmt: skip
    statuses, failing, _ = run_gates(MIAC_CHAIN, gates, ws, TaskOracle({}, ""))
    pre_ok = all(statuses[s.value] == CheckStatus.PASS for s in MIAC_CHAIN[:2])
    return {
        "statuses": statuses,
        "harness_valid": pre_ok,
        "sandbox_supported": pre_ok and statuses[Stage.SANDBOX] == CheckStatus.PASS,
        "failed_stage": failing.stage.value if failing else None,
        "error": (failing.message[:1000] if failing else ""),
        "incompatibility": (
            _incompatibility(failing.raw) if failing and failing.stage is Stage.SANDBOX else None
        ),
        "sandbox_unsupported_pattern": bool(failing and failing.status is CheckStatus.UNSUPPORTED),
    }


def _plan_counts(stdout: str) -> dict[str, int] | None:
    """Contagens da linha "Plan: N to add, N to change, N to destroy" (None se não houver)."""
    m = _PLAN_SUMMARY.search(stdout)
    return dict(zip(("add", "change", "destroy"), map(int, m.groups()), strict=True)) if m else None


def sequential_check(
    triple: Any, toolchain: Toolchain, executor: FlociExecutor, runs_dir: Path, run_id: str
) -> dict[str, Any]:
    """initial e depois expected no MESMO estado (a pré-validação usa um Floci limpo para cada).

    Exercita o caminho de update (no awscc, UpdateResource do Cloud Control) e mede o drift:
    depois do apply da expected, um novo plan deveria sair sem mudanças.
    """
    executor.reset()
    artifact = Artifact({"main.tf": triple.initial})
    ws = create_workspace(
        runs_dir, run_id, triple.triple_id, 0, "sequential", "final", artifact,
        executor.override_for(artifact.files),
    )  # fmt: skip
    path = ws.path
    out: dict[str, Any] = {"ok": False, "step": None, "error": "", "update_resource_error": False}

    def tf(step: str, argv: list[str], ok: tuple[int, ...] = (0,)):
        """Roda `terraform argv`; em falha registra passo e erro em `out` e devolve None."""
        try:
            proc = toolchain.run(["terraform", *argv], path)
        except (subprocess.TimeoutExpired, OSError) as exc:
            out.update(step=step, error=f"falha ao executar terraform: {exc}")
            return None
        if proc.returncode in ok:
            return proc
        raw = proc.stdout + proc.stderr
        out.update(
            step=step,
            error=(error_blocks(proc.stderr) or proc.stdout.strip())[:1000],
            update_resource_error=bool(_UPDATE_RESOURCE.search(raw)),
        )
        return None

    flags = ["-input=false", "-no-color"]
    try:
        if not tf("init", ["init", "-backend=false", *flags]):
            return out
        if not tf("apply_initial", ["apply", "-auto-approve", *flags]):
            return out
        (path / "main.tf").write_text(triple.expected)
        (path / OVERRIDE_FILENAME).write_text(executor.override_for({"main.tf": triple.expected}))
        plan = tf("plan_expected", ["plan", f"-out={PLAN_FILE}", *flags])
        if not plan:
            return out
        out["plan"] = _plan_counts(plan.stdout)
        show = tf("show", ["show", "-json", PLAN_FILE])
        if not show:
            return out
        changes = json.loads(show.stdout).get("resource_changes", [])
        actions = [rc["change"]["actions"] for rc in changes]
        out["actions"] = {
            "create": sum(a == ["create"] for a in actions),
            "update": sum(a == ["update"] for a in actions),
            "replace": sum(set(a) == {"create", "delete"} for a in actions),
            "delete": sum(a == ["delete"] for a in actions),
        }
        if not tf("apply_expected", ["apply", "-auto-approve", *flags, PLAN_FILE]):
            return out
        out["ok"] = True
        # -detailed-exitcode: 0 = sem mudanças, 2 = há mudanças (drift).
        replan = tf("replan", ["plan", "-detailed-exitcode", *flags], ok=(0, 2))
        if replan:
            out["clean_after"] = replan.returncode == 0
            out["replan"] = _plan_counts(replan.stdout)
        return out
    finally:
        shutil.rmtree(path / ".terraform", ignore_errors=True)


def cmd_prevalidate_miac(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    gates = build_gates(cfg, toolchain, executor)
    adapter = MultiIacEvalAdapter(args.csv)
    triples = adapter.load()
    if args.tasks:
        triples = [t for t in triples if t.triple_id in set(args.tasks)]
    triples = triples[: args.limit] if args.limit else triples
    runs_dir = Path(cfg["run"].get("runs_dir", "runs"))
    run_id = new_run_id("miac-prevalidate")
    (runs_dir / run_id).mkdir(parents=True, exist_ok=False)
    (runs_dir / run_id / "prevalidation_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "dataset": "AmazonScience/Multi-IaC-Eval",
                "dataset_csv": str(args.csv),
                "dataset_sha256": adapter.sha256(),
                "tasks": [t.triple_id for t in triples],
                **collect_provenance(toolchain, executor, adapter, runs_dir / run_id),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    recorder = JsonlRecorder(runs_dir / run_id / "prevalidation.jsonl")
    rows = []
    try:
        for triple in triples:
            row: dict[str, Any] = {"task_id": triple.triple_id, "source_repo": triple.source_repo}
            for variant in ("initial", "expected"):
                row[variant] = prevalidate_config(
                    triple.triple_id, variant, getattr(triple, variant), gates, executor,
                    runs_dir, run_id,
                )  # fmt: skip
            row["usable"] = (
                row["initial"]["sandbox_supported"] and row["expected"]["sandbox_supported"]
            )
            if args.sequential and row["usable"]:
                row["sequential"] = sequential_check(triple, toolchain, executor, runs_dir, run_id)
            recorder.record(row)
            rows.append(row)
            print(
                f"{triple.triple_id}: initial={row['initial']['statuses']} "
                f"expected={row['expected']['statuses']}",
                file=sys.stderr,
            )
    finally:
        recorder.close()

    def count(variant: str, key: str) -> int:
        return sum(r[variant][key] for r in rows)

    summary = {
        "run_id": run_id,
        "tasks": len(rows),
        **{
            variant: {
                "harness_valid": count(variant, "harness_valid"),
                "sandbox_supported": count(variant, "sandbox_supported"),
            }
            for variant in ("initial", "expected")
        },
        "both_harness_valid": sum(
            r["initial"]["harness_valid"] and r["expected"]["harness_valid"] for r in rows
        ),
        "usable": sum(r["usable"] for r in rows),
        "usable_tasks": [r["task_id"] for r in rows if r["usable"]],
    }
    if args.sequential:
        seq = [r["sequential"] for r in rows if "sequential" in r]
        summary["sequential"] = {
            "checked": len(seq),
            "ok": sum(s["ok"] for s in seq),
            "update_resource_error": sum(s["update_resource_error"] for s in seq),
            "clean_after": sum(s.get("clean_after", False) for s in seq),
        }
    (runs_dir / run_id / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


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


def _select_modality_run(
    cfg: dict[str, Any], tasks_arg: list[str] | None, modalities_arg: list[str] | None = None
) -> tuple[list[Any], list[str]]:
    """Tarefas e modalidades: argumento da CLI > seção [modality] > todas.

    `free` (E4) é a forma livre: roda sozinha e sem `allow_question` (uma variável por vez).
    """
    mod_cfg = cfg.get("modality", {})
    tasks = load_tasks()
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


# ---------- modality-reask ----------


def _first_question(events: list[dict[str, Any]]) -> dict[tuple[Any, ...], tuple[int, str]]:
    """(tarefa, modalidade, rep) -> (1ª tentativa com bloco question.md, texto), do response.txt."""
    found: dict[tuple[Any, ...], tuple[int, str]] = {}
    for e in events:
        key = (e.get("task_id"), e.get("modality"), e.get("rep"))
        if e.get("event") != "attempt" or key in found:
            continue
        response = Path(e["artifact_path"]) / RESPONSE_FILENAME
        question = parse_question(response.read_text()) if response.is_file() else None
        if question is not None:
            found[key] = (e["attempt"], question)
    return found


def cmd_modality_reask(args: argparse.Namespace) -> int:
    """Reclassifica (sem LLM) trajetórias E2 cuja pergunta o parser antigo não reconheceu.

    A trajetória é cortada na 1ª tentativa com bloco question.md: final ASKED, tokens só até ali
    e check real no estado inicial (reset + initial.tf no Floci). As demais são copiadas iguais.
    """
    src = Path(args.src_run_dir)
    saved = json.loads((src / "run_config.json").read_text(encoding="utf-8"))
    cfg = saved["config"]
    if not _allow_question(cfg):
        raise SystemExit("modality-reask só vale para runs com [modality] allow_question")
    if cfg.get("sandbox", {}).get("kind", FlociExecutor.KIND) == RealAwsExecutor.KIND:
        raise SystemExit("modality-reask só roda no Floci: o run de origem usou a AWS real")
    events = load_events(src / "events.jsonl")
    questions = _first_question(events)
    finals = {(e["task_id"], e["modality"], e["rep"]): e for e in events if e["event"] == "final"}
    # Só muda o que o harness corrigido teria encerrado antes (ou com outro desfecho).
    fix = {
        key: q for key, q in questions.items()
        if key in finals and not (finals[key].get("asked") and finals[key]["attempts"] == q[0])
    }  # fmt: skip
    tasks = {t.task_id: t for t in load_tasks()}
    max_chars = cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS)
    runs_dir = src.parent
    run_id = new_run_id(f"reask-{src.name}")
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
                "note": (
                    "modality-reask: trajetórias cuja 1ª pergunta veio como ```question.md "
                    "(sem title=) foram cortadas nessa tentativa e reclassificadas como ASKED, "
                    "sem LLM; check rodado de novo no estado inicial. Demais eventos copiados."
                ),
                "reclassified": len(fix),
                **_git_provenance(),
                "versions": {
                    "toolchain_image": toolchain.image,
                    **_sandbox_provenance(executor),
                },
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    both_files: dict[tuple[Any, ...], bool] = {}
    recorder = JsonlRecorder(run_dir / "events.jsonl")
    try:
        for e in events:
            key = (e.get("task_id"), e.get("modality"), e.get("rep"))
            if key not in fix:
                recorder.record(e)
                continue
            q_attempt, question = fix[key]
            if e["event"] == "attempt" and e["attempt"] < q_attempt:
                recorder.record(e)
            elif e["event"] == "attempt" and e["attempt"] == q_attempt:
                modality = MODALITIES[e["modality"]]
                raw = (Path(e["artifact_path"]) / RESPONSE_FILENAME).read_text()
                content = parse_files(raw).get(modality.filename)
                both_files[key] = content is not None
                e.update(
                    format_ok=True, exec_ok=None, asked=True, question=question,
                    both_files=both_files[key], unsupported=False,
                    uses_local_exec=uses_local_exec(modality, content), output="",
                    raw_output="", feedback_sent=None, termination=ModTermination.ASKED.value,
                    artifact_hash=None, answer=None, reclassified=True,
                )  # fmt: skip
                recorder.record(e)
            elif e["event"] == "final":
                task, modality = tasks[e["task_id"]], MODALITIES[e["modality"]]
                kept = [
                    a for a in events
                    if a["event"] == "attempt" and (a["task_id"], a["modality"], a["rep"]) == key
                    and a["attempt"] <= q_attempt
                ]  # fmt: skip
                tokens_in = sum(a["input_tokens"] for a in kept)
                tokens_out = sum(a["output_tokens"] for a in kept)
                # Check real no estado inicial, como o harness corrigido faria.
                executor.reset()
                initial, path = apply_initial(
                    toolchain, executor, task, modality, runs_dir, run_id, max_chars,
                    rep=e["rep"], name="reask_initial",
                )  # fmt: skip
                if initial.ok:
                    check_pass, details = run_check(toolchain, task, path, settle=True)
                else:
                    check_pass, details = False, {"error": "initial.tf não aplicou"}
                e.update(
                    attempts=q_attempt, termination=ModTermination.ASKED.value,
                    exec_ok=False, check_pass=check_pass, check_details=details,
                    success=task.expect == "ask", asked=True, question=question,
                    both_files=both_files[key], input_tokens=tokens_in,
                    output_tokens=tokens_out, total_tokens=tokens_in + tokens_out,
                    reclassified=True,
                )  # fmt: skip
                recorder.record(e)
                print(f"{key[0]} × {key[1]} rep {key[2]}: ASKED na tentativa {q_attempt} "
                      f"(check={check_pass})", file=sys.stderr)  # fmt: skip
    finally:
        recorder.close()
        final_cleanup(executor)
    print(f"reclassificadas: {len(fix)}", file=sys.stderr)
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
    targets: list[Target],
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
    targets: list[Target],
) -> dict[str, Any]:  # fmt: skip
    toolchain, executor = targets[0].toolchain, targets[0].executor
    return {
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
    agent, targets = _build_agent(cfg), _build_targets(cfg)
    run_dir.mkdir(parents=True, exist_ok=False)
    run_config = _agent_run_config(cfg, tasks, reps, run_dir, agent, targets)
    (run_dir / "run_config.json").write_text(json.dumps(run_config, indent=2, ensure_ascii=False))
    _agent_loop(cfg, _agent_plan(cfg, tasks, reps), runs_dir, run_id, agent, targets)
    print(_agent_summary(load_events(run_dir / "events.jsonl")), file=sys.stderr)
    print(run_dir)
    return 0


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
    for task, cond, model, rep in pending:  # diretório sem evento (queda antes do registro)
        shutil.rmtree(udir((task.task_id, cond, model, rep)), ignore_errors=True)
    _record_resume(
        config_path, saved, run_id, len(plan) - len(pending), len(discarded), len(pending),
        "units",
    )  # fmt: skip
    _agent_loop(cfg, pending, runs_dir, run_id, _build_agent(cfg), _build_targets(cfg))
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


def cmd_agent_judge(args: argparse.Namespace) -> int:
    """Classifica a mensagem final de cada trajetória ainda sem veredito (judge.jsonl).

    `--sample N` amostra trajetórias já julgadas para a leitura à mão; `--agreement CSV` compara
    o juiz com a leitura à mão. Nenhum dos dois chama o juiz.
    """
    if args.agreement is not None:
        return _agent_judge_agreement(args.agreement)
    if args.run_dir is None:
        raise SystemExit("erro: informe o diretório do run (ou --agreement <csv>)")
    run_dir = Path(args.run_dir)
    if args.sample is not None:
        return _agent_judge_sample(run_dir, args.sample, args.seed)
    cfg = json.loads((run_dir / "run_config.json").read_text())["config"]
    judge = LlmJudge(model=args.model or cfg.get("judge", {}).get("model", DEFAULT_JUDGE_MODEL))
    judge_path = run_dir / JUDGE_FILENAME
    done = set()
    if judge_path.exists() and not args.redo:
        done = {j["trajectory_id"] for j in load_events(judge_path)}
    prompts = {t.task_id: agent_prompt(t) for t in load_tasks()}
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


def cmd_agent_metrics(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    events = load_events(run_dir / "events.jsonl")
    judge_path = run_dir / JUDGE_FILENAME
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


# ---------- run ----------


def _load_prevalidation(path: Path | None, allow_missing: bool) -> dict[str, dict[str, Any]]:
    if path is None:
        if not allow_missing:
            raise SystemExit(
                "erro: informe --prevalidation <prevalidation.jsonl> "
                "(ou --no-prevalidation, só para smoke)"
            )
        print("aviso: --no-prevalidation; assumindo harness_valid=True e sandbox_supported=True",
              file=sys.stderr)  # fmt: skip
        return {}
    with path.open(encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return {r["task_id"]: r for r in rows}


def _build_run(cfg: dict[str, Any]) -> tuple[Toolchain, FlociExecutor, dict, IacEvalV2Adapter]:
    """Montagem comum a run e resume: toolchain, executor, gates e adapter."""
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    gates = build_gates(cfg, toolchain, executor)
    adapter = IacEvalV2Adapter(DATA_DIR, cfg["dataset"]["revision"])
    return toolchain, executor, gates, adapter


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


def _run_pairs(
    cfg: dict[str, Any],
    executor: FlociExecutor,
    gates: dict,
    adapter: IacEvalV2Adapter,
    backend: Any,
    conditions: list[Condition],
    pairs: list[tuple[Task, TaskOracle]],
    reps: int,
    pre: dict[str, dict[str, Any]],
    runs_dir: Path,
    run_id: str,
    skip: Container[tuple[str, int]] = frozenset(),
) -> None:
    """Loop principal: roda cada (task, rep) fora de `skip`, gravando em events.jsonl (append)."""
    ctx = RunContext(
        run_id=run_id,
        runs_dir=runs_dir,
        dataset_version=adapter.version,
        model_provider=cfg["agent"]["provider"],
        max_feedback_chars=cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS),
        override=lambda artifact: executor.override_for(artifact.files),
    )
    recorder = JsonlRecorder(runs_dir / run_id / "events.jsonl")
    try:
        for task, oracle in pairs:
            p = pre.get(task.task_id, {})
            for rep in range(1, reps + 1):
                if (task.task_id, rep) in skip:
                    continue
                run_task(
                    task, oracle, rep, conditions, backend, gates, recorder, ctx,
                    harness_valid=p.get("harness_valid", True),
                    sandbox_supported=p.get("sandbox_supported", True),
                )  # fmt: skip
                print(f"{task.task_id} rep {rep} ok", file=sys.stderr)
    finally:
        recorder.close()


def cmd_run(args: argparse.Namespace) -> int:
    if args.resume is not None:
        return cmd_resume(args)
    if args.config is None:
        raise SystemExit("erro: informe --config (ou --resume RUN_DIR)")
    cfg = load_config(args.config)
    pre = _load_prevalidation(args.prevalidation, args.no_prevalidation)
    run_cfg = cfg["run"]
    toolchain, executor, gates, adapter = _build_run(cfg)
    pairs = select_tasks(adapter.load(), args.tasks, args.limit)
    conditions = [CONDITIONS[c] for c in run_cfg["conditions"]]
    reps = args.reps or run_cfg.get("reps", 1)
    backend = _build_backend(cfg)
    runs_dir = Path(run_cfg.get("runs_dir", "runs"))
    run_id = new_run_id(run_cfg["name"])
    (runs_dir / run_id).mkdir(parents=True, exist_ok=False)
    (runs_dir / run_id / "run_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "reps": reps,
                "tasks": [t.task_id for t, _ in pairs],
                **collect_provenance(toolchain, executor, adapter, runs_dir / run_id),
                "prevalidation": str(args.prevalidation) if args.prevalidation else None,
                "prevalidation_sha256": (
                    hashlib.sha256(args.prevalidation.read_bytes()).hexdigest()
                    if args.prevalidation
                    else None
                ),
                "no_prevalidation": args.no_prevalidation,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    _run_pairs(
        cfg, executor, gates, adapter, backend, conditions, pairs, reps, pre, runs_dir, run_id
    )
    print(runs_dir / run_id)
    return 0


# ---------- resume ----------

# Opções que definem o run; no resume vêm do run_config.json e não podem ser repassadas.
_RUN_DEFINING_OPTIONS = ("config", "tasks", "limit", "reps", "prevalidation", "no_prevalidation")


def pair_completion(
    events: list[dict[str, Any]],
    conditions: list[str] | None,
    fields: tuple[str, ...] = ("task_id", "rep"),
) -> tuple[set[tuple], set[tuple]]:
    """(completos, incompletos) por unidade `fields`.

    Completo = tem um evento `final` para cada condição; com `conditions=None` (modalidades,
    sem condição), basta um evento `final`.
    """
    finals: dict[tuple, set[str | None]] = {}
    for e in events:
        done = finals.setdefault(tuple(e[f] for f in fields), set())
        if e["event"] == "final":
            done.add(e.get("condition"))
    required = set(conditions) if conditions is not None else {None}
    complete = {k for k, done in finals.items() if required <= done}
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


def cmd_resume(args: argparse.Namespace) -> int:
    # Presença, não veracidade: --reps 0 / --limit 0 também conflitam (0 == False, daí o `is`).
    given = [
        f"--{o.replace('_', '-')}"
        for o in _RUN_DEFINING_OPTIONS
        if getattr(args, o) is not None and getattr(args, o) is not False
    ]
    if given:
        raise SystemExit(
            f"erro: --resume não aceita {', '.join(given)} (a configuração vem do run_config.json)"
        )
    run_dir = Path(args.resume)
    config_path = run_dir / "run_config.json"
    if not config_path.is_file():
        raise SystemExit(f"erro: {config_path} não encontrado")
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    cfg, reps = saved["config"], saved["reps"]
    pre_path = Path(saved["prevalidation"]) if saved.get("prevalidation") else None
    if pre_path is not None:
        if not pre_path.is_file():
            raise SystemExit(f"erro: pré-validação do run não encontrada: {pre_path}")
        sha = hashlib.sha256(pre_path.read_bytes()).hexdigest()
        if sha != saved["prevalidation_sha256"]:
            raise SystemExit(
                f"erro: {pre_path} mudou desde o início do run (sha256 {sha} != "
                f"{saved['prevalidation_sha256']}); não dá para retomar com outra pré-validação"
            )
    pre = _load_prevalidation(pre_path, saved.get("no_prevalidation", False))
    _, executor, gates, adapter = _build_run(cfg)
    # select_tasks com lista vazia devolveria tudo; um run sem tarefas segue sem tarefas.
    pairs = select_tasks(adapter.load(), saved["tasks"], None) if saved["tasks"] else []
    conditions = [CONDITIONS[c] for c in cfg["run"]["conditions"]]
    backend = _build_backend(cfg)
    runs_dir, run_id = run_dir.parent, run_dir.name
    complete, discarded = discard_incomplete(runs_dir, run_id, cfg["run"]["conditions"])
    planned = [(t.task_id, rep) for t, _ in pairs for rep in range(1, reps + 1)]
    pending = [p for p in planned if p not in complete]
    # Par sem evento mas com diretório (queda antes do 1º registro): limpa para recriar.
    for task_id, rep in pending:
        shutil.rmtree(pair_dir(runs_dir, run_id, task_id, rep), ignore_errors=True)
    skipped = len(planned) - len(pending)
    saved.setdefault("resumes", []).append(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            **_git_provenance(),
            "skipped_pairs": skipped,
            "discarded_pairs": len(discarded),
        }
    )
    config_path.write_text(json.dumps(saved, indent=2, ensure_ascii=False))
    print(
        f"resume {run_id}: {skipped} pares completos pulados, "
        f"{len(discarded)} incompletos descartados, {len(pending)} a rodar",
        file=sys.stderr,
    )
    _run_pairs(
        cfg, executor, gates, adapter, backend, conditions, pairs, reps, pre, runs_dir, run_id,
        complete,
    )  # fmt: skip
    print(run_dir)
    return 0


# ---------- metrics ----------


def cmd_metrics(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    cfg = json.loads((run_dir / "run_config.json").read_text())["config"]
    prices = {m: tuple(p) for m, p in cfg.get("prices", {}).items()}
    metrics = compute_metrics(load_events(run_dir / "events.jsonl"), prices)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iac-harness")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, config_required: bool = True) -> None:
        p.add_argument("--config", type=Path, required=config_required)
        p.add_argument("--tasks", nargs="+")
        p.add_argument("--limit", type=int)

    p_pre = sub.add_parser("prevalidate", help="roda a referência de cada tarefa pela cadeia")
    common(p_pre)
    p_pre.set_defaults(func=cmd_prevalidate)

    p_miac = sub.add_parser(
        "prevalidate-miac", help="roda initial e expected das triplas Multi-IaC-Eval pela cadeia"
    )
    common(p_miac)
    p_miac.add_argument("--csv", type=Path, required=True, help="test.csv do Multi-IaC-Eval")
    p_miac.add_argument(
        "--sequential",
        action="store_true",
        help="nas utilizáveis, aplica initial e depois expected no mesmo estado (exercita update)",
    )
    p_miac.set_defaults(func=cmd_prevalidate_miac)

    p_mpre = sub.add_parser(
        "modality-prevalidate",
        help="checa referência e estado inicial de cada tarefa × modalidade (Terraform/CLI/boto3)",
    )
    p_mpre.add_argument("--config", type=Path, required=True)
    p_mpre.add_argument("--tasks", nargs="+")
    p_mpre.set_defaults(func=cmd_modality_prevalidate)

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

    p_mrea = sub.add_parser(
        "modality-reask",
        help="reclassifica sem LLM as perguntas (```question.md) que o parser antigo não leu",
    )
    p_mrea.add_argument("src_run_dir", type=Path, help="run dir de um modality-run do E2")
    p_mrea.set_defaults(func=cmd_modality_reask)

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
    p_ajud.set_defaults(func=cmd_agent_judge)

    p_amet = sub.add_parser(
        "agent-metrics", help="métricas de um agent-run (condição × modelo, estratos, alvo)"
    )
    p_amet.add_argument("run_dir", type=Path)
    p_amet.add_argument("--format", choices=("json", "table"), default="json")
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

    p_run = sub.add_parser("run", help="roda o agente nas condições configuradas")
    common(p_run, config_required=False)  # obrigatório sem --resume (validado em cmd_run)
    p_run.add_argument("--reps", type=int)
    p_run.add_argument("--prevalidation", type=Path)
    p_run.add_argument(
        "--no-prevalidation",
        action="store_true",
        help="roda sem pré-validação (só smoke): assume harness_valid e sandbox_supported",
    )
    p_run.add_argument(
        "--resume",
        type=Path,
        metavar="RUN_DIR",
        help="retoma um run interrompido: pula pares (task, rep) completos e refaz os incompletos",
    )
    p_run.set_defaults(func=cmd_run)

    p_met = sub.add_parser("metrics", help="imprime as métricas de uma execução")
    p_met.add_argument("run_dir", type=Path)
    p_met.set_defaults(func=cmd_metrics)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
