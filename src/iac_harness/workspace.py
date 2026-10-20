"""Diretórios de trabalho por tentativa. Nunca sobrescreve trajetórias anteriores."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from iac_harness.types import Artifact

OVERRIDE_FILENAME = "zz_harness_override.tf"
RESPONSE_FILENAME = "response.txt"  # resposta bruta do modelo, por tentativa


@dataclass(frozen=True)
class Workspace:
    path: Path
    artifact_hash: str


def _safe_name(value: str) -> str:
    return value.replace("/", "__")


def pair_dir(runs_dir: Path, run_id: str, task_id: str, rep: int) -> Path:
    """Diretório de artefatos de um par (task, rep): runs/<run_id>/<task>/<rep>/."""
    return runs_dir / run_id / _safe_name(task_id) / str(rep)


def inject_override(path: Path, content: str) -> None:
    """Escreve o override de provider; o conteúdo concreto vem de quem chama."""
    (path / OVERRIDE_FILENAME).write_text(content)


def create_workspace(
    runs_dir: Path,
    run_id: str,
    task_id: str,
    rep: int,
    condition: str,
    name: str,
    artifact: Artifact,
    override: str | None = None,
) -> Workspace:
    """Cria runs/<run_id>/<task>/<rep>/<cond>/<name>/ e grava os arquivos do artefato."""
    path = pair_dir(runs_dir, run_id, task_id, rep) / condition / name
    path.mkdir(parents=True, exist_ok=False)
    root = path.resolve()
    for filename, content in artifact.files.items():
        target = (path / filename).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ValueError(f"nome de arquivo fora do workspace: {filename!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    if override is not None:
        inject_override(path, override)
    return Workspace(path=path, artifact_hash=artifact.sha256)
