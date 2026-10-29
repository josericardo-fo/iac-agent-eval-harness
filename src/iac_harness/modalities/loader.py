"""Carga das tarefas do teste de modalidades (src/iac_harness/modalities/tasks/<id>/)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from iac_harness.modalities.spec import MODALITIES

TASKS_DIR = Path(__file__).parent / "tasks"
CATEGORIES = ("create", "modify", "delete", "query")
UPSTREAM_DIRNAME = "upstream"


@dataclass(frozen=True)
class ModalityTask:
    task_id: str
    category: str
    origin: str
    region: str
    prompt: str
    check: str  # código do check.py (roda fora do workspace)
    references: dict[str, str]  # nome da modalidade → conteúdo da referência
    initial_tf: str | None = None  # estado inicial (só em modify/delete/query)
    hide_initial: bool = False  # o prompt não mostra o initial.tf (o agente descobre o estado)
    answer: bool = False  # fora da consulta, também pede resposta em JSON
    missing_references: tuple[str, ...] = ()  # modalidades sem arquivo de referência
    missing_reason: str = ""  # por que faltam (obrigatório com missing_references)
    upstream: dict[str, str] = field(default_factory=dict)  # upstream/: arquivo → conteúdo
    collateral: str | None = None  # código do collateral.py (retrato do estado), se existir

    @property
    def wants_answer(self) -> bool:
        """Se a tarefa pede resposta em JSON (consulta, ou `answer` nas demais)."""
        return self.category == "query" or self.answer


def load_task(path: Path) -> ModalityTask:
    meta = tomllib.loads((path / "meta.toml").read_text())
    if meta["id"] != path.name:
        raise ValueError(f"{path}: id {meta['id']!r} difere do nome da pasta")
    if meta["category"] not in CATEGORIES:
        raise ValueError(f"{path}: categoria inválida {meta['category']!r}")
    initial = path / "initial.tf"
    if (meta["category"] != "create") != initial.exists():
        raise ValueError(
            f"{path}: initial.tf é obrigatório em modify/delete/query e proibido em create"
        )
    missing = tuple(meta.get("missing_references", ()))
    unknown = set(missing) - set(MODALITIES)
    if unknown:
        raise ValueError(f"{path}: missing_references com modalidades inválidas {sorted(unknown)}")
    if missing and not meta.get("missing_reason"):
        raise ValueError(f"{path}: missing_reason é obrigatório com missing_references")
    collateral = path / "collateral.py"
    upstream_dir = path / UPSTREAM_DIRNAME
    upstream = (
        {p.name: p.read_text() for p in sorted(upstream_dir.iterdir()) if p.is_file()}
        if upstream_dir.is_dir()
        else {}
    )
    return ModalityTask(
        task_id=meta["id"],
        category=meta["category"],
        origin=meta["origin"],
        region=meta["region"],
        prompt=(path / "prompt.md").read_text(),
        check=(path / "check.py").read_text(),
        references={
            m.name: (path / m.reference).read_text()
            for m in MODALITIES.values()
            if m.name not in missing
        },
        initial_tf=initial.read_text() if initial.exists() else None,
        hide_initial=meta.get("hide_initial", False),
        answer=meta.get("answer", False),
        missing_references=missing,
        missing_reason=meta.get("missing_reason", ""),
        upstream=upstream,
        collateral=collateral.read_text() if collateral.exists() else None,
    )


def load_tasks(root: Path = TASKS_DIR) -> list[ModalityTask]:
    """Todas as tarefas, em ordem de id; arquivo obrigatório ausente levanta erro."""
    return [load_task(p) for p in sorted(root.iterdir()) if (p / "meta.toml").is_file()]
