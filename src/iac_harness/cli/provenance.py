"""Procedência de cada run: commit do git e imagem do emulador (ou conta, na AWS real)."""

from __future__ import annotations

import subprocess
from typing import Any

from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import FlociExecutor


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
