"""Métricas do teste de modalidades, no mesmo vocabulário do A/B/C (iac_harness.metrics).

"Modalidade" ocupa o lugar de "condição"; a categoria (create/modify/delete/query) é o estrato.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from itertools import combinations
from typing import Any

from iac_harness.metrics import (
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    PriceTable,
    _cost,
    _rate,
    holm,
    mcnemar_exact,
    paired_bootstrap,
)
from iac_harness.modalities.spec import MODALITIES

Row = dict[str, Any]
Key = tuple[str, int]  # (task_id, rep): unidade de pareamento entre modalidades


def _success(e: Row) -> bool:
    return bool(e["success"])


def _first_attempt(e: Row) -> bool:
    # Equivalente ao pass@1 sem repair: sucesso já na primeira tentativa.
    return bool(e["success"]) and e["attempts"] == 1


def _per_task(rows: list[Row], value: Callable[[Row], float]) -> dict[str, float]:
    by_task: dict[str, list[float]] = defaultdict(list)
    for e in rows:
        by_task[e["task_id"]].append(value(e))
    return {t: statistics.fmean(v) for t, v in by_task.items()}


def _block(
    rows: list[Row],
    failed_exec: set[tuple[str, str, int]],
    unsupported: Counter[tuple[str, str, int]],
    local_exec: Counter[tuple[str, str, int]],
    difficulty: dict[str, int] | None,
    prices: PriceTable,
) -> dict[str, Any]:
    """Métricas de um conjunto de trajetórias (uma modalidade, global ou de uma categoria)."""
    n = len(rows)
    by_task: dict[str, list[bool]] = defaultdict(list)
    by_rep: dict[int, list[bool]] = defaultdict(list)
    by_difficulty: dict[int, list[bool]] = defaultdict(list)
    for e in rows:
        by_task[e["task_id"]].append(_success(e))
        by_rep[e["rep"]].append(_success(e))
        if difficulty and e["task_id"] in difficulty:
            by_difficulty[difficulty[e["task_id"]]].append(_success(e))
    task_rates = [sum(v) / len(v) for v in by_task.values()]
    rep_rates = [sum(v) / len(v) for v in by_rep.values()]
    unstable = sum(1 for v in by_task.values() if 0 < sum(v) < len(v))
    wins = [e["attempts"] for e in rows if _success(e)]
    keys = [(e["task_id"], e["modality"], e["rep"]) for e in rows]
    # repair: trajetórias com alguma tentativa que falhou a execução e que terminaram em sucesso
    repaired = [e for e, k in zip(rows, keys, strict=True) if k in failed_exec]
    tok = {f: sum(e[f"{f}_tokens"] for e in rows) for f in ("input", "output", "total")}
    return {
        "success": _rate(sum(map(_success, rows)), n),
        "state_pass": _rate(sum(bool(e["check_pass"]) for e in rows), n),
        "first_attempt_success": _rate(sum(map(_first_attempt, rows)), n),
        "success_per_rep": {str(r): _rate(sum(v), len(v)) for r, v in sorted(by_rep.items())},
        "mean_task_pass_rate": statistics.fmean(task_rates) if task_rates else None,
        "rep_stdev": statistics.stdev(rep_rates) if len(rep_rates) > 1 else None,
        "unstable_task_fraction": unstable / len(by_task) if by_task else None,
        "attempts_to_success": statistics.fmean(wins) if wins else None,
        "mean_attempts": statistics.fmean(e["attempts"] for e in rows) if rows else None,
        "by_difficulty": {str(d): _rate(sum(v), len(v)) for d, v in sorted(by_difficulty.items())},
        "repair_rate": _rate(sum(map(_success, repaired)), len(repaired)),
        "unsupported_attempts": sum(unsupported[k] for k in keys),
        # Terraform com provisioner local-exec (tentativas e trajetórias com alguma tentativa)
        "local_exec_attempts": sum(local_exec[k] for k in keys),
        "local_exec_trajectories": sum(1 for k in keys if local_exec[k]),
        "tokens": {
            **tok,
            **{f"mean_{f}": (v / n if n else None) for f, v in tok.items()},
        },
        # Mesma fórmula do A/B/C: os tokens da trajetória entram como os "de repair", sem parcela
        # compartilhada (cada modalidade gera do zero).
        "cost_usd": _cost(
            [
                {
                    "model_id": e["model_id"],
                    "repair_input_tokens": e["input_tokens"],
                    "repair_output_tokens": e["output_tokens"],
                }
                for e in rows
            ],
            prices,
            shared=False,
        ),
        "termination": dict(sorted(Counter(e["termination"] for e in rows).items())),
    }


def _comparisons(
    rows_by_mod: dict[str, list[Row]], n_boot: int, seed: int
) -> dict[str, list[dict[str, Any]]]:
    """Pares de modalidades pareados por (task_id, rep); Holm dentro de cada métrica do bloco."""
    pairs = list(combinations([m for m in MODALITIES if m in rows_by_mod], 2))
    out: dict[str, list[dict[str, Any]]] = {}
    for name, ok in (("success", _success), ("first_attempt_success", _first_attempt)):
        outcome = {m: {(e["task_id"], e["rep"]): ok(e) for e in r} for m, r in rows_by_mod.items()}
        per_task = {m: _per_task(r, lambda e, ok=ok: float(ok(e))) for m, r in rows_by_mod.items()}
        cmps: list[dict[str, Any]] = []
        for x, y in pairs:
            keys: list[Key] = sorted(set(outcome[x]) & set(outcome[y]))
            only_x = sum(1 for k in keys if outcome[x][k] and not outcome[y][k])
            only_y = sum(1 for k in keys if outcome[y][k] and not outcome[x][k])
            diff, lo, hi = paired_bootstrap(per_task[x], per_task[y], n_boot, seed)
            cmps.append(
                {
                    "pair": f"{x}x{y}",
                    "n_pairs": len(keys),
                    "only_first": only_x,
                    "only_second": only_y,
                    "mcnemar_p": mcnemar_exact(only_x, only_y),
                    "diff_mean": diff,
                    "diff_ci95": [lo, hi],
                }
            )
        for cmp, adj in zip(cmps, holm([c["mcnemar_p"] for c in cmps]), strict=True):
            cmp["holm_p"] = adj
        out[name] = cmps

    # tokens médios por tarefa: só a diferença e o IC (sem teste de hipótese)
    tokens = {m: _per_task(r, lambda e: e["total_tokens"]) for m, r in rows_by_mod.items()}
    out["total_tokens"] = []
    for x, y in pairs:
        diff, lo, hi = paired_bootstrap(tokens[x], tokens[y], n_boot, seed)
        out["total_tokens"].append({"pair": f"{x}x{y}", "diff_mean": diff, "diff_ci95": [lo, hi]})
    return out


def compute_modality_metrics(
    events: list[dict[str, Any]],
    prices: PriceTable | None = None,
    n_boot: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
    difficulty: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Por modalidade (global e por categoria) e comparações pareadas entre modalidades.

    `success` = execução ok + estado certo (estrita); `state_pass` = só o estado certo (o check),
    comparável com os benchmarks que julgam pelo estado final. `difficulty` (task_id → nível) é
    opcional: tarefas fora do mapa ficam fora de `by_difficulty`.
    """
    prices = prices or {}
    finals = [e for e in events if e.get("event") == "final"]
    attempts = [e for e in events if e.get("event") == "attempt"]
    failed_exec = {(e["task_id"], e["modality"], e["rep"]) for e in attempts if not e["exec_ok"]}
    unsupported = Counter(
        (e["task_id"], e["modality"], e["rep"]) for e in attempts if e.get("unsupported")
    )
    local_exec = Counter(
        (e["task_id"], e["modality"], e["rep"]) for e in attempts if e.get("uses_local_exec")
    )
    categories = sorted({e["category"] for e in finals})
    modalities = [m for m in MODALITIES if any(e["modality"] == m for e in finals)]
    modalities += sorted({e["modality"] for e in finals} - set(modalities))

    def rows(mod: str, cat: str | None = None) -> list[Row]:
        return [e for e in finals if e["modality"] == mod and cat in (None, e["category"])]

    def block(r: list[Row]) -> dict[str, Any]:
        return _block(r, failed_exec, unsupported, local_exec, difficulty, prices)

    return {
        "n_trajectories": len(finals),
        "n_tasks": len({e["task_id"] for e in finals}),
        "modalities": {
            m: {
                "overall": block(rows(m)),
                "by_category": {c: block(r) for c in categories if (r := rows(m, c))},
            }
            for m in modalities
        },
        "comparisons": {
            "overall": _comparisons({m: rows(m) for m in modalities}, n_boot, seed),
            "by_category": {
                c: _comparisons({m: r for m in modalities if (r := rows(m, c))}, n_boot, seed)
                for c in categories
            },
        },
    }
