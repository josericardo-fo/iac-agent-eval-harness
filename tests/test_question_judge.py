"""Juiz de qualidade da pergunta (E15): parsing, prompt e seleção, sem LLM."""

import json

import pytest

from iac_harness.agentic.question_judge import (
    QUESTION_PROMPT_VERSION,
    QuestionJudge,
    meta_header,
    parse_question_verdict,
)
from iac_harness.cli import agent as cli_agent
from iac_harness.cli import main


def _reply(scores, reason="r"):
    return json.dumps({c: {"score": s, "reason": reason} for c, s in scores.items()})


def test_valid_verdict_sums_scores():
    out = parse_question_verdict(_reply({"C1": 2, "C2": 1, "C3": 0, "C4": 1, "C5": 2}))
    assert out["scores"] == {"C1": 2, "C2": 1, "C3": 0, "C4": 1, "C5": 2}
    assert out["total"] == 6
    assert out["reasons"]["C3"] == "r"
    assert out["error"] is None


def test_c1_zero_forces_total_zero():
    out = parse_question_verdict(
        "Reasoning first. " + _reply({"C1": 0, "C2": 2, "C3": 2, "C4": 2, "C5": 2})
    )
    assert out["total"] == 0
    assert out["scores"]["C2"] == 2  # as notas ficam gravadas; só o total zera


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no json here",
        "[1, 2]",
        _reply({"C1": 2, "C2": 3, "C3": 0, "C4": 1, "C5": 2}),  # fora de 0–2
        _reply({"C1": 2, "C2": -1, "C3": 0, "C4": 1, "C5": 2}),
        _reply({"C1": 2, "C2": 1.5, "C3": 0, "C4": 1, "C5": 2}),
        _reply({"C1": 2, "C2": True, "C3": 0, "C4": 1, "C5": 2}),
        _reply({"C1": 2, "C2": 1, "C3": 0, "C4": 1}),  # falta C5
        '{"C1": 2, "C2": 1, "C3": 0, "C4": 1, "C5": 2}',  # sem score/reason
        '{"C1": {"score": 2',  # truncado
    ],
)
def test_invalid_verdict_records_error(text):
    out = parse_question_verdict(text)
    assert out["error"] and out["scores"] is None and out["total"] is None


class FakeLlm:
    params = {"provider": "fake", "model": "fake-1", "max_tokens": 10}

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.reply


GOOD = _reply({"C1": 2, "C2": 2, "C3": 1, "C4": 0, "C5": 2})


def test_grade_sends_inputs_and_returns_verdict():
    llm = FakeLlm("ok " + GOOD)
    judge = QuestionJudge(llm)
    out = judge.grade(
        request="Delete the logs bucket.",
        expect="ask",
        meta_note="Há dois buckets de logs.",
        user_card="O usuário quer apagar o antigo.",
        question="Which of the two buckets should I delete?",
    )
    assert out["total"] == 7 and out["raw"] == "ok " + GOOD and out["error"] is None
    system, user = llm.calls[0]
    for text in ("Delete the logs bucket.", "Há dois buckets de logs.",
                 "O usuário quer apagar o antigo.", "Which of the two buckets"):  # fmt: skip
        assert text in user
    assert "ask" in user
    for c in ("C1", "C2", "C3", "C4", "C5"):
        assert c in system


def test_grade_without_user_card_says_so():
    llm = FakeLlm(GOOD)
    QuestionJudge(llm).grade("Do X.", "act", "", None, "Should I?")
    assert "(none)" in llm.calls[0][1]


def test_params_record_prompt_version():
    p = QuestionJudge(FakeLlm(GOOD)).params
    assert p["judge_prompt_version"] == QUESTION_PROMPT_VERSION == "e15-question-v1"
    assert p["judge_model"] == "fake-1" and p["judge_provider"] == "fake"


def test_meta_header_takes_leading_comments_only():
    text = '# E2, par 6.\n# Há dois buckets.\n#\nid = "x"\n# depois do id\n'
    assert meta_header(text) == "E2, par 6.\nHá dois buckets.\n"


# --- agent-judge-questions pela CLI (tarefas reais do catálogo, juiz falso) ---

TID = "amb-del-s3|own-rules-v2|Sonnet 5.5|1"


def _ev(task, cond, model, rep, text):
    return {"event": "final", "trajectory_id": f"{task}|{cond}|{model}|{rep}", "task_id": task,
            "condition": cond, "model": model, "rep": rep, "expect": "ask",
            "termination": "DONE", "final_text": text}  # fmt: skip


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _run(tmp_path, asked):
    """Run com uma trajetória por (texto, asked); judge.jsonl com o `asked` de cada uma."""
    run = tmp_path / "r"
    run.mkdir()
    (run / "run_config.json").write_text(json.dumps({"config": {}}))
    events = [_ev("amb-del-s3", "own-rules-v2", "Sonnet 5.5", i + 1, f"Question {i}?")
              for i in range(len(asked))]  # fmt: skip
    _jsonl(run / "events.jsonl", events)
    _jsonl(run / "judge.jsonl", [{"trajectory_id": e["trajectory_id"], "asked": a,
                                  "kind": "ask" if a else "done"}
                                 for e, a in zip(events, asked, strict=True)])  # fmt: skip
    return run, events


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLlm(GOOD)
    monkeypatch.setattr(cli_agent, "build_text_model", lambda section: llm)
    return llm


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_judge_questions_grades_only_asked(tmp_path, fake_llm):
    run, events = _run(tmp_path, [True, False, True])
    assert main(["agent-judge-questions", str(run)]) == 0
    rows = _lines(run / "judge-questions.jsonl")
    assert [r["trajectory_id"] for r in rows] == [events[0]["trajectory_id"],
                                                  events[2]["trajectory_id"]]  # fmt: skip
    assert rows[0]["total"] == 7 and rows[0]["scores"]["C1"] == 2 and rows[0]["error"] is None
    assert rows[0]["judge_prompt_version"] == "e15-question-v1" and rows[0]["raw"] == GOOD
    assert "Question 0?" in fake_llm.calls[0][1]


def test_judge_questions_prompt_hides_condition_model_and_id(tmp_path, fake_llm):
    run, events = _run(tmp_path, [True])
    assert main(["agent-judge-questions", str(run)]) == 0
    system, user = fake_llm.calls[0]
    for secret in ("own-rules-v2", "Sonnet", events[0]["trajectory_id"], "|own-rules"):
        assert secret not in system + user
    assert "Há dois buckets de logs" in user  # cabeçalho do meta.toml da tarefa real
    assert "Expected behaviour (task authors): ask" in user


def test_judge_questions_resumes_and_limits(tmp_path, fake_llm):
    run, events = _run(tmp_path, [True, True, True, True])
    _jsonl(run / "q.jsonl", [{"trajectory_id": events[0]["trajectory_id"], "total": 1}])
    assert main(["agent-judge-questions", str(run), "--out", "q.jsonl", "--limit", "2"]) == 0
    assert len(fake_llm.calls) == 2
    assert main(["agent-judge-questions", str(run), "--out", "q.jsonl"]) == 0
    assert len(fake_llm.calls) == 3  # só a que faltava
    ids = [r["trajectory_id"] for r in _lines(run / "q.jsonl")]
    assert sorted(ids) == sorted(e["trajectory_id"] for e in events)
    assert not (run / "judge-questions.jsonl").exists()


def test_judge_questions_leaves_judge_jsonl_untouched(tmp_path, fake_llm):
    run, _ = _run(tmp_path, [True])
    before = (run / "judge.jsonl").read_bytes()
    assert main(["agent-judge-questions", str(run)]) == 0
    assert (run / "judge.jsonl").read_bytes() == before


def test_judge_questions_uses_codex_provider(tmp_path, monkeypatch):
    run, _ = _run(tmp_path, [True])
    sections = []
    fake = lambda section: sections.append(section) or FakeLlm(GOOD)  # noqa: E731
    monkeypatch.setattr(cli_agent, "build_text_model", fake)
    assert main(["agent-judge-questions", str(run), "--provider", "codex"]) == 0
    assert sections[0]["provider"] == "codex"
