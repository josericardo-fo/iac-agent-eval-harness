"""CLI: prevalidate | run | metrics. Mapeamentos simples nome → classe, sem registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iac_harness.agents.anthropic_backend import DEFAULT_MAX_TOKENS, AnthropicBackend
from iac_harness.agents.prompts import PROMPT_VERSION
from iac_harness.conditions import A, B, C, Condition
from iac_harness.datasets.iac_eval_v2 import IacEvalV2Adapter
from iac_harness.datasets.multi_iac_eval import MultiIacEvalAdapter
from iac_harness.executors.floci import UNSUPPORTED_PATTERNS_VERSION, FlociExecutor
from iac_harness.feedback import DEFAULT_MAX_CHARS
from iac_harness.metrics import compute_metrics, load_events
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.runner import COMMON_CHAIN, RunContext, SandboxGate, run_gates, run_task
from iac_harness.toolchain import DEFAULT_ENDPOINT, DEFAULT_REGION, DEFAULT_TIMEOUT_S, Toolchain
from iac_harness.types import Artifact, CheckStatus, Stage, Task, TaskOracle
from iac_harness.verifiers.opa import OpaVerifier
from iac_harness.verifiers.terraform import PLAN_FILE, PlanVerifier, ValidateVerifier, error_blocks
from iac_harness.workspace import OVERRIDE_FILENAME, create_workspace

CONDITIONS: dict[str, Condition] = {"A": A, "B": B, "C": C}
BACKENDS = {"anthropic": AnthropicBackend}
DATA_DIR = Path("data")
MIRROR_DIR = "/opt/terraform-providers"  # filesystem mirror dentro da imagem da toolchain
_RESOURCE = re.compile(r"\b(aws(?:cc)?_\w+)\b")
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
    )


def build_executor(cfg: dict[str, Any], toolchain: Toolchain) -> FlociExecutor:
    sb = cfg.get("sandbox", {})
    kwargs = {k: sb[k] for k in ("endpoint", "host_endpoint", "container") if k in sb}
    return FlociExecutor(toolchain, **kwargs)


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
    floci_image = _query(["docker", "inspect", "--format", "{{.Config.Image}}", executor.container])
    floci_image_id = _query(["docker", "inspect", "--format", "{{.Image}}", executor.container])
    floci_digests = None
    if floci_image_id and not floci_image_id.startswith("erro:"):
        floci_digests = _query(
            ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", floci_image_id]
        )
    return {
        **_git_provenance(),
        "versions": {
            **toolchain.versions(run_dir),
            "toolchain_image": toolchain.image,
            "toolchain_image_id": toolchain_id,
            "providers": _mirror_providers(toolchain, run_dir),
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


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    pre = _load_prevalidation(args.prevalidation, args.no_prevalidation)
    run_cfg = cfg["run"]
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    gates = build_gates(cfg, toolchain, executor)
    adapter = IacEvalV2Adapter(DATA_DIR, cfg["dataset"]["revision"])
    pairs = select_tasks(adapter.load(), args.tasks, args.limit)
    conditions = [CONDITIONS[c] for c in run_cfg["conditions"]]
    reps = args.reps or run_cfg.get("reps", 1)
    agent_cfg = cfg["agent"]
    backend_cls = BACKENDS[agent_cfg["provider"]]
    backend = backend_cls(
        model=agent_cfg["model"],
        max_tokens=agent_cfg.get("max_tokens", DEFAULT_MAX_TOKENS),
        effort=agent_cfg.get("effort"),
    )
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
    ctx = RunContext(
        run_id=run_id,
        runs_dir=runs_dir,
        dataset_version=adapter.version,
        model_provider=agent_cfg["provider"],
        max_feedback_chars=cfg.get("feedback", {}).get("max_chars", DEFAULT_MAX_CHARS),
        override=lambda artifact: executor.override_for(artifact.files),
    )
    recorder = JsonlRecorder(runs_dir / run_id / "events.jsonl")
    try:
        for task, oracle in pairs:
            p = pre.get(task.task_id, {})
            for rep in range(1, reps + 1):
                run_task(
                    task, oracle, rep, conditions, backend, gates, recorder, ctx,
                    harness_valid=p.get("harness_valid", True),
                    sandbox_supported=p.get("sandbox_supported", True),
                )  # fmt: skip
                print(f"{task.task_id} rep {rep} ok", file=sys.stderr)
    finally:
        recorder.close()
    print(runs_dir / run_id)
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

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", type=Path, required=True)
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

    p_run = sub.add_parser("run", help="roda o agente nas condições configuradas")
    common(p_run)
    p_run.add_argument("--reps", type=int)
    p_run.add_argument("--prevalidation", type=Path)
    p_run.add_argument(
        "--no-prevalidation",
        action="store_true",
        help="roda sem pré-validação (só smoke): assume harness_valid e sandbox_supported",
    )
    p_run.set_defaults(func=cmd_run)

    p_met = sub.add_parser("metrics", help="imprime as métricas de uma execução")
    p_met.add_argument("run_dir", type=Path)
    p_met.set_defaults(func=cmd_metrics)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
