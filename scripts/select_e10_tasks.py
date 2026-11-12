"""Sorteio fixo das 10 tarefas do E10 (pedido leigo), registro metodológico, decisão E10.

Critério escrito no registro antes do sorteio: tarefas do E3 no Floci (`configs/e3-agent.toml`)
com `intent = "clear"` em `configs/strata.toml`, fora a task-118. Determinístico: rodar de novo
gera o mesmo `configs/e10-selection.json`.

Uso: uv run python scripts/select_e10_tasks.py
"""

import json
import random
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
E3_CONFIG = ROOT / "configs" / "e3-agent.toml"
STRATA = ROOT / "configs" / "strata.toml"
OUTPUT_PATH = ROOT / "configs" / "e10-selection.json"

# Data do registro do E10 (11/11/2026), fixada antes de sortear.
SEED = 20261111
N = 10
EXCLUDED = ["task-118"]


def main() -> None:
    tasks = tomllib.loads(E3_CONFIG.read_text())["modality"]["tasks"]
    strata = tomllib.loads(STRATA.read_text())["tasks"]
    # `intent` é o estrato do Floci; `intent_aws` só vale na real.
    pool = sorted(t for t in tasks if strata[t]["intent"] == "clear" and t not in EXCLUDED)
    ids = list(pool)
    random.Random(f"{SEED}-e10").shuffle(ids)
    selected, queue = sorted(ids[:N]), ids[N:]

    print(f"Pool: {len(pool)} tarefas clear do E3 (sem {', '.join(EXCLUDED)})")
    for t in selected:
        print(f"  {t:<22} {strata[t]['category']:<7} {strata[t]['origin']}")
    print(f"Fila de substitutos: {', '.join(queue)}")

    output = {
        "seed": SEED,
        "rule": (
            "Tarefas de configs/e3-agent.toml com intent = clear em configs/strata.toml, menos "
            "task-118; ids ordenados, random.Random(f'{SEED}-e10').shuffle, as 10 primeiras; "
            "o resto é a fila de substitutos, na ordem."
        ),
        "pool": pool,
        "selected": selected,
        "substitutes": queue,
    }
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"Gravado em {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
