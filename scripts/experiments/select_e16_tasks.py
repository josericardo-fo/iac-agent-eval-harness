"""Sorteio fixo das 50 tarefas do E16 (Ambig-IaC), registro metodológico, linha E16.

Critério escrito no registro antes do sorteio: 50 tarefas sorteadas com semente fixa entre as que
passam no gate (a referência `main_tf` passa no oráculo e um `main.tf` vazio reprova).

Como o gate custa containers, ele roda na ordem do sorteio até juntar as aprovadas: a ordem é uma
permutação semeada dos 300 ids, e as selecionadas são as N primeiras aprovadas nessa ordem
(equivale a sortear N entre as aprovadas). As aprovadas seguintes são a fila de substitutos.

Uso (determinístico: rodar de novo gera o mesmo arquivo):
  1. uv run python scripts/experiments/select_e16_tasks.py
     grava `configs/e16-selection.json` só com a ordem;
  2. uv run iac-harness ambig-prevalidate --config configs/e16-ambig-iac.toml \\
         --order configs/e16-selection.json --target-pass 60
  3. uv run python scripts/experiments/select_e16_tasks.py \\
         --prevalidation runs/<run>/prevalidation.jsonl
     completa selecionadas, substitutos e reprovadas no gate e grava o bloco `amendment`
     (emenda de 16/11: tarefas impossíveis saem, substitutos entram). Com `--amended-gate
     runs/<run>/prevalidation.jsonl` (gate das 50 emendadas), grava também as regras
     discriminantes de cada tarefa (as que reprovam no main.tf vazio), base do `rego_frac`.
"""

import argparse
import json
import random
import re
from pathlib import Path

from iac_harness.datasets import ambig_iac

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = ROOT / "configs" / "e16-selection.json"

# Data do registro do E16 (16/11/2026), fixada antes de sortear.
SEED = 20261116
N = 50
RULE = (
    "ids do Ambig-IaC (ambig-000..ambig-299) ordenados, random.Random(f'{SEED}-e16').shuffle; "
    "o gate roda nessa ordem; selecionadas = as 50 primeiras aprovadas (gravadas ordenadas); "
    "substitutos = as aprovadas seguintes, na ordem."
)
AMENDMENT_RULE = (
    "sai a tarefa cujo `checks_rego` cita um recurso `aws_x.nome` com `nome` ausente do `intent` "
    "(nenhuma pergunta recupera o nome; `id`, `arn` e `name` não contam); entram os primeiros "
    "substitutos da fila que passam na mesma regra; finais gravadas ordenadas."
)
_RESOURCE_REF = re.compile(r'"(aws_[a-z0-9_]+)\.([A-Za-z0-9_-]+)')
_GENERIC_ATTRIBUTES = ("id", "arn", "name")


def unrecoverable_names(rego: str, intent: str) -> list[str]:
    """Referências `aws_x.nome` do Rego cujo `nome` não aparece no `intent` (ordenadas)."""
    names = set(_RESOURCE_REF.findall(rego))
    return sorted(f"{a}.{b}" for a, b in names if b not in intent and b not in _GENERIC_ATTRIBUTES)


def amend(selected: list[str], substitutes: list[str], rows: dict[str, dict]) -> dict:
    """Bloco `amendment`: tira as impossíveis das selecionadas e repõe pela fila de substitutos."""

    def possible(task: str) -> bool:
        return not unrecoverable_names(rows[task]["checks_rego"], rows[task]["intent"])

    removed = [t for t in selected if not possible(t)]
    added = [t for t in substitutes if possible(t)][: len(removed)]
    final = sorted([t for t in selected if t not in removed] + added)
    return {"rule": AMENDMENT_RULE, "removed": removed, "added": added, "selected_amended": final}


def discriminating_rules(selected: list[str], rows: list[dict]) -> dict[str, list[str]]:
    """Por tarefa, as regras (caminhos) que reprovam no passo `empty` do gate: as discriminantes."""
    by_id = {r["task_id"]: r for r in rows}
    out = {}
    for task in selected:
        rules = sorted(set(by_id[task]["empty"]["failed_rules"]))
        if not rules:
            raise SystemExit(f"erro: {task} sem regra reprovada no main.tf vazio (gate inválido)")
        out[task] = rules
    return out


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
    parser.add_argument(
        "--amended-gate", type=Path,
        help="prevalidation.jsonl do gate das 50 emendadas (passo `empty`: regras discriminantes)",
    )  # fmt: skip
    args = parser.parse_args()
    ids = [ambig_iac.task_id(r["id"]) for r in ambig_iac.load_rows(ROOT / "data")]
    order = draw_order(ids)
    output = {"seed": SEED, "rule": RULE, "dataset": ambig_iac.provenance(), "order": order}
    if args.prevalidation:
        lines = args.prevalidation.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
        output = {**output, "prevalidation": str(args.prevalidation), **select(order, rows)}
        dataset_rows = {ambig_iac.task_id(r["id"]): r for r in ambig_iac.load_rows(ROOT / "data")}
        output["amendment"] = amend(output["selected"], output["substitutes"], dataset_rows)
        if args.amended_gate:
            lines = args.amended_gate.read_text(encoding="utf-8").splitlines()
            gate = [json.loads(line) for line in lines if line.strip()]
            output["amendment"]["amended_gate"] = str(args.amended_gate)
            output["amendment"]["discriminating_rules"] = discriminating_rules(
                output["amendment"]["selected_amended"], gate
            )
        print(f"Aprovadas no gate: {len(output['selected']) + len(output['substitutes'])}, "
              f"reprovadas: {len(output['gate_failed'])}, não avaliadas: "
              f"{len(output['not_gated'])}")  # fmt: skip
        print(f"Selecionadas ({len(output['selected'])}): {', '.join(output['selected'])}")
        print(
            f"Emenda: saem {output['amendment']['removed']}, entram {output['amendment']['added']}"
        )
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"Gravado em {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
