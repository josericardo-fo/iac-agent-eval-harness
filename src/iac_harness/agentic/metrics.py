"""Métricas do E3 (agente real): por condição × modelo, estrato e alvo; condição × `none`.

Reaproveita o `_block` das modalidades (taxas por rep, instabilidade, Wilson) e os testes de
`iac_harness.metrics` (bootstrap pareado por tarefa, McNemar pela maioria das reps, Holm), sem
mudar o código nem a saída dos runs antigos.
"""

from __future__ import annotations

import random
import statistics
from collections import Counter
from typing import Any

from iac_harness.agentic.judge import KINDS
from iac_harness.agentic.outcome import success as success_rule
from iac_harness.agentic.outcome import success_final as success_final_rule
from iac_harness.agentic.runner import HARNESS_FAILURES
from iac_harness.agentic.runner import TOKEN_FIELDS as _EVENT_TOKEN_FIELDS
from iac_harness.agentic.strata import AXES
from iac_harness.metrics import (
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    _rate,
    holm,
    mcnemar_exact,
    paired_bootstrap,
)
from iac_harness.modalities.metrics import _block, _per_task, _task_majority

Row = dict[str, Any]
BASELINE_CONDITION = "none"
TOKEN_FIELDS = (*_EVENT_TOKEN_FIELDS, "total")
# Chaves do _block que valem para o agente (tentativas, repair e local-exec são das modalidades).
_BLOCK_KEYS = (
    "success", "success_per_rep", "mean_task_pass_rate", "rep_stdev", "unstable_task_fraction",
)  # fmt: skip


def merge_judgments(events: list[Row], judgments: list[Row]) -> list[Row]:
    """Eventos `final` com `asked`, `judge_kind` e `success` vindos do judge.jsonl.

    O último veredito de cada `trajectory_id` vale. Sem veredito, `asked`/`success` ficam None.
    """
    verdicts = {j["trajectory_id"]: j for j in judgments}
    out = []
    for e in events:
        if e.get("event") != "final":
            continue
        j = verdicts.get(e.get("trajectory_id"))
        asked = j.get("asked") if j else None
        row = {
            **e,
            "asked": asked,
            "judge_kind": j.get("kind") if j else None,
            "success": success_rule(e["expect"], e.get("check_pass"), asked),
        }
        if "check_pass_final" in e:  # trajetória com usuário simulado
            row["success_final"] = success_final_rule(e["check_pass_final"])
        out.append(row)
    return out


def _key(e: Row) -> str:
    return f"{e['condition']}|{e['model']}"


def _as_modality_row(e: Row) -> Row:
    """Adapta o evento do agente ao formato que o `_block` das modalidades lê."""
    return {
        **e, "modality": _key(e), "attempts": 1, "model_id": e["model"],
        "success": bool(e["success"]), "check_pass": bool(e["check_pass"]),
    }  # fmt: skip


def _optional_rate(rows: list[Row], field: str) -> dict[str, Any]:
    """Taxa sobre as trajetórias em que o campo não é None (colateral, verified_state, asked)."""
    known = [e[field] for e in rows if e.get(field) is not None]
    return _rate(sum(bool(v) for v in known), len(known))


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _user_sim_block(rows: list[Row]) -> dict[str, Any] | None:
    """Desfecho depois das respostas do usuário simulado; None se nenhuma trajetória o usou."""
    sim = [e for e in rows if "check_pass_final" in e]
    if not sim:
        return None
    return {
        "n": len(sim),
        "replied": _rate(sum(bool(e.get("user_turns")) for e in sim), len(sim)),
        "mean_user_turns": _mean([e.get("user_turns") or 0 for e in sim]),
        "state_pass_final": _rate(sum(bool(e["check_pass_final"]) for e in sim), len(sim)),
        "success_final": _optional_rate(sim, "success_final"),
        "errors": sum(1 for e in sim if e.get("user_sim_error")),
        "cost_usd_total": round(sum(float(e.get("cost_usd_total") or 0) for e in sim), 6),
    }


def agent_block(rows: list[Row]) -> dict[str, Any]:
    judged = [e for e in rows if e.get("success") is not None]
    base = _block([_as_modality_row(e) for e in judged], set(), Counter(), Counter(), None, {})
    tok = {f: sum(int(e.get(f"{f}_tokens") or 0) for e in rows) for f in TOKEN_FIELDS}
    tools: Counter[str] = Counter()
    for e in rows:
        tools.update(e.get("tool_counts") or {})
    out = {
        "n": len(rows),
        "n_judged": len(judged),
        **{k: base[k] for k in _BLOCK_KEYS},
        "state_pass": _rate(sum(bool(e.get("check_pass")) for e in rows), len(rows)),
        "asked": _optional_rate(rows, "asked"),
        "collateral_ok": _optional_rate(rows, "collateral_ok"),
        "verified_state": _optional_rate(rows, "verified_state"),
        "tokens": {**tok, "mean_total": tok["total"] / len(rows) if rows else None},
        "cost_usd": round(sum(float(e.get("cost_usd") or 0) for e in rows), 6),
        "mean_num_turns": _mean([e["num_turns"] for e in rows if e.get("num_turns") is not None]),
        "mean_duration_s": _mean([e["duration_s"] for e in rows if e.get("duration_s")]),
        "tool_calls": dict(sorted(tools.items())),
        "termination": dict(sorted(Counter(e.get("termination") for e in rows).items())),
    }
    sim = _user_sim_block(rows)
    if sim is not None:  # só nos runs com usuário simulado: a saída dos demais não muda
        out["user_sim"] = sim
    return out


def by_condition_model(rows: list[Row]) -> dict[str, dict[str, Any]]:
    keys = sorted({_key(e) for e in rows})
    return {k: agent_block([e for e in rows if _key(e) == k]) for k in keys}


def _strata_values(e: Row, axis: str) -> list[Any]:
    value = e.get(f"strata_{axis}")
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _vs_none(rows: list[Row], n_boot: int, seed: int) -> dict[str, list[dict[str, Any]]]:
    """Cada condição × `none`, pareado por tarefa (mesmo modelo). Holm dentro de cada métrica.

    McNemar com a tarefa como unidade (maioria das reps, como nas modalidades); bootstrap pareado
    da taxa por tarefa e dos tokens médios por tarefa (diferença = condição - none).
    """
    judged = [e for e in rows if e.get("success") is not None]
    by_cond = {
        c: [e for e in judged if e["condition"] == c] for c in {e["condition"] for e in rows}
    }
    base = by_cond.get(BASELINE_CONDITION, [])
    others = sorted(c for c in by_cond if c != BASELINE_CONDITION)
    out: dict[str, list[dict[str, Any]]] = {}
    for name, ok in (
        ("success", lambda e: bool(e["success"])),
        ("state_pass", lambda e: bool(e["check_pass"])),
    ):
        cmps = []
        for cond in others:
            x, y = _task_majority(base, ok), _task_majority(by_cond[cond], ok)
            keys = sorted(set(x) & set(y))
            only_x = sum(1 for k in keys if x[k] and not y[k])
            only_y = sum(1 for k in keys if y[k] and not x[k])
            diff, lo, hi = paired_bootstrap(
                _per_task(base, lambda e, ok=ok: float(ok(e))),
                _per_task(by_cond[cond], lambda e, ok=ok: float(ok(e))),
                n_boot, seed,
            )  # fmt: skip
            cmps.append(
                {
                    "pair": f"{BASELINE_CONDITION}x{cond}",
                    "n_pairs": len(keys),
                    "only_first": only_x,
                    "only_second": only_y,
                    "mcnemar_p": mcnemar_exact(only_x, only_y),
                    "mcnemar_unit": "task_majority",
                    "diff_mean": diff,
                    "diff_ci95": [lo, hi],
                }  # fmt: skip
            )
        for cmp, adj in zip(cmps, holm([c["mcnemar_p"] for c in cmps]), strict=True):
            cmp["holm_p"] = adj
        out[name] = cmps
    out["total_tokens"] = []
    for cond in others:
        diff, lo, hi = paired_bootstrap(
            _per_task(base, lambda e: e["total_tokens"]),
            _per_task(by_cond[cond], lambda e: e["total_tokens"]),
            n_boot, seed,
        )  # fmt: skip
        out["total_tokens"].append(
            {"pair": f"{BASELINE_CONDITION}x{cond}", "diff_mean": diff, "diff_ci95": [lo, hi]}
        )
    return out


def _success(e: Row) -> bool:
    return bool(e["success"])


def _by_condition(rows: list[Row]) -> tuple[list[Row], dict[str, list[Row]]]:
    """(trajetórias julgadas de `none`, {condição: julgadas}) das demais condições."""
    judged = [e for e in rows if e.get("success") is not None]
    others = sorted({e["condition"] for e in judged} - {BASELINE_CONDITION})
    base = [e for e in judged if e["condition"] == BASELINE_CONDITION]
    return base, {c: [e for e in judged if e["condition"] == c] for c in others}


def _per_task_vs_none(rows: list[Row]) -> dict[str, list[dict[str, Any]]]:
    """Por condição: cada tarefa com sucesso pela maioria das reps e tokens médios × `none`."""
    base, by_cond = _by_condition(rows)
    base_ok = _task_majority(base, _success)
    base_tok = _per_task(base, lambda e: e["total_tokens"])
    out: dict[str, list[dict[str, Any]]] = {}
    for cond, cond_rows in by_cond.items():
        ok = _task_majority(cond_rows, _success)
        tok = _per_task(cond_rows, lambda e: e["total_tokens"])
        out[cond] = [
            {
                "task_id": t,
                "none_success": base_ok[t],
                "cond_success": ok[t],
                "none_tokens": base_tok[t],
                "cond_tokens": tok[t],
                "tokens_ratio": tok[t] / base_tok[t] if base_tok[t] else None,
            }
            for t in sorted(set(ok) & set(base_ok))
        ]
    return out


def _easy_harm(rows: list[Row]) -> dict[str, dict[str, Any]]:
    """Piora nas tarefas fáceis (Nekrasov; SkillsBench): `none` acerta em todas as reps e a
    condição falha pela maioria."""
    base, by_cond = _by_condition(rows)
    easy = {e["task_id"] for e in base} - {e["task_id"] for e in base if not _success(e)}
    out: dict[str, dict[str, Any]] = {}
    for cond, cond_rows in by_cond.items():
        ok = _task_majority(cond_rows, _success)
        paired = sorted(easy & set(ok))
        harmed = [t for t in paired if not ok[t]]
        out[cond] = {
            "n_easy": len(paired),
            "harmed": _rate(len(harmed), len(paired)),
            "harmed_tasks": harmed,
        }
    return out


def compare_runs(first: list[Row], second: list[Row]) -> dict[str, Any]:
    """Concordância entre dois runs (ex.: Floci × AWS real) no sucesso pela maioria das reps.

    Unidade: (tarefa, condição, modelo) presente nos dois, sem falhas do harness; concordância
    simples (sem kappa) e a lista das discordantes.
    """

    def majority(rows: list[Row]) -> dict[tuple[str, str, str], bool]:
        judged = [e for e in rows if e.get("success") is not None
                  and e.get("termination") not in HARNESS_FAILURES]  # fmt: skip
        groups: dict[tuple[str, str, str], list[bool]] = {}
        for e in judged:
            groups.setdefault((e["task_id"], e["condition"], e["model"]), []).append(_success(e))
        return {k: sum(v) * 2 > len(v) for k, v in groups.items()}

    a, b = majority(first), majority(second)
    keys = sorted(set(a) & set(b))
    discordant = [
        {"task_id": t, "condition": c, "model": m, "first": a[(t, c, m)], "second": b[(t, c, m)]}
        for t, c, m in keys
        if a[(t, c, m)] != b[(t, c, m)]
    ]
    return {
        "n_pairs": len(keys),
        "only_first": len(set(a) - set(b)),
        "only_second": len(set(b) - set(a)),
        "agreement": _rate(len(keys) - len(discordant), len(keys)),
        "discordant": discordant,
    }


JUDGE_EXCERPT_CHARS = 500
JUDGE_SAMPLE_FIELDS = ("trajectory_id", "final_text_excerpt", "judge_kind", "human_kind")


def judge_sample(events: list[Row], judgments: list[Row], n: int, seed: int) -> list[Row]:
    """Amostra reprodutível de trajetórias julgadas para a leitura à mão (`human_kind` vazio)."""
    verdicts = {j["trajectory_id"]: j for j in judgments}
    pool = sorted(
        (e for e in events if e.get("event") == "final"
         and e.get("termination") not in HARNESS_FAILURES
         and (verdicts.get(e["trajectory_id"]) or {}).get("kind") is not None),
        key=lambda e: e["trajectory_id"],
    )  # fmt: skip
    picked = random.Random(seed).sample(pool, min(n, len(pool)))
    return [
        {
            "trajectory_id": e["trajectory_id"],
            "final_text_excerpt": (e.get("final_text") or "")[:JUDGE_EXCERPT_CHARS],
            "judge_kind": verdicts[e["trajectory_id"]]["kind"],
            "human_kind": "",
        }
        for e in picked
    ]


def judge_agreement(rows: list[Row]) -> dict[str, Any]:
    """Juiz × leitura à mão: concordância no `kind` e no `asked` (kind == "ask").

    Linhas com `human_kind` vazio ficam de fora; valor fora de KINDS levanta erro.
    """
    labeled = []
    for r in rows:
        human = (r.get("human_kind") or "").strip()
        if not human:
            continue
        if human not in KINDS:
            raise ValueError(f"human_kind inválido em {r['trajectory_id']}: {human!r}")
        labeled.append({**r, "human_kind": human})
    return {
        "kind": _rate(sum(r["judge_kind"] == r["human_kind"] for r in labeled), len(labeled)),
        "asked": _rate(
            sum((r["judge_kind"] == "ask") == (r["human_kind"] == "ask") for r in labeled),
            len(labeled),
        ),
        "unlabeled": len(rows) - len(labeled),
        "disagreements": [
            {k: r[k] for k in ("trajectory_id", "judge_kind", "human_kind")}
            for r in labeled
            if r["judge_kind"] != r["human_kind"]
        ],
    }


def _kappa(pairs: list[tuple[Any, Any]]) -> float | None:
    """Kappa de Cohen entre dois rotuladores; None sem pares ou com pe == 1."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(first[k] * second[k] for k in first) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def judge_pair_agreement(events: list[Row], a: list[Row], b: list[Row]) -> dict[str, Any]:
    """Juiz × juiz: concordância e kappa no `kind` e no `asked`, nos ids presentes nos dois.

    O último veredito de cada `trajectory_id` vale; task/condição/modelo vêm dos eventos.
    """
    va = {j["trajectory_id"]: j for j in a}
    vb = {j["trajectory_id"]: j for j in b}
    common = sorted(va.keys() & vb.keys())
    meta = {e["trajectory_id"]: e for e in events}
    kinds = [(va[i].get("kind"), vb[i].get("kind")) for i in common]
    asked = [(k1 == "ask", k2 == "ask") for k1, k2 in kinds]
    agree = lambda pairs: _rate(sum(x == y for x, y in pairs), len(pairs))  # noqa: E731
    return {
        "n": len(common),
        "kind": {**agree(kinds), "kappa": _kappa(kinds)},
        "asked": {**agree(asked), "kappa": _kappa(asked)},
        "asked_confusion": {
            f"a_{'ask' if x else 'other'}__b_{'ask' if y else 'other'}": c
            for (x, y), c in sorted(Counter(asked).items())
        },
        "only_in_a": len(va.keys() - vb.keys()),
        "only_in_b": len(vb.keys() - va.keys()),
        "disagreements": [
            {
                "trajectory_id": i,
                **{k: meta.get(i, {}).get(k) for k in ("task_id", "condition", "model")},
                "kind_a": k1,
                "kind_b": k2,
            }
            for i, (k1, k2) in zip(common, kinds, strict=True)
            if k1 != k2
        ],
    }


def compute_agent_metrics(
    finals: list[Row], n_boot: int = BOOTSTRAP_ITERATIONS, seed: int = BOOTSTRAP_SEED
) -> dict[str, Any]:
    """`finals` já com o juiz aplicado (merge_judgments). Falhas do harness ficam de fora."""
    excluded = [e for e in finals if e.get("termination") in HARNESS_FAILURES]
    rows = [e for e in finals if e.get("termination") not in HARNESS_FAILURES]
    models = sorted({e["model"] for e in rows})
    by_strata: dict[str, dict[str, Any]] = {}
    for axis in AXES:
        values = sorted({v for e in rows for v in _strata_values(e, axis)})
        by_strata[axis] = {
            v: by_condition_model([e for e in rows if v in _strata_values(e, axis)]) for v in values
        }
    return {
        "n_trajectories": len(rows),
        "n_tasks": len({e["task_id"] for e in rows}),
        "n_unjudged": sum(1 for e in rows if e.get("asked") is None),
        "harness_failures": dict(sorted(Counter(e["termination"] for e in excluded).items())),
        "by_condition_model": by_condition_model(rows),
        "by_target": {
            t: by_condition_model([e for e in rows if e["target"] == t])
            for t in sorted({e["target"] for e in rows})
        },
        "by_strata": by_strata,
        "vs_none": {
            m: _vs_none([e for e in rows if e["model"] == m], n_boot, seed) for m in models
        },
        "per_task": {m: _per_task_vs_none([e for e in rows if e["model"] == m]) for m in models},
        "easy_harm": {m: _easy_harm([e for e in rows if e["model"] == m]) for m in models},
    }
