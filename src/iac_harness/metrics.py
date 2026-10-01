"""Métricas recalculadas a partir do JSONL (sem ler logs textuais). Estatística só com stdlib."""

from __future__ import annotations

import json
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from typing import Any

from iac_harness.conditions import B
from iac_harness.types import CheckStatus, FinalStatus, FirstFailure, Stage

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


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """Estimador sem viés de pass@k (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). `None` se n < k."""
    if n < k:
        return None
    return 1 - math.comb(n - c, k) / math.comb(n, k)


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


# ---------- métricas ----------


def _population(
    rows_by_cond: dict[str, list[dict[str, Any]]],
    ok: Callable[[dict[str, Any]], bool],
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """Bloco de métricas de uma população: taxas por condição, baseline (A) e comparações."""
    conds = sorted(rows_by_cond)
    base = rows_by_cond.get("A", [])
    base_by_difficulty: dict[int, list[bool]] = defaultdict(list)
    for e in base:
        base_by_difficulty[e["difficulty"]].append(ok(e))
    n_tasks = len({e["task_id"] for rows in rows_by_cond.values() for e in rows})
    out: dict[str, Any] = {
        "n_tasks": n_tasks,
        # A final de A é a própria tentativa 1 compartilhada (sem repair): a baseline vem dela.
        "baseline_pass_at_1": _rate(sum(map(ok, base)), len(base)) if base else None,
        "baseline_pass_at_1_by_difficulty": {
            str(d): _rate(sum(v), len(v)) for d, v in sorted(base_by_difficulty.items())
        },
        "conditions": {},
    }

    # (task, rep) -> sucesso, por condição, para os testes pareados
    outcome: dict[str, dict[tuple[str, int], bool]] = {}
    per_task: dict[str, dict[str, float]] = {}
    for cond in conds:
        rows = rows_by_cond[cond]
        outcome[cond] = {(e["task_id"], e["rep"]): ok(e) for e in rows}
        by_task: dict[str, list[bool]] = defaultdict(list)
        by_rep: dict[int, list[bool]] = defaultdict(list)
        by_difficulty: dict[int, list[bool]] = defaultdict(list)
        for e in rows:
            by_task[e["task_id"]].append(ok(e))
            by_rep[e["rep"]].append(ok(e))
            by_difficulty[e["difficulty"]].append(ok(e))
        per_task[cond] = {t: sum(v) / len(v) for t, v in by_task.items()}
        rep_rates = [sum(v) / len(v) for v in by_rep.values()]
        unstable = sum(1 for v in by_task.values() if 0 < sum(v) < len(v))
        wins = [e["attempts"] for e in rows if ok(e)]
        out["conditions"][cond] = {
            "success": _rate(sum(map(ok, rows)), len(rows)),
            "success_per_rep": {str(r): _rate(sum(v), len(v)) for r, v in sorted(by_rep.items())},
            "mean_task_pass_rate": statistics.fmean(per_task[cond].values())
            if per_task[cond]
            else None,
            "rep_stdev": statistics.stdev(rep_rates) if len(rep_rates) > 1 else None,
            "unstable_task_fraction": unstable / len(by_task) if by_task else None,
            "attempts_to_success": statistics.fmean(wins) if wins else None,
            "by_difficulty": {
                str(d): _rate(sum(v), len(v)) for d, v in sorted(by_difficulty.items())
            },
        }

    # Controle "mais tentativas, sem feedback": pass@k das tentativas 1 independentes (k = orçamento
    # de B). Secundário e exploratório: fora do Holm e sem McNemar (pass@k não é binário por par).
    k = B.max_attempts
    base_by_task: dict[str, list[bool]] = defaultdict(list)
    for e in base:
        base_by_task[e["task_id"]].append(ok(e))
    task_difficulty = {e["task_id"]: e["difficulty"] for e in base}
    task_pass_k = {
        t: p for t, v in base_by_task.items() if (p := pass_at_k(len(v), sum(v), k)) is not None
    }
    out["baseline_pass_at_k"] = None
    out["pass_at_k_comparisons"] = None
    if task_pass_k:
        pk_by_difficulty: dict[int, list[float]] = defaultdict(list)
        for t, p in task_pass_k.items():
            pk_by_difficulty[task_difficulty[t]].append(p)
        out["baseline_pass_at_k"] = {
            "k": k,
            "mean": statistics.fmean(task_pass_k.values()),
            "by_difficulty": {
                str(d): statistics.fmean(v) for d, v in sorted(pk_by_difficulty.items())
            },
            "n_tasks": len(task_pass_k),
        }
        pk_comparisons = []
        for cond in (c for c in ("B", "C") if c in per_task):
            diff, lo, hi = paired_bootstrap(task_pass_k, per_task[cond], n_boot, seed)
            pk_comparisons.append(
                {"pair": f"pass@{k}x{cond}", "diff_mean": diff, "diff_ci95": [lo, hi]}
            )
        out["pass_at_k_comparisons"] = pk_comparisons

    # comparações pareadas com Holm (dentro da população)
    comparisons: list[dict[str, Any]] = []
    for x, y in combinations([c for c in ("A", "B", "C") if c in conds], 2):
        keys = sorted(set(outcome[x]) & set(outcome[y]))
        only_x = sum(1 for k in keys if outcome[x][k] and not outcome[y][k])
        only_y = sum(1 for k in keys if outcome[y][k] and not outcome[x][k])
        diff, lo, hi = paired_bootstrap(per_task[x], per_task[y], n_boot, seed)
        comparisons.append(
            {
                "pair": f"{x}x{y}",
                "only_first": only_x,
                "only_second": only_y,
                "mcnemar_p": mcnemar_exact(only_x, only_y),
                "diff_mean": diff,
                "diff_ci95": [lo, hi],
            }
        )
    for cmp, adj in zip(comparisons, holm([c["mcnemar_p"] for c in comparisons]), strict=True):
        cmp["holm_p"] = adj
    out["comparisons"] = comparisons
    return out


def compute_metrics(
    events: list[dict[str, Any]],
    prices: PriceTable | None = None,
    n_boot: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    prices = prices or {}
    all_finals = [e for e in events if e["event"] == "final"]
    attempts = [e for e in events if e["event"] == "attempt"]
    excluded = (FinalStatus.HARNESS_INVALID, FinalStatus.HARNESS_ERROR)
    invalid = sorted(
        {e["task_id"] for e in all_finals if e["final_status"] == FinalStatus.HARNESS_INVALID}
    )
    harness_errors = [e for e in all_finals if e["final_status"] == FinalStatus.HARNESS_ERROR]
    finals = [e for e in all_finals if e["final_status"] not in excluded]
    conds = sorted({e["condition"] for e in finals})

    def of(cond: str) -> list[dict[str, Any]]:
        return [e for e in finals if e["condition"] == cond]

    ok = lambda e: e["final_status"] == FinalStatus.PASS  # noqa: E731
    pre_stages = (Stage.VALIDATE, Stage.PLAN, Stage.OPA)
    predeploy_ok = lambda e: all(  # noqa: E731
        e["statuses"].get(s) == CheckStatus.PASS for s in pre_stages
    )
    out: dict[str, Any] = {
        "harness_invalid_tasks": invalid,
        "harness_error": {
            "count": len(harness_errors),
            "by_condition": dict(Counter(e["condition"] for e in harness_errors)),
        },
        "populations": {
            # pré-deploy: todas as não excluídas; comparável à literatura
            "predeploy": _population({c: of(c) for c in conds}, predeploy_ok, n_boot, seed),
            # ponta a ponta: só onde o emulador é instrumento confiável (referência roda no Floci)
            "end_to_end": _population(
                {c: [e for e in of(c) if e["sandbox_supported"]] for c in conds}, ok, n_boot, seed
            ),
        },
        "conditions": {},
    }

    for cond in conds:
        rows = of(cond)
        # métricas de sandbox: só no denominador sandbox_supported
        supported = [e for e in rows if e["sandbox_supported"]]
        reached = [e for e in supported if predeploy_ok(e)]
        sandbox_fail = sum(
            1 for e in reached if e["statuses"].get(Stage.SANDBOX) == CheckStatus.FAIL
        )
        out["conditions"][cond] = {
            "first_failure_stage": dict(Counter(e["first_failure_stage"] for e in rows)),
            "sandbox_only_detection": _rate(sandbox_fail, len(reached)),
            # Custo marginal da condição: tentativa 1 compartilhada + repair da própria condição.
            "tokens": {
                "input": sum(_tokens(e)[0] for e in rows),
                "output": sum(_tokens(e)[1] for e in rows),
                "total": sum(sum(_tokens(e)) for e in rows),
            },
            "cost_usd": _cost(rows, prices),
        }

    # Custo total do experimento: a tentativa 1 compartilhada conta uma vez por (task, rep).
    shared_once: dict[tuple[str, int], dict[str, Any]] = {}
    for e in finals:
        shared_once.setdefault((e["task_id"], e["rep"]), e)
    repair_only = _cost(finals, prices, shared=False)
    shared_cost = _cost(
        [{**e, "repair_input_tokens": 0, "repair_output_tokens": 0} for e in shared_once.values()],
        prices,
    )
    out["total_cost_usd"] = repair_only + shared_cost

    # sandbox repair rate (C): falhas de sandbox (FAIL, não UNSUPPORTED) em alguma tentativa
    # que terminaram em PASS
    if "C" in conds:
        failed_keys = {
            (e["task_id"], e["rep"])
            for e in attempts
            if e["condition"] == "C"
            and e["first_failure_stage"] == FirstFailure.SANDBOX_FAIL
            and e.get("first_failure_status") == CheckStatus.FAIL
        }
        c_rows = {(e["task_id"], e["rep"]): e for e in of("C") if e["sandbox_supported"]}
        relevant = [c_rows[k] for k in failed_keys if k in c_rows]
        out["sandbox_repair_rate"] = _rate(sum(map(ok, relevant)), len(relevant))
    return out
