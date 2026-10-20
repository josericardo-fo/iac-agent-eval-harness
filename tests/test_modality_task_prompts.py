"""prompt.md das tarefas vindas do IaC-Eval v2 = enunciado do CSV sem o bloco "## Intent".

Garante que nenhuma instância do dataset foi editada à mão.
"""

import csv
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "iac_eval_v2_defefc108d437f5659f40be66fc863833e9c96fb.csv"
TASKS_DIR = ROOT / "src" / "iac_harness" / "modalities" / "tasks"
ORIGIN_PREFIX = "iac-eval-v2:"


def _dataset_tasks() -> list[tuple[Path, str]]:
    tasks = []
    for meta_path in sorted(TASKS_DIR.glob("*/meta.toml")):
        origin = tomllib.loads(meta_path.read_text(encoding="utf-8")).get("origin", "")
        if origin.startswith(ORIGIN_PREFIX):
            tasks.append((meta_path.parent, origin.removeprefix(ORIGIN_PREFIX)))
    return tasks


def _expected_prompt(csv_prompt: str) -> str:
    # Mesma normalização das 5 primeiras: corta em "\n\n## Intent", rstrip e um "\n" final.
    return csv_prompt.split("\n\n## Intent")[0].rstrip() + "\n"


@pytest.fixture(scope="module")
def csv_prompts() -> dict[str, str]:
    if not CSV_PATH.exists():
        pytest.skip(f"CSV do IaC-Eval v2 ausente: {CSV_PATH}")
    csv.field_size_limit(2**31 - 1)
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return {row["task_id"]: row["prompt"] for row in csv.DictReader(f)}


@pytest.mark.parametrize(
    ("task_dir", "dataset_id"), _dataset_tasks(), ids=lambda v: getattr(v, "name", v)
)
def test_prompt_matches_csv_without_intent(task_dir, dataset_id, csv_prompts):
    prompt_md = (task_dir / "prompt.md").read_bytes().decode("utf-8")
    assert "\n\n## Intent" in csv_prompts[dataset_id]
    assert prompt_md == _expected_prompt(csv_prompts[dataset_id])
