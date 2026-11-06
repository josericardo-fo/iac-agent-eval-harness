"""Estratos das tarefas (configs/strata.toml): os meta.toml não mudam; a classificação fica aqui."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

DEFAULT_STRATA_FILE = Path("configs/strata.toml")
ORIGINS = ("iac-eval-v2", "aws-bench", "authored", "harness")
INTENTS = ("clear", "ambiguous", "false-premise", "side-effect-risk", "cost-lock")
TARGETS = ("both", "real-only")
# Eixos usados nas métricas (services é lista: cada serviço vira um estrato).
AXES = ("category", "origin", "difficulty", "intent", "target", "services")


def load_strata(path: Path = DEFAULT_STRATA_FILE) -> dict[str, dict[str, Any]]:
    """task_id → estratos; valor fora do vocabulário levanta erro."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks", {})
    for task_id, row in tasks.items():
        for key, allowed in (("origin", ORIGINS), ("intent", INTENTS), ("target", TARGETS)):
            if row.get(key) not in allowed:
                raise ValueError(f"{path}: {task_id}.{key} inválido: {row.get(key)!r}")
        if not isinstance(row.get("services"), list) or not row["services"]:
            raise ValueError(f"{path}: {task_id}.services deve ser uma lista não vazia")
    return tasks


def strata_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Campos gravados no evento final (prefixo strata_ para não colidir com target/category)."""
    return {f"strata_{axis}": row.get(axis) for axis in AXES}
