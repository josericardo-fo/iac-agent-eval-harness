"""Montagem comum aos comandos: config, toolchain, executor, seleção de tarefas e resume."""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iac_harness.cli.provenance import _git_provenance
from iac_harness.datasets import ambig_iac
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.spec import FREE, MODALITIES
from iac_harness.toolchain import (
    DEFAULT_ENDPOINT,
    DEFAULT_REGION,
    DEFAULT_TIMEOUT_S,
    Toolchain,
)
from iac_harness.workspace import pair_dir

EXECUTORS: dict[str, type[FlociExecutor]] = {
    cls.KIND: cls for cls in (FlociExecutor, RealAwsExecutor)
}
DATA_DIR = Path("data")


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

    `free` é a forma livre: roda sozinha e sem `allow_question` (uma variável por vez).
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


def _allow_question(cfg: dict[str, Any]) -> bool:
    """`[modality] allow_question`: o agente pode perguntar em vez de agir."""
    return bool(cfg.get("modality", {}).get("allow_question", False))


# ---------- resume (modality-run --resume e agent-resume) ----------


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
