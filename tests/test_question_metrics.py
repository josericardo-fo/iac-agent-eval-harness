"""Agregação do E15 (agent-questions-report): kappa ponderado e relatório num run sintético."""

import csv
import json

import pytest

from iac_harness.agentic.question_metrics import weighted_kappa
from iac_harness.cli import main


def test_weighted_kappa_worked_example():
    # pa = (.25, .25, .5), pb = (.5, .25, .25); só (2, 0) discorda, peso 1:
    # observado 0.25, esperado 0.40625 -> 1 - 0.25 / 0.40625 = 5/13
    assert weighted_kappa([(0, 0), (1, 1), (2, 2), (2, 0)]) == pytest.approx(5 / 13)


def test_weighted_kappa_perfect_and_degenerate():
    assert weighted_kappa([(0, 0), (2, 2), (1, 1)]) == pytest.approx(1.0)
    assert weighted_kappa([(1, 1), (1, 1)]) is None  # esperado = 0: indefinido
    assert weighted_kappa([]) is None


def _final(task, cond, rep=1, model="Haiku 5.5", **kw):
    return {"event": "final", "trajectory_id": f"{task}|{cond}|{model}|{rep}", "task_id": task,
            "condition": cond, "model": model, "rep": rep, "final_text": "Which one?",
            **kw}  # fmt: skip


def _q(e, s, error=None):
    """Linha do juiz da pergunta com todas as notas = s (ou só C1..C5 dados em dict)."""
    scores = s if isinstance(s, dict) else {c: s for c in ("C1", "C2", "C3", "C4", "C5")}
    total = 0 if scores["C1"] == 0 else sum(scores.values())
    if error:
        return {"trajectory_id": e["trajectory_id"], "scores": None, "total": None,
                "error": error}  # fmt: skip
    return {"trajectory_id": e["trajectory_id"], "scores": scores, "total": total, "error": None}


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _run(path, events, files):
    path.mkdir()
    _jsonl(path / "events.jsonl", events)
    for name, rows in files.items():
        _jsonl(path / name, rows)
    return path


def test_report_means_and_paired_bootstrap(tmp_path, capsys):
    ev = [_final("t1", "none"), _final("t1", "own-rules"), _final("t2", "none"),
          _final("t2", "own-rules"), _final("t3", "own-rules"),
          _final("t4", "none", rep=2)]  # fmt: skip
    rows = [_q(ev[0], 1), _q(ev[1], 2), _q(ev[2], 1), _q(ev[3], 2), _q(ev[4], 0),
            _q(ev[5], 1, error="resposta do juiz inválida")]  # fmt: skip
    run = _run(tmp_path / "run-a", ev, {"judge-questions.jsonl": rows})
    assert main(["agent-questions-report", str(run)]) == 0
    out = capsys.readouterr().out
    # none: 2 notas válidas (total 5 cada) e 1 erro; own-rules: 10, 10 e 0 (C1 = 0)
    assert "| none | 2 | 1 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 5.00 |" in out
    assert "| own-rules | 3 | 0 | 1.33 | 1.33 | 1.33 | 1.33 | 1.33 | 6.67 |" in out
    # pareado só em t1 e t2 (t3 não tem pergunta no none): +5 em todas as reamostras
    assert "| own-rules | 2 | +1.00 [+1.00, +1.00] |" in out
    assert "+5.00 [+5.00, +5.00] |" in out
    assert "## run-a" in out and "## Agregado" in out
    assert not (run / "questions-disagreements.csv").exists()


def test_report_compare_kappa_and_disagreements(tmp_path, capsys):
    ev = [_final("t1", "none"), _final("t2", "none"), _final("t3", "own-rules")]
    a = [_q(ev[0], 2), _q(ev[1], 1), _q(ev[2], 0)]
    b = [_q(ev[0], 2), _q(ev[1], 1), _q(ev[2], {"C1": 0, "C2": 2, "C3": 0, "C4": 0, "C5": 0})]
    run = _run(tmp_path / "r", ev, {"a.jsonl": a, "b.jsonl": b})
    assert main(["agent-questions-report", str(run), "--judge-file", "a.jsonl",
                 "--compare", "a.jsonl", "b.jsonl"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "| C1 | 3 | 1.00 |" in out
    assert "| C2 | 3 | 0.00 |" in out  # pares (2,2), (1,1), (0,2): observado = esperado = 1/3
    with (run / "questions-disagreements.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["trajectory_id"] for r in rows] == [ev[2]["trajectory_id"]]
    assert rows[0]["criteria"] == "C2" and rows[0]["a_C2"] == "0" and rows[0]["b_C2"] == "2"


def test_report_e13_joins_user_answer_and_final_success(tmp_path, capsys):
    ev = [_final("real-x", "none", sim_exchanges=[
              {"agent": "Which IP?", "user": '{"answer": "Use 10.0.0.1, please."}'}],
              check_pass_final=True)]  # fmt: skip
    run = _run(tmp_path / "e13", ev, {"judge-questions.jsonl": [_q(ev[0], 2)]})
    assert main(["agent-questions-report", str(run)]) == 0
    out = capsys.readouterr().out
    assert "| real-x | none | 10 | 2 | 2 | 2 | 2 | 2 | Use 10.0.0.1, please. | True |" in out
