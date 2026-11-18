"""Métricas recalculadas a partir do JSONL (sem ler logs textuais). Estatística só com stdlib."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

# Preço em US$ por milhão de tokens: {model_id: (entrada, saída)}.
PriceTable = dict[str, tuple[float, float]]

BOOTSTRAP_SEED = 0
BOOTSTRAP_ITERATIONS = 2000


# ---------- estatística ----------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson para uma proporção."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def mcnemar_exact(b: int, c: int) -> float:
    """p-valor bicaudal do McNemar exato (binomial sobre os pares discordantes)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_bootstrap(
    a: dict[str, float],
    b: dict[str, float],
    n_boot: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float, float]:
    """Diferença média (b - a) e IC 95%, reamostrando tarefas com seed fixo."""
    tasks = sorted(set(a) & set(b))
    if not tasks:
        return (0.0, 0.0, 0.0)
    diffs = [b[t] - a[t] for t in tasks]
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(n_boot))
    lo = means[int(0.025 * (n_boot - 1))]
    hi = means[int(math.ceil(0.975 * (n_boot - 1)))]
    return (sum(diffs) / len(diffs), lo, hi)


def holm(pvalues: list[float]) -> list[float]:
    """Ajuste de Holm-Bonferroni, na ordem original."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


# ---------- leitura ----------


def load_events(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "wilson": [lo, hi]}


def _tokens(e: dict[str, Any], *, shared: bool = True) -> tuple[int, int]:
    """Tokens (entrada, saída) de um FinalRecord: repair da condição (+ tentativa 1 se shared)."""
    t_in, t_out = e["repair_input_tokens"], e["repair_output_tokens"]
    if shared:
        t_in += e["shared_input_tokens"]
        t_out += e["shared_output_tokens"]
    return t_in, t_out


def _cost(rows: list[dict[str, Any]], prices: PriceTable, *, shared: bool = True) -> float:
    total = 0.0
    for e in rows:
        price = prices.get(e["model_id"])
        if price:
            t_in, t_out = _tokens(e, shared=shared)
            total += (t_in * price[0] + t_out * price[1]) / 1_000_000
    return total
