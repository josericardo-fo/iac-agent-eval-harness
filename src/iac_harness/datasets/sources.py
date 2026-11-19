"""TaskSource: de onde vêm as tarefas de um run (diretório de tarefas ou adapter de dataset).

A config escolhe a fonte por `[modality] dataset` (chave de `TASK_SOURCES`); sem ela, as tarefas
vêm de um diretório (`[modality] tasks_dir`, padrão `modalities/tasks/`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from iac_harness.datasets import ambig_iac
from iac_harness.modalities.loader import TASKS_DIR, ModalityTask, load_tasks


class TaskSource(Protocol):
    def load(self) -> list[ModalityTask]: ...

    def provenance(self) -> dict[str, Any] | None:
        """Procedência gravada no run_config (`dataset`); None = nada a gravar."""
        ...


@dataclass(frozen=True)
class DirectorySource:
    """Um diretório com uma pasta por tarefa (`modalities/tasks/` é um deles)."""

    root: Path = TASKS_DIR

    def load(self) -> list[ModalityTask]:
        return load_tasks(self.root)

    def provenance(self) -> None:
        return None


@dataclass(frozen=True)
class AmbigIacSource:
    """Ambig-IaC: linhas do JSONL em `data_dir`; `rules_file` aplica as regras
    discriminantes (`amendment.discriminating_rules`) por tarefa."""

    data_dir: Path
    prompt_field: str = "prompt"
    rules_file: Path | None = None

    def load(self) -> list[ModalityTask]:
        tasks = ambig_iac.load_tasks(self.data_dir, self.prompt_field)
        if self.rules_file is None:
            return tasks
        rules = json.loads(self.rules_file.read_text())["amendment"]["discriminating_rules"]
        return [
            replace(t, rego_rules=tuple(rules[t.task_id])) if t.task_id in rules else t
            for t in tasks
        ]

    def provenance(self) -> dict[str, Any]:
        return ambig_iac.provenance(self.prompt_field)


def _ambig_iac(mod_cfg: dict[str, Any], data_dir: Path) -> AmbigIacSource:
    rules_file = mod_cfg.get("rules_file")
    return AmbigIacSource(
        data_dir,
        mod_cfg.get("prompt_field", "prompt"),
        Path(rules_file) if rules_file is not None else None,
    )


# `[modality] dataset` → fonte, montada a partir da seção [modality] e do diretório de dados.
TASK_SOURCES: dict[str, Callable[[dict[str, Any], Path], TaskSource]] = {
    ambig_iac.DATASET: _ambig_iac,
}


def task_source(mod_cfg: dict[str, Any], data_dir: Path) -> TaskSource:
    """Fonte da seção [modality]: `dataset` (registro) ou, sem ele, `tasks_dir` (diretório)."""
    dataset = mod_cfg.get("dataset")
    if dataset is None:
        tasks_dir = mod_cfg.get("tasks_dir")
        return DirectorySource(Path(tasks_dir)) if tasks_dir else DirectorySource()
    if dataset not in TASK_SOURCES:
        raise SystemExit(f"erro: dataset desconhecido {dataset!r} (use {ambig_iac.DATASET!r})")
    return TASK_SOURCES[dataset](mod_cfg, data_dir)
