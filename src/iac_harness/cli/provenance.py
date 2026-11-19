"""Procedência de cada run: commit do git e imagem do emulador (ou conta, na AWS real)."""

from __future__ import annotations

from typing import Any

from iac_harness.executors import Backend
from iac_harness.shell import query


def _git_provenance() -> dict[str, Any]:
    commit = query(["git", "rev-parse", "HEAD"])
    if commit is None or commit.startswith("erro:"):
        # Repositório sem commit (ou git indisponível): sem revisão para registrar.
        commit = None
    status = query(["git", "status", "--porcelain"])
    dirty = None if status is not None and status.startswith("erro:") else status is not None
    return {"git_commit": commit, "git_dirty": dirty}


def _sandbox_provenance(executor: Backend) -> dict[str, Any]:
    """Backend usado no run: tipo (`[sandbox] kind`) e o que o identifica (`Backend.provenance`).

    Floci: imagem, ID e digests do container. AWS real: conta, ARN e região (a guarda de
    identidade roda aqui, antes de tudo).
    """
    return executor.provenance()
