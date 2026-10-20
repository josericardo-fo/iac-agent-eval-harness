"""Carga das tarefas do teste de modalidades (src/iac_harness/modalities/tasks/<id>/)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from iac_harness.modalities.spec import MODALITIES

TASKS_DIR = Path(__file__).parent / "tasks"
CATEGORIES = ("create", "modify")


@dataclass(frozen=True)
class ModalityTask:
    task_id: str
    category: str
    origin: str
    region: str
    prompt: str
    check: str  # código do check.py (roda fora do workspace)
    references: dict[str, str]  # nome da modalidade → conteúdo da referência
    initial_tf: str | None = None  # estado inicial (só nas tarefas de modificação)


def load_task(path: Path) -> ModalityTask:
    meta = tomllib.loads((path / "meta.toml").read_text())
    if meta["id"] != path.name:
        raise ValueError(f"{path}: id {meta['id']!r} difere do nome da pasta")
    if meta["category"] not in CATEGORIES:
        raise ValueError(f"{path}: categoria inválida {meta['category']!r}")
    initial = path / "initial.tf"
    if (meta["category"] == "modify") != initial.exists():
        raise ValueError(f"{path}: initial.tf é obrigatório só nas tarefas de modificação")
    return ModalityTask(
        task_id=meta["id"],
        category=meta["category"],
        origin=meta["origin"],
        region=meta["region"],
        prompt=(path / "prompt.md").read_text(),
        check=(path / "check.py").read_text(),
        references={m.name: (path / m.reference).read_text() for m in MODALITIES.values()},
        initial_tf=initial.read_text() if initial.exists() else None,
    )


def load_tasks(root: Path = TASKS_DIR) -> list[ModalityTask]:
    """Todas as tarefas, em ordem de id; arquivo obrigatório ausente levanta erro."""
    return [load_task(p) for p in sorted(root.iterdir()) if (p / "meta.toml").is_file()]
