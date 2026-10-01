"""Adapter do IaC-Eval v2: baixa o data.csv na revisão fixa e produz (Task, TaskOracle)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import httpx2

from iac_harness.types import Task, TaskOracle

DEFAULT_REVISION = "defefc108d437f5659f40be66fc863833e9c96fb"
URL_TEMPLATE = "https://huggingface.co/datasets/iac-eval-v2/iac-eval-v2/resolve/{revision}/data.csv"

# Campos do CSV que entram em Task.metadata (o resto vira Task/TaskOracle).
_METADATA_FIELDS = (
    "suite",
    "resource",
    "intent",
    "source",
    "terraform_version",
    "opa_version",
    "has_starting_state",
    "opa_input_path",
)


class IacEvalV2Adapter:
    def __init__(self, cache_dir: Path, revision: str = DEFAULT_REVISION) -> None:
        self._cache_dir = Path(cache_dir)
        self.revision = revision

    @property
    def version(self) -> str:
        return self.revision

    @property
    def csv_path(self) -> Path:
        return self._cache_dir / f"iac_eval_v2_{self.revision}.csv"

    def _download(self) -> Path:
        path = self.csv_path
        if path.exists():
            return path
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        resp = httpx2.get(
            URL_TEMPLATE.format(revision=self.revision), follow_redirects=True, timeout=60
        )
        resp.raise_for_status()
        # Escrita atômica: evita cache corrompido se o processo morrer no meio.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(resp.content)
        tmp.replace(path)
        return path

    def load(self) -> list[tuple[Task, TaskOracle]]:
        text = self._download().read_text(encoding="utf-8")
        csv.field_size_limit(2**31 - 1)
        pairs: list[tuple[Task, TaskOracle]] = []
        for row in csv.DictReader(io.StringIO(text, newline="")):
            task = Task(
                task_id=row["task_id"],
                prompt=row["prompt"],
                difficulty=int(row["difficulty"]),
                metadata={k: row[k] for k in _METADATA_FIELDS},
            )
            oracle = TaskOracle(
                reference_files={"main.tf": row["reference_output"]},
                rego_policy=row["rego_policy"],
            )
            pairs.append((task, oracle))
        return pairs
