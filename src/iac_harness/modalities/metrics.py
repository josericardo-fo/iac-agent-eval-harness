"""Métricas do teste de modalidades sobre os eventos `final` do events.jsonl."""

from __future__ import annotations

from collections import Counter
from typing import Any


def compute_modality_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Por modalidade × categoria: sucesso e estado n/N, tentativas e tokens médios, terminations.

    `success` = execução ok + estado certo (estrita); `state_pass` = só o estado certo (o check),
    a métrica comparável com os benchmarks que julgam pelo estado final.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for e in events:
        if e.get("event") == "final":
            groups.setdefault((e["modality"], e["category"]), []).append(e)
    out: dict[str, Any] = {}
    for (modality, category), finals in sorted(groups.items()):
        n = len(finals)
        succeeded = sum(bool(f["success"]) for f in finals)
        passed = sum(bool(f["check_pass"]) for f in finals)
        out.setdefault(modality, {})[category] = {
            "success": f"{succeeded}/{n}",
            "success_rate": succeeded / n,
            "state_pass": f"{passed}/{n}",
            "state_pass_rate": passed / n,
            "mean_attempts": sum(f["attempts"] for f in finals) / n,
            "mean_input_tokens": sum(f["input_tokens"] for f in finals) / n,
            "mean_output_tokens": sum(f["output_tokens"] for f in finals) / n,
            "mean_total_tokens": sum(f["total_tokens"] for f in finals) / n,
            "termination": dict(sorted(Counter(f["termination"] for f in finals).items())),
        }
    return out
