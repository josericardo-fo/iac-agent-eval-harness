"""Agregação do `agent-questions-report`: notas das perguntas por condição, toolkit ×
`none` por bootstrap pareado por tarefa, concordância entre dois juízes (kappa ponderado) e o
cruzamento descritivo com o usuário simulado.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from iac_harness.agentic.metrics import BASELINE_CONDITION
from iac_harness.agentic.outcome import success_final as success_final_rule
from iac_harness.agentic.question_judge import CRITERIA, SCORES
from iac_harness.metrics import BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED, paired_bootstrap


def weighted_kappa(pairs: list[tuple[int, int]], categories: tuple[int, ...] = SCORES):
    """Kappa de Cohen ponderado (pesos quadráticos); None sem pares ou com esperado = 0."""
    n = len(pairs)
    if not n:
        return None
    span = (categories[-1] - categories[0]) ** 2
    pa = {k: sum(a == k for a, _ in pairs) / n for k in categories}
    pb = {k: sum(b == k for _, b in pairs) / n for k in categories}
    observed = sum((a - b) ** 2 / span for a, b in pairs) / n
    expected = sum((i - j) ** 2 / span * pa[i] * pb[j] for i in categories for j in categories)
    return None if expected == 0 else 1 - observed / expected


Row = dict[str, Any]
FIELDS = (*CRITERIA, "total")
DISAGREEMENTS_FILENAME = "questions-disagreements.csv"
DISAGREEMENT_FIELDS = (
    "trajectory_id", "task_id", "condition", "model", "criteria",
    *(f"a_{c}" for c in CRITERIA), *(f"b_{c}" for c in CRITERIA),
)  # fmt: skip
ANSWER_CHARS = 120


def merge_questions(events: list[Row], grades: list[Row], run: str = "") -> list[Row]:
    """Eventos `final` com nota (a última linha de cada `trajectory_id` vale); `run` no pooled.

    Linha sem notas válidas (erro do juiz) fica com `scores` None e entra só na contagem de erros.
    """
    last = {g["trajectory_id"]: g for g in grades}
    return [
        {
            **e,
            "run": run,
            "scores": last[e["trajectory_id"]].get("scores"),
            "total": last[e["trajectory_id"]].get("total"),
        }
        for e in events
        if e.get("event") == "final" and e.get("trajectory_id") in last
    ]


def _value(r: Row, field: str) -> float:
    return r["total"] if field == "total" else r["scores"][field]


def _valid(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.get("scores")]


def _conditions(rows: list[Row]) -> list[str]:
    """`none` primeiro, depois as demais em ordem alfabética."""
    conds = {r["condition"] for r in rows}
    return [c for c in (BASELINE_CONDITION,) if c in conds] + sorted(conds - {BASELINE_CONDITION})


def by_condition(rows: list[Row]) -> dict[str, dict[str, Any]]:
    """Por condição: n (notas válidas), erros do juiz e a média de C1..C5 e do total."""
    out = {}
    for cond in _conditions(rows):
        mine = [r for r in rows if r["condition"] == cond]
        valid = _valid(mine)
        out[cond] = {
            "n": len(valid),
            "n_errors": len(mine) - len(valid),
            "means": {
                f: statistics.fmean(_value(r, f) for r in valid) if valid else None for f in FIELDS
            },
        }
    return out


def _per_unit(rows: list[Row], unit: Callable[[Row], str], field: str) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by[unit(r)].append(_value(r, field))
    return {k: statistics.fmean(v) for k, v in by.items()}


def vs_none(
    rows: list[Row], unit: Callable[[Row], str] = lambda r: r["task_id"],
    n_boot: int = BOOTSTRAP_ITERATIONS, seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, Any]]:  # fmt: skip
    """Cada condição × `none`: bootstrap pareado da nota média por unidade (padrão: tarefa).

    Só entram unidades com pergunta (nota válida) nas duas condições; diferença = condição - none.
    """
    valid = _valid(rows)
    base = [r for r in valid if r["condition"] == BASELINE_CONDITION]
    out = {}
    for cond in _conditions(valid):
        if cond == BASELINE_CONDITION:
            continue
        mine = [r for r in valid if r["condition"] == cond]
        units = {unit(r) for r in base} & {unit(r) for r in mine}
        out[cond] = {"n_units": len(units)}
        for f in FIELDS:
            out[cond][f] = (
                paired_bootstrap(_per_unit(base, unit, f), _per_unit(mine, unit, f), n_boot, seed)
                if units
                else None
            )
    return out


def question_agreement(a: list[Row], b: list[Row]) -> dict[str, Any]:
    """Juiz A × juiz B nas trajetórias com nota válida nos dois: kappa ponderado por critério e
    os itens com desacordo de 2 pontos em algum critério."""
    va = {(r["run"], r["trajectory_id"]): r for r in _valid(a)}
    vb = {(r["run"], r["trajectory_id"]): r for r in _valid(b)}
    common = sorted(va.keys() & vb.keys())
    disagreements = []
    for k in common:
        sa, sb = va[k]["scores"], vb[k]["scores"]
        crit = [c for c in CRITERIA if abs(sa[c] - sb[c]) == 2]
        if crit:
            disagreements.append(
                {
                    **{f: va[k].get(f) for f in ("trajectory_id", "task_id", "condition", "model")},
                    "criteria": " ".join(crit),
                    **{f"a_{c}": sa[c] for c in CRITERIA},
                    **{f"b_{c}": sb[c] for c in CRITERIA},
                }
            )
    return {
        "n": len(common),
        "kappa": {
            c: weighted_kappa([(va[k]["scores"][c], vb[k]["scores"][c]) for k in common])
            for c in CRITERIA
        },
        "disagreements": disagreements,
    }


def _user_answer(e: Row) -> str | None:
    """Resposta do usuário simulado à 1ª pergunta (`{"answer": ...}` vira o texto)."""
    exchanges = e.get("sim_exchanges") or []
    if not exchanges:
        return None
    user = exchanges[0].get("user")
    try:
        data = json.loads(user)
        user = data.get("answer", user) if isinstance(data, dict) else user
    except (TypeError, json.JSONDecodeError):
        pass
    return None if user is None else str(user)


def _fmt(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "–"
    return f"{x:+.2f}" if signed else f"{x:.2f}"


def _cell(v: tuple[float, float, float] | None) -> str:
    if v is None:
        return "–"
    return f"{_fmt(v[0], True)} [{_fmt(v[1], True)}, {_fmt(v[2], True)}]"


def _one_line(text: str) -> str:
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= ANSWER_CHARS else text[: ANSWER_CHARS - 1] + "…"


def _section(
    title: str, rows: list[Row], unit: Callable[[Row], str],
    agreement: dict[str, Any] | None, unit_label: str,
) -> list[str]:  # fmt: skip
    head = " | ".join(FIELDS)
    lines = [f"## {title}", "", f"| condição | n | erros | {head} |",
             "|---|---|---|" + "---|" * len(FIELDS)]  # fmt: skip
    for cond, b in by_condition(rows).items():
        means = " | ".join(_fmt(b["means"][f]) for f in FIELDS)
        lines.append(f"| {cond} | {b['n']} | {b['n_errors']} | {means} |")
    cmp = vs_none(rows, unit)
    if cmp:
        lines += [
            "",
            f"Toolkit × `none`: bootstrap pareado por {unit_label}, só {unit_label}s com pergunta "
            "nas duas condições; diferença = condição − none [IC 95%].",
            "",
            f"| condição | {unit_label}s | {head} |",
            "|---|---|" + "---|" * len(FIELDS),
        ]
        for cond, c in cmp.items():
            cells = " | ".join(_cell(c[f]) for f in FIELDS)
            lines.append(f"| {cond} | {c['n_units']} | {cells} |")
    if agreement is not None:
        lines += ["", "Concordância entre os juízes (kappa de Cohen ponderado, pesos quadráticos):",
                  "", "| critério | n | kappa |", "|---|---|---|"]  # fmt: skip
        for c in CRITERIA:
            lines.append(f"| {c} | {agreement['n']} | {_fmt(agreement['kappa'][c])} |")
        lines += [
            "",
            f"Desacordos de 2 pontos em algum critério: {len(agreement['disagreements'])}",
        ]
    e13 = [r for r in _valid(rows) if r.get("sim_exchanges")]
    if e13:
        crit = " | ".join(CRITERIA)
        lines += ["", "Usuário simulado (descritivo): nota × resposta e `success_final`.", "",
                  f"| tarefa | condição | total | {crit} | resposta | success_final |",
                  "|---|---|---|" + "---|" * len(CRITERIA) + "---|---|"]  # fmt: skip
        for r in sorted(e13, key=lambda r: (r["task_id"], r["condition"])):
            answer = _user_answer(r)
            scores = " | ".join(str(r["scores"][c]) for c in CRITERIA)
            lines.append(
                f"| {r['task_id']} | {r['condition']} | {r['total']} | {scores} | "
                f"{_one_line(answer) if answer else '–'} | "
                f"{success_final_rule(r.get('check_pass_final'))} |"
            )
    return [*lines, ""]


def questions_report(
    runs: list[tuple[str, list[Row], tuple[list[Row], list[Row]] | None]], judge_file: str
) -> str:
    """Markdown: uma seção por run (pareado por tarefa) e o agregado (pareado por run + tarefa).

    `runs`: (nome, linhas de `merge_questions`, (linhas do juiz A, do juiz B) ou None).
    """
    lines = [f"# E15: qualidade das perguntas (`{judge_file}`)", ""]
    for name, rows, pair in runs:
        agreement = question_agreement(*pair) if pair else None
        lines += _section(name, rows, lambda r: r["task_id"], agreement, "tarefa")
    pooled = [r for _, rows, _ in runs for r in rows]
    pairs = [p for _, _, p in runs if p]
    pooled_agreement = (
        question_agreement([r for a, _ in pairs for r in a], [r for _, b in pairs for r in b])
        if pairs
        else None
    )
    lines += _section(
        "Agregado (todos os runs)", pooled, lambda r: f"{r['run']}|{r['task_id']}",
        pooled_agreement, "run+tarefa",
    )  # fmt: skip
    return "\n".join(lines)
