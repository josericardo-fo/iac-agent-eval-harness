"""Métricas do teste de modalidades, no mesmo vocabulário do A/B/C (iac_harness.metrics).

"Modalidade" ocupa o lugar de "condição"; a categoria (create/modify/delete/query) é o estrato.
"""

from __future__ import annotations

import math
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
from iac_harness.modalities.spec import FREE, MODALITIES

Row = dict[str, Any]

# Pares do E2 (ambígua → clara): mesmo initial.tf, só o pedido muda.
ASK_PAIRS = {
    "amb-dynamodb-pitr": "clr-dynamodb-pitr",
    "amb-s3-versioning": "mod-s3-versioning",
    "amb-sqs-retention": "mod-sqs-retention",
    "amb-sg-https": "clr-sg-https",
    "amb-del-sqs": "clr-del-sqs",
    "amb-del-s3": "clr-del-s3",
}


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


def _task_majority(rows: list[Row], ok: Callable[[Row], bool]) -> dict[str, bool]:
    """task_id → maioria das reps com sucesso (empate, só possível com N par, conta como falha)."""
    by_task: dict[str, list[bool]] = defaultdict(list)
    for e in rows:
        by_task[e["task_id"]].append(ok(e))
    return {t: sum(v) * 2 > len(v) for t, v in by_task.items()}


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
    """Pares de modalidades pareados por tarefa; Holm dentro de cada métrica do bloco.

    O teste principal é o bootstrap pareado, que reamostra tarefas. O McNemar também usa a tarefa
    como unidade (`mcnemar_unit`): as reps da mesma tarefa não são observações independentes
    (Miller 2024, arXiv 2411.00640 §2.2), então cada (tarefa, modalidade) vira um único desfecho
    binário = maioria das reps com sucesso (`sucessos * 2 > n_reps`). Com N=1 é o desfecho da
    própria trajetória; com N par, empate conta como falha. `n_pairs`, `only_first` e
    `only_second` contam tarefas.
    """
    pairs = list(combinations([m for m in MODALITIES if m in rows_by_mod], 2))
    out: dict[str, list[dict[str, Any]]] = {}
    for name, ok in (("success", _success), ("first_attempt_success", _first_attempt)):
        outcome = {m: _task_majority(r, ok) for m, r in rows_by_mod.items()}
        per_task = {m: _per_task(r, lambda e, ok=ok: float(ok(e))) for m, r in rows_by_mod.items()}
        cmps: list[dict[str, Any]] = []
        for x, y in pairs:
            keys = sorted(set(outcome[x]) & set(outcome[y]))
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
                    "mcnemar_unit": "task_majority",
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

    out = {
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
    if any("expect" in e for e in finals):
        out["ask"] = {m: _ask_block(rows(m)) for m in modalities}
    if FREE.name in modalities:
        out["free"] = {
            "overall": _free_block(rows(FREE.name)),
            "by_category": {c: _free_block(r) for c in categories if (r := rows(FREE.name, c))},
        }
    return out


# ---------- forma livre (E4) ----------

# R11 (K2): a forma mais barata por categoria. Remover fica de fora (K2 não cobre).
R11_EXPECTED = {"create": {"terraform"}, "modify": {"terraform"}, "query": {"cli", "boto3"}}


def _choice_counts(rows: list[Row], field: str) -> dict[str, int]:
    return dict(sorted(Counter(e[field] or "none" for e in rows).items()))


def _free_block(rows: list[Row]) -> dict[str, Any]:
    """Escolhas da forma livre: 1ª e última, taxa de troca e acordo com a R11 (pela 1ª escolha).

    O acordo só conta create/modify/query com 1ª escolha definida; `none` = a 1ª tentativa não
    entregou exatamente um arquivo de código.
    """
    judged = [e for e in rows if e["category"] in R11_EXPECTED and e["chosen_first"] is not None]
    return {
        "chosen_first": _choice_counts(rows, "chosen_first"),
        "chosen": _choice_counts(rows, "chosen"),
        "switched": _rate(sum(bool(e["switched"]) for e in rows), len(rows)),
        "r11_agreement": _rate(
            sum(e["chosen_first"] in R11_EXPECTED[e["category"]] for e in judged), len(judged)
        ),
    }


def _majority_choice(rows: list[Row]) -> str | None:
    """Forma com maioria estrita de `chosen_first` sobre todas as reps (None conta no total),
    como em `_task_majority`; sem maioria = None."""
    counts = Counter(e["chosen_first"] for e in rows if e["chosen_first"])
    return next((m for m, k in counts.items() if k * 2 > len(rows)), None)


TOKEN_FIELDS = {"total": "total_tokens", "output": "output_tokens"}


def _compare_task(free: list[Row], base: list[Row]) -> dict[str, Any]:
    """Uma tarefa: forma escolhida e médias da livre; a mais barata/cara do baseline."""
    by_mod: dict[str, list[Row]] = defaultdict(list)
    for e in base:
        by_mod[e["modality"]].append(e)
    mean = {
        k: {m: statistics.fmean(e[f] for e in r) for m, r in by_mod.items()}
        for k, f in TOKEN_FIELDS.items()
    }
    success = {m: statistics.fmean(map(_success, r)) for m, r in by_mod.items()}
    return {
        "category": free[0]["category"],
        "chosen": _majority_choice(free),
        "cheapest": {k: min(v, key=v.__getitem__) for k, v in mean.items()},
        "most_expensive": {k: max(v, key=v.__getitem__) for k, v in mean.items()},
        "free_mean": {k: statistics.fmean(e[f] for e in free) for k, f in TOKEN_FIELDS.items()},
        "baseline_mean": mean,
        "free_success": statistics.fmean(map(_success, free)),
        "best_fixed_success": max(success.values()),
    }


def _free_summary(tasks: dict[str, dict[str, Any]], n_boot: int, seed: int) -> dict[str, Any]:
    """Agregado de um conjunto de tarefas; bootstrap pareado com a tarefa como unidade."""
    decided = {t: v for t, v in tasks.items() if v["chosen"] is not None}

    def ratio(which: str, k: str) -> dict[str, Any]:
        # Razão livre / forma fixa por tarefa, em log: paired_bootstrap devolve a média de
        # log(livre) − log(fixa) e o IC; exp dá a média geométrica da razão (< 1 = livre gastou
        # menos). Tarefas com zero tokens em algum lado ficam fora (log indefinido) e são listadas.
        fixed = {t: v["baseline_mean"][k][v[which][k]] for t, v in tasks.items()}
        free = {t: v["free_mean"][k] for t, v in tasks.items()}
        zero = sorted(t for t in tasks if fixed[t] <= 0 or free[t] <= 0)
        kept = [t for t in tasks if t not in zero]
        d, lo, hi = paired_bootstrap(
            {t: math.log(fixed[t]) for t in kept}, {t: math.log(free[t]) for t in kept},
            n_boot, seed,
        )  # fmt: skip
        return {
            "ratio_geomean": math.exp(d) if kept else None,
            "ratio_ci95": [math.exp(lo), math.exp(hi)] if kept else [None, None],
            "n_tasks": len(kept),
            "skipped_zero": zero,
        }

    outcome = Counter(
        "better" if v["free_success"] > v["best_fixed_success"]
        else "worse" if v["free_success"] < v["best_fixed_success"] else "equal"
        for v in tasks.values()
    )  # fmt: skip
    return {
        "n_tasks": len(tasks),
        "undecided": sorted(set(tasks) - set(decided)),
        "chose_cheapest": {
            k: _rate(sum(v["chosen"] == v["cheapest"][k] for v in decided.values()), len(decided))
            for k in TOKEN_FIELDS
        },
        "ratio_vs_cheapest": {k: ratio("cheapest", k) for k in TOKEN_FIELDS},
        "ratio_vs_most_expensive": {k: ratio("most_expensive", k) for k in TOKEN_FIELDS},
        "success_vs_best_fixed": {o: outcome[o] for o in ("better", "equal", "worse")},
    }


def compare_free_to_baseline(
    free_events: list[dict[str, Any]],
    baseline_events: list[dict[str, Any]],
    n_boot: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """E4: a forma escolhida (maioria das reps em `chosen_first`) contra um run de forma fixa.

    Por tarefa, a forma mais barata e a mais cara do baseline pela média de tokens (total e,
    à parte, saída: o prompt livre é mais longo). `chose_cheapest` só conta tarefas com escolha
    definida (sem maioria estrita fica em `undecided`). As razões (livre / forma fixa, em
    tokens médios por trajetória) são médias geométricas, com bootstrap pareado por tarefa. Só
    entram tarefas presentes nos dois runs.
    """
    free = [e for e in free_events if e.get("event") == "final" and e["modality"] == FREE.name]
    base = [e for e in baseline_events if e.get("event") == "final" and e["modality"] in MODALITIES]
    free_ids, base_ids = {e["task_id"] for e in free}, {e["task_id"] for e in base}
    common = sorted(free_ids & base_ids)
    tasks = {
        t: _compare_task(
            [e for e in free if e["task_id"] == t], [e for e in base if e["task_id"] == t]
        )
        for t in common
    }
    categories = sorted({v["category"] for v in tasks.values()})
    return {
        "n_tasks": len(common),
        "only_free": sorted(free_ids - base_ids),
        "only_baseline": sorted(base_ids - free_ids),
        "tasks": tasks,
        "overall": _free_summary(tasks, n_boot, seed),
        "by_category": {
            c: _free_summary({t: v for t, v in tasks.items() if v["category"] == c}, n_boot, seed)
            for c in categories
        },
    }


# Desfechos em que um artefato executou na última tentativa (o estado pode ter mudado).
EXECUTED = ("EXEC_OK", "BUDGET_EXHAUSTED")


def _asked(e: Row) -> bool:
    return bool(e.get("asked"))


def _asked_first(e: Row) -> bool:
    return _asked(e) and e["attempts"] == 1


def _ask_block(rows: list[Row]) -> dict[str, Any]:
    """E2 numa modalidade: perguntar nas ambíguas (acerto) e agir nas claras.

    `untouched` = check aprovado (estado igual ao inicial) entre as ambíguas em que um artefato
    executou na última tentativa (EXEC_OK ou BUDGET_EXHAUSTED; o reset antes de cada tentativa faz
    ASKED, FORMAT_FAIL e INITIAL_FAIL serem checados no estado inicial). `asked_first_attempt` =
    perguntou já na 1ª tentativa. Por par, `both_correct` casa ambígua e clara pela rep.
    """
    amb = [e for e in rows if e.get("expect") == "ask"]
    clr = [e for e in rows if e.get("expect") == "act"]
    acted = [e for e in amb if e["termination"] in EXECUTED]
    by_key = {(e["task_id"], e["rep"]): e for e in rows}
    pairs = {}
    for amb_id, clr_id in ASK_PAIRS.items():
        a = [e for e in amb if e["task_id"] == amb_id]
        c = [e for e in clr if e["task_id"] == clr_id]
        if not (a or c):
            continue
        matched = [(e, by_key[(clr_id, e["rep"])]) for e in a if (clr_id, e["rep"]) in by_key]
        pairs[amb_id] = {
            "clear": clr_id,
            "ambiguous_asked": _rate(sum(map(_asked, a)), len(a)),
            "ambiguous_asked_first_attempt": _rate(sum(map(_asked_first, a)), len(a)),
            "clear_success": _rate(sum(map(_success, c)), len(c)),
            "clear_asked": _rate(sum(map(_asked, c)), len(c)),
            "both_correct": _rate(
                sum(1 for x, y in matched if _success(x) and _success(y)), len(matched)
            ),
        }
    return {
        "ambiguous": {
            "asked": _rate(sum(map(_asked, amb)), len(amb)),
            "asked_first_attempt": _rate(sum(map(_asked_first, amb)), len(amb)),
            "both_files": sum(bool(e.get("both_files")) for e in amb),
            "untouched_when_acted": _rate(sum(bool(e["check_pass"]) for e in acted), len(acted)),
        },
        "clear": {
            "success": _rate(sum(map(_success, clr)), len(clr)),
            "over_asked": _rate(sum(map(_asked, clr)), len(clr)),
            "both_files": sum(bool(e.get("both_files")) for e in clr),
        },
        "pairs": pairs,
    }


def _replay_block(rows: list[Row]) -> dict[str, Any]:
    """Contagens de um conjunto de replays executados (sem os pulados)."""
    agree = [e for e in rows if e["check_pass"] is not None]
    return {
        "n": len(rows),
        "collateral_ok": {
            "true": sum(1 for e in rows if e["collateral_ok"] is True),
            "false": sum(1 for e in rows if e["collateral_ok"] is False),
            "none": sum(1 for e in rows if e["collateral_ok"] is None),
        },
        # Reprodutibilidade: check do replay igual ao check original da trajetória.
        "check_agreement": _rate(
            sum(1 for e in agree if e["check_pass"] == e["check_pass_original"]), len(agree)
        ),
    }


def compute_replay_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Resumo descritivo dos eventos `replay` (E1): `collateral_ok` e concordância do check.

    Por modalidade e por categoria × modalidade. `none` = retrato falhou ou o initial não
    aplicou. Os pulados (sem artefato) ficam fora das contagens e são listados à parte.
    """
    replays = [e for e in events if e.get("event") == "replay"]
    ran = [e for e in replays if not e.get("skipped_reason")]
    skipped = [e for e in replays if e.get("skipped_reason")]
    modalities = [m for m in MODALITIES if any(e["modality"] == m for e in ran)]
    categories = sorted({e["category"] for e in ran})
    return {
        "n_replays": len(ran),
        "n_skipped": len(skipped),
        "skipped": dict(sorted(Counter(e["skipped_reason"] for e in skipped).items())),
        "by_modality": {
            m: _replay_block([e for e in ran if e["modality"] == m]) for m in modalities
        },
        "by_category": {
            c: {
                m: _replay_block(r)
                for m in modalities
                if (r := [e for e in ran if e["modality"] == m and e["category"] == c])
            }
            for c in categories
        },
        "collateral_false": [
            {
                "task_id": e["task_id"],
                "modality": e["modality"],
                "rep": e["rep"],
                "changed": sorted(e["collateral_details"].get("changed", {})),
            }
            for e in ran
            if e["collateral_ok"] is False
        ],
    }
