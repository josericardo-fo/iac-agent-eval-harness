"""Formatação das tabelas e resumos que os comandos imprimem (modalidades e agente)."""

from __future__ import annotations

from typing import Any

from iac_harness.agentic.metrics import (
    by_condition_model,
)
from iac_harness.modalities.metrics import (
    compute_modality_metrics,
)


def _modality_table(rows: list[dict[str, Any]], modalities: list[str]) -> str:
    """Tabela tarefa × modalidade: baseline_pass/ref_exec_ok/ref_check_pass (1/0/-)."""

    def flag(value: Any) -> str:
        return "-" if value is None else str(int(bool(value)))

    cells = {
        (r["task_id"], r["modality"]): (
            "sem ref" if r.get("ref_missing")
            else flag(r["baseline_pass"]) + flag(r["ref_exec_ok"]) + flag(r["ref_check_pass"])
            + ("" if r["valid"] else " !")
        )
        for r in rows
    }  # fmt: skip
    tasks = list(dict.fromkeys(r["task_id"] for r in rows))
    width = max([len(t) for t in tasks] + [4])
    lines = [
        "células: baseline_pass ref_exec_ok ref_check_pass (esperado 011, ou 110 nas tarefas"
        " expect=ask; '!' = inválida; 'sem ref' = modalidade sem referência)",
        f"{'task':<{width}}  " + "  ".join(f"{m:<10}" for m in modalities),
    ]
    for t in tasks:
        lines.append(
            f"{t:<{width}}  " + "  ".join(f"{cells.get((t, m), ''):<10}" for m in modalities)
        )
    return "\n".join(lines)


def _replay_table(metrics: dict[str, Any]) -> str:
    """collateral_ok e concordância do check por categoria × modalidade."""
    header = (
        f"{'categoria':<9} {'modalidade':<10} {'n':>3} {'colat ok':>8} {'colat X':>7} "
        f"{'colat ?':>7} {'check=orig':>10}"
    )
    lines = [header, "-" * len(header)]
    rows = [
        (cat, mod, m) for cat, mods in metrics["by_category"].items() for mod, m in mods.items()
    ]
    rows += [("total", mod, m) for mod, m in metrics["by_modality"].items()]
    for cat, mod, m in rows:
        c = m["collateral_ok"]
        agree = f"{m['check_agreement']['k']}/{m['check_agreement']['n']}"
        lines.append(
            f"{cat:<9} {mod:<10} {m['n']:>3} {c['true']:>8} {c['false']:>7} {c['none']:>7} "
            f"{agree:>10}"
        )
    lines.append(f"pulados: {metrics['n_skipped']} {metrics['skipped']}")
    for item in metrics["collateral_false"]:
        lines.append(f"colateral: {item['task_id']} × {item['modality']} rep {item['rep']}: "
                     f"{', '.join(item['changed'])}")  # fmt: skip
    return "\n".join(lines)


def _short_rate(r: dict[str, Any]) -> str:
    """k/n [IC de Wilson curto, em %]."""
    lo, hi = r["wilson"]
    return f"{r['k']}/{r['n']} [{lo * 100:.0f}-{hi * 100:.0f}%]"


def _modality_summary(events: list[dict[str, Any]]) -> str:
    metrics = compute_modality_metrics(events)
    lines = [f"{metrics['n_trajectories']} trajetórias"]
    for modality, blocks in metrics["modalities"].items():
        for category, m in blocks["by_category"].items():
            first = f"{m['first_attempt_success']['k']}/{m['first_attempt_success']['n']}"
            state = f"{m['state_pass']['k']}/{m['state_pass']['n']}"
            lines.append(
                f"{modality:<10} {category:<7} sucesso {_short_rate(m['success']):<17} "
                f"1ª tent. {first:<6} estado {state:<6} "
                f"tentativas {m['mean_attempts']:.2f}  tokens {m['tokens']['mean_total']:.0f}  "
                f"{m['termination']}"
            )
    return "\n".join(lines)


def _modality_metrics_table(metrics: dict[str, Any]) -> str:
    """Tabela legível modalidade × categoria (e total por modalidade)."""
    header = (
        f"{'modalidade':<10} {'categoria':<9} {'sucesso [IC 95%]':<20} {'1ª tentativa':<13} "
        f"{'tentativas':>10} {'tokens':>8} {'custo US$':>10}"
    )
    lines = [header, "-" * len(header)]
    for modality, blocks in metrics["modalities"].items():
        for category, m in [*blocks["by_category"].items(), ("total", blocks["overall"])]:
            first = f"{m['first_attempt_success']['k']}/{m['first_attempt_success']['n']}"
            lines.append(
                f"{modality:<10} {category:<9} {_short_rate(m['success']):<20} "
                f"{first:<13} {m['mean_attempts']:>10.2f} "
                f"{m['tokens']['mean_total']:>8.0f} {m['cost_usd']:>10.4f}"
            )
    return "\n".join(lines)


def _free_table(metrics: dict[str, Any]) -> str:
    """Forma livre (E4): escolhas por categoria e, com --baseline, a comparação de tokens."""
    lines = ["", "forma livre: 1ª escolha / última escolha / troca / acordo R11"]
    free = metrics["free"]
    for category, b in [*free["by_category"].items(), ("total", free["overall"])]:
        agree = b["r11_agreement"]
        lines.append(
            f"{category:<9} {b['chosen_first']}  {b['chosen']}  "
            f"troca {b['switched']['k']}/{b['switched']['n']}  "
            f"R11 {agree['k']}/{agree['n']}"
        )
    base = metrics.get("baseline")
    if base:
        lines += [
            "",
            f"livre × baseline {base['run_dir']} ({base['n_tasks']} tarefas; "
            "razão = livre / forma fixa, média geométrica dos tokens médios por tarefa)",
        ]

        def fmt(r: dict[str, Any]) -> str:
            if r["ratio_geomean"] is None:
                return "n/d"
            lo, hi = r["ratio_ci95"]
            return f"{r['ratio_geomean']:.2f} [{lo:.2f}, {hi:.2f}]"

        for category, b in [*base["by_category"].items(), ("total", base["overall"])]:
            for k in ("total", "output"):
                vs_cheapest = b["ratio_vs_cheapest"][k]
                vs_priciest = b["ratio_vs_most_expensive"][k]
                chose = b["chose_cheapest"][k]
                lines.append(
                    f"{category:<9} {k:<6} escolheu a mais barata {chose['k']}/{chose['n']}  "
                    f"vs mais barata {fmt(vs_cheapest)}  vs mais cara {fmt(vs_priciest)}"
                )
            lines.append(f"{category:<9} sucesso livre × melhor fixa {b['success_vs_best_fixed']}")
        if base["overall"]["undecided"]:
            lines.append(f"sem escolha majoritária: {', '.join(base['overall']['undecided'])}")
    return "\n".join(lines)


def _agent_summary(events: list[dict[str, Any]]) -> str:
    finals = [e for e in events if e.get("event") == "final"]
    lines = [f"{len(finals)} trajetórias"]
    for key, b in by_condition_model(finals).items():
        lines.append(
            f"{key:<28} estado {b['state_pass']['k']}/{b['state_pass']['n']}  "
            f"tokens {b['tokens']['mean_total'] or 0:.0f}  US$ {b['cost_usd']:.4f}  "
            f"{b['termination']}"
        )
    return "\n".join(lines)


def _agent_metrics_table(metrics: dict[str, Any]) -> str:
    header = (
        f"{'condição|modelo':<28} {'sucesso [IC 95%]':<20} {'estado':<9} {'perguntou':<10} "
        f"{'colateral':<10} {'releu':<8} {'tokens':>9} {'custo US$':>10}"
    )
    lines = [
        f"{metrics['n_trajectories']} trajetórias, {metrics['n_unjudged']} sem juiz, "
        f"falhas do harness {metrics['harness_failures']}",
        header,
        "-" * len(header),
    ]

    def frac(r: dict[str, Any]) -> str:
        return f"{r['k']}/{r['n']}"

    for key, b in metrics["by_condition_model"].items():
        lines.append(
            f"{key:<28} {_short_rate(b['success']):<20} {frac(b['state_pass']):<9} "
            f"{frac(b['asked']):<10} {frac(b['collateral_ok']):<10} "
            f"{frac(b['verified_state']):<8} {b['tokens']['mean_total'] or 0:>9.0f} "
            f"{b['cost_usd']:>10.4f}"
        )
        if "user_sim" in b:
            u = b["user_sim"]
            lines.append(
                f"  usuário simulado: respondeu {frac(u['replied'])}, "
                f"estado final {frac(u['state_pass_final'])}, "
                f"sucesso final {frac(u['success_final'])}, erros {u['errors']}"
            )
    for model, cmps in metrics["vs_none"].items():
        lines += ["", f"{model}: condição × none (sucesso; McNemar pela maioria das reps)"]
        for c in cmps["success"]:
            lo, hi = c["diff_ci95"]
            lines.append(
                f"  {c['pair']:<22} n={c['n_pairs']:<3} só none {c['only_first']:<3} "
                f"só cond {c['only_second']:<3} p={c['mcnemar_p']:.3f} holm={c['holm_p']:.3f} "
                f"dif {c['diff_mean']:+.2f} [{lo:+.2f}, {hi:+.2f}]"
            )
        for cond, harm in metrics["easy_harm"][model].items():
            lines.append(
                f"  piora nas fáceis {cond:<12} {frac(harm['harmed'])} "
                f"{', '.join(harm['harmed_tasks'])}"
            )
        for cond, rows in metrics["per_task"][model].items():
            lines.append(f"  por tarefa: none × {cond} (maioria das reps; razão de tokens)")
            for r in rows:
                ratio = "n/d" if r["tokens_ratio"] is None else f"{r['tokens_ratio']:.2f}"
                lines.append(
                    f"    {r['task_id']:<28} none {_mark(r['none_success'])} "
                    f"cond {_mark(r['cond_success'])} tokens ×{ratio}"
                )
    for model, scores in metrics.get("partial_scores", {}).items():
        lines += ["", f"{model}: notas parciais do oráculo (média por tarefa)"]
        lines += [
            f"  {cond:<14} rego_frac {_fmt_score(s['rego_frac'])} "
            f"type_f1 {_fmt_score(s['type_f1'])}"
            for cond, s in scores["mean"].items()
        ]
        lines += [
            f"  {c['metric']:<10} {c['pair']:<32} n={c['n_pairs']:<3} "
            f"dif {c['diff_mean']:+.3f} [{c['diff_ci95'][0]:+.3f}, {c['diff_ci95'][1]:+.3f}]"
            for c in scores["paired"]
        ]
    cmp = metrics.get("compare")
    if cmp:
        lines += [
            "",
            f"concordância × {cmp['run_dir']}: {frac(cmp['agreement'])} "
            f"(tarefa, condição, modelo); só neste {cmp['only_first']}, "
            f"só no outro {cmp['only_second']}",
        ]
        lines += [
            f"  {d['task_id']} {d['condition']} {d['model']}: "
            f"{_mark(d['first'])} × {_mark(d['second'])}"
            for d in cmp["discordant"]
        ]
    return "\n".join(lines)


def _fmt_score(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.3f}"


def _mark(ok: bool) -> str:
    return "ok" if ok else "--"
