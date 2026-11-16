"""Sorteio fixo das 50 tarefas do E16 (Ambig-IaC), registro metodológico, linha E16.

Critério escrito no registro antes do sorteio: 50 tarefas sorteadas com semente fixa entre as que
passam no gate (a referência `main_tf` passa no oráculo e um `main.tf` vazio reprova).

Como o gate custa containers, ele roda na ordem do sorteio até juntar as aprovadas: a ordem é uma
permutação semeada dos 300 ids, e as selecionadas são as N primeiras aprovadas nessa ordem
(equivale a sortear N entre as aprovadas). As aprovadas seguintes são a fila de substitutos.

Uso (determinístico: rodar de novo gera o mesmo arquivo):
  1. uv run python scripts/select_e16_tasks.py
     grava `configs/e16-selection.json` só com a ordem;
  2. uv run iac-harness ambig-prevalidate --config configs/e16-ambig-iac.toml \\
         --order configs/e16-selection.json --target-pass 60
  3. uv run python scripts/select_e16_tasks.py --prevalidation runs/<run>/prevalidation.jsonl
     completa selecionadas, substitutos e reprovadas no gate.
"""

import argparse
import json
import random
from pathlib import Path

from iac_harness.datasets import ambig_iac

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = ROOT / "configs" / "e16-selection.json"

# Data do registro do E16 (16/11/2026), fixada antes de sortear.
SEED = 20261116
N = 50
RULE = (
    "ids do Ambig-IaC (ambig-000..ambig-299) ordenados, random.Random(f'{SEED}-e16').shuffle; "
    "o gate roda nessa ordem; selecionadas = as 50 primeiras aprovadas (gravadas ordenadas); "
    "substitutos = as aprovadas seguintes, na ordem."
)


def draw_order(ids: list[str]) -> list[str]:
    order = sorted(ids)
    random.Random(f"{SEED}-e16").shuffle(order)
    return order


def select(order: list[str], rows: list[dict], n: int = N) -> dict:
    """Selecionadas (N primeiras aprovadas na ordem), substitutos, reprovadas e não avaliadas."""
    by_id = {r["task_id"]: r for r in rows}
    passed = [t for t in order if t in by_id and by_id[t]["gate_pass"]]
    return {
        "selected": sorted(passed[:n]),
        "substitutes": passed[n:],
        "gate_failed": [
            {"task_id": t, "reason": by_id[t]["reason"]}
            for t in order
            if t in by_id and not by_id[t]["gate_pass"]
        ],
        "not_gated": [t for t in order if t not in by_id],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prevalidation", type=Path, help="prevalidation.jsonl do gate")
    args = parser.parse_args()
    ids = [ambig_iac.task_id(r["id"]) for r in ambig_iac.load_rows(ROOT / "data")]
    order = draw_order(ids)
    output = {"seed": SEED, "rule": RULE, "dataset": ambig_iac.provenance(), "order": order}
    if args.prevalidation:
        lines = args.prevalidation.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
        output = {**output, "prevalidation": str(args.prevalidation), **select(order, rows)}
        print(f"Aprovadas no gate: {len(output['selected']) + len(output['substitutes'])}, "
              f"reprovadas: {len(output['gate_failed'])}, não avaliadas: "
              f"{len(output['not_gated'])}")  # fmt: skip
        print(f"Selecionadas ({len(output['selected'])}): {', '.join(output['selected'])}")
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"Gravado em {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
