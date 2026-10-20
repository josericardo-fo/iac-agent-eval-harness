"""Sorteio fixo das 7 tarefas de criação novas para o experimento de modalidades.

Critério documentado em docs/selecao-tarefas-modalidades.md. Determinístico: rodar de novo
gera o mesmo src/iac_harness/modalities/selection.json.

Uso: uv run python scripts/select_modality_tasks.py
"""

import csv
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "iac_eval_v2_defefc108d437f5659f40be66fc863833e9c96fb.csv"
PREVALIDATION_PATH = ROOT / "runs" / "prevalidation-merged" / "prevalidation.jsonl"
OUTPUT_PATH = ROOT / "src" / "iac_harness" / "modalities" / "selection.json"

SEED = 20261020
ALREADY_USED = ["aws/task-108", "aws/task-115", "aws/task-230", "aws/task-231", "aws/task-234"]
MAX_PER_SERVICE = 2
TOOL_MENTION_RE = re.compile(r"terraform|\bhcl\b", re.IGNORECASE)
# Estrato -> (dificuldades do CSV, cota); a ordem do dict é a ordem do sorteio.
STRATA = {
    "1-2": ({1, 2}, 1),
    "3": ({3}, 2),
    "4": ({4}, 2),
    "5-6": ({5, 6}, 2),
}

RULES = {
    "a": "Excluir as 5 tarefas de criação já usadas (aws/task-108, 115, 230, 231, 234).",
    "b": 'Excluir reference_output com data "archive_file" ou resource "aws_lambda_function".',
    "c": "Excluir reference_output sem nenhum bloco resource (só data source).",
    "d": (
        "Serviço = segmento após 'aws_' do primeiro resource da reference_output; "
        "no máximo 2 por serviço entre as sorteadas (as 5 já usadas não contam; v2 da regra)."
    ),
    "e": "Estratos por difficulty: 1-2 -> 1, 3 -> 2, 4 -> 2, 5-6 -> 2.",
    "f": (
        "Por estrato, na ordem acima: ids ordenados, random.Random(f'{SEED}-{estrato}').shuffle, "
        "pega os que não estouram o teto global de serviço; o resto vira fila de substitutos."
    ),
    "g": (
        "Excluir enunciados (sem o Intent) que citam 'terraform' ou 'hcl': não são neutros entre "
        "modalidades e não podem ser editados (mesmo critério das 5 já usadas)."
    ),
}

RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"')
EXCLUDED_ZIP_RE = re.compile(r'data\s+"archive_file"|resource\s+"aws_lambda_function"')


def load_csv() -> dict[str, dict[str, str]]:
    csv.field_size_limit(2**31 - 1)
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        return {row["task_id"]: row for row in csv.DictReader(f)}


def load_pool() -> list[str]:
    pool = []
    with PREVALIDATION_PATH.open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record["sandbox_supported"] and record["harness_valid"]:
                pool.append(record["task_id"])
    return sorted(pool)


def service_of(reference_output: str) -> str:
    resource_type = RESOURCE_RE.search(reference_output).group(1)
    return resource_type.removeprefix("aws_").split("_")[0]


def first_prompt_line(prompt: str) -> str:
    # Pula o título "# Task NNN" e linhas vazias; só para exibição na tabela.
    statement = prompt.split("\n\n## Intent")[0]
    lines = [ln.strip() for ln in statement.splitlines() if ln.strip() and not ln.startswith("#")]
    return lines[0] if lines else ""


def main() -> None:
    rows = load_csv()
    pool = load_pool()

    excluded: dict[str, list[str]] = {"a": [], "b": [], "c": [], "g": []}
    candidates = []
    for task_id in pool:
        reference = rows[task_id]["reference_output"]
        if task_id in ALREADY_USED:
            excluded["a"].append(task_id)
        elif EXCLUDED_ZIP_RE.search(reference):
            excluded["b"].append(task_id)
        elif not RESOURCE_RE.search(reference):
            excluded["c"].append(task_id)
        elif TOOL_MENTION_RE.search(rows[task_id]["prompt"].split("\n\n## Intent")[0]):
            excluded["g"].append(task_id)
        else:
            candidates.append(task_id)

    service_count: Counter[str] = Counter()  # v2: o teto vale só entre as sorteadas

    selected: dict[str, list[str]] = {}
    substitutes: dict[str, list[str]] = {}
    for stratum, (difficulties, quota) in STRATA.items():
        ids = sorted(t for t in candidates if int(rows[t]["difficulty"]) in difficulties)
        random.Random(f"{SEED}-{stratum}").shuffle(ids)
        picked, queue = [], []
        for task_id in ids:
            service = service_of(rows[task_id]["reference_output"])
            if len(picked) < quota and service_count[service] < MAX_PER_SERVICE:
                picked.append(task_id)
                service_count[service] += 1
            else:
                queue.append(task_id)
        selected[stratum] = picked
        substitutes[stratum] = queue

    def describe(task_id: str) -> str:
        row = rows[task_id]
        service = service_of(row["reference_output"])
        return (
            f"{task_id:<14} {row['difficulty']:<5} {service:<12} {first_prompt_line(row['prompt'])}"
        )

    print(f"Pool: {len(pool)} tarefas; candidatas após a-c: {len(candidates)}")
    for rule, ids in excluded.items():
        print(f"Excluídas pela regra {rule}: {len(ids)}")
    print()
    print(f"{'estrato':<8} {'task_id':<14} {'dif':<5} {'serviço':<12} enunciado")
    for stratum, ids in selected.items():
        for task_id in ids:
            print(f"{stratum:<8} {describe(task_id)}")
    for stratum, ids in substitutes.items():
        print(f"\nSubstitutos do estrato {stratum} (em ordem):")
        for task_id in ids:
            print(f"  {describe(task_id)}")

    output = {
        "seed": SEED,
        "rules": RULES,
        "excluded": excluded,
        "selected": selected,
        "substitutes": substitutes,
    }
    OUTPUT_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nGravado em {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
