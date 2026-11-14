"""agent-judge e agent-metrics pela CLI, com juiz falso (sem LLM)."""

import csv
import json

import pytest

from iac_harness import cli
from iac_harness.cli import main
from test_agentic_metrics import _final


def _write_run(run_dir, events, judgments=None):
    run_dir.mkdir(parents=True)
    (run_dir / "run_config.json").write_text(json.dumps({"config": {}}))
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    if judgments is not None:
        (run_dir / "judge.jsonl").write_text("".join(json.dumps(j) + "\n" for j in judgments))


def _verdicts(events, kind="done"):
    return [{"trajectory_id": e["trajectory_id"], "kind": kind, "asked": kind == "ask"}
            for e in events]  # fmt: skip


class FakeJudge:
    params = {"judge_model": "fake"}

    def __init__(self, model=None, llm=None):
        self.calls = []

    def classify(self, final_text, task_prompt, mutated):
        FakeJudge.last = self
        self.calls.append(mutated)
        return {"asked": False, "kind": "done", "raw": ""}


def test_agent_judge_passes_mutation_summary(tmp_path, monkeypatch):
    events = [
        _final("mod-sqs-retention", "none", 1, True, final_text="ok", mutated=True),
        _final("mod-sqs-retention", "none", 2, True, final_text="ok", mutated=False),
        # evento antigo sem `mutated`: verified_state não None implica que houve mudança
        _final("mod-sqs-retention", "none", 3, True, final_text="ok", verified_state=False),
    ]
    _write_run(tmp_path / "r", events)
    monkeypatch.setattr(cli, "LlmJudge", FakeJudge)
    monkeypatch.setattr(cli, "build_text_model", lambda section, default: None)
    assert main(["agent-judge", str(tmp_path / "r")]) == 0
    assert FakeJudge.last.calls == [True, False, True]


def test_agent_judge_sample_writes_csv(tmp_path, monkeypatch):
    events = [_final(f"t{i}", "none", 1, True, final_text=f"m{i}") for i in range(5)]
    _write_run(tmp_path / "r", events, _verdicts(events))
    monkeypatch.setattr(cli, "LlmJudge", lambda **kw: pytest.fail("--sample não chama o juiz"))
    assert main(["agent-judge", str(tmp_path / "r"), "--sample", "3", "--seed", "1"]) == 0
    with (tmp_path / "r" / "judge_sample.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    assert list(rows[0]) == ["trajectory_id", "final_text_excerpt", "judge_kind", "human_kind"]
    assert all(r["human_kind"] == "" and r["judge_kind"] == "done" for r in rows)


def test_agent_judge_agreement_reads_csv(tmp_path, capsys):
    path = tmp_path / "judge_sample.csv"
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, ["trajectory_id", "final_text_excerpt", "judge_kind", "human_kind"])
        w.writeheader()
        w.writerow({"trajectory_id": "a", "final_text_excerpt": "x", "judge_kind": "ask",
                    "human_kind": "ask"})  # fmt: skip
        w.writerow({"trajectory_id": "b", "final_text_excerpt": "y", "judge_kind": "done",
                    "human_kind": "partial"})  # fmt: skip
    assert main(["agent-judge", "--agreement", str(path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"]["k"] == 1 and out["kind"]["n"] == 2


def test_agent_judge_needs_run_dir_without_agreement():
    with pytest.raises(SystemExit):
        main(["agent-judge"])


def test_agent_metrics_compare(tmp_path, capsys):
    a = [_final("t1", "none", r, True) for r in (1, 2, 3)]
    b = [_final("t1", "none", r, False) for r in (1, 2, 3)]
    _write_run(tmp_path / "floci", a, _verdicts(a))
    _write_run(tmp_path / "aws", b, _verdicts(b))
    assert main(["agent-metrics", str(tmp_path / "floci"), "--compare", str(tmp_path / "aws")]) == 0
    out = json.loads(capsys.readouterr().out)
    cmp = out["compare"]
    assert cmp["run_dir"] == str(tmp_path / "aws")
    assert cmp["n_pairs"] == 1 and cmp["agreement"]["k"] == 0
    assert "per_task" in out and "easy_harm" in out


def test_agent_metrics_table_shows_new_sections(tmp_path, capsys):
    events = []
    for task in ("t1", "t2"):
        for rep in (1, 2, 3):
            events.append(_final(task, "none", rep, True))
            events.append(_final(task, "own-tools", rep, task == "t2"))
    _write_run(tmp_path / "r", events, _verdicts(events))
    _write_run(tmp_path / "o", events, _verdicts(events))
    argv = ["agent-metrics", str(tmp_path / "r"), "--format", "table", "--compare",
            str(tmp_path / "o")]  # fmt: skip
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "piora nas fáceis" in out and "1/2" in out
    assert "t1" in out and "concordância" in out


# ---------- usuário simulado na config ----------


def _cfg(**sections):
    return {"agent": {}, **sections}


def test_build_user_sim_absent_keeps_old_flow():
    assert cli._build_user_sim(_cfg()) is None


def test_build_user_sim_uses_codex_by_default_and_max_replies(monkeypatch):
    from test_agentic_judge import FakeText

    seen = []

    def fake_factory(section, default_provider):
        seen.append((section, default_provider))
        return FakeText("x")

    monkeypatch.setattr(cli, "build_text_model", fake_factory)
    monkeypatch.setattr(cli, "_build_judge", lambda section, model=None: FakeJudge())
    sim, judge = cli._build_user_sim(_cfg(user_sim={"max_replies": 2}, judge={"model": "J"}))
    assert sim.max_replies == 2 and isinstance(judge, FakeJudge)
    assert seen[0][1] == "codex"
    sim, _ = cli._build_user_sim(_cfg(user_sim={}))
    assert sim.max_replies == 1


def test_build_user_sim_rejects_zero_replies(monkeypatch):
    from test_agentic_judge import FakeText

    monkeypatch.setattr(cli, "build_text_model", lambda s, d: FakeText("x"))
    with pytest.raises(SystemExit):
        cli._build_user_sim(_cfg(user_sim={"max_replies": 0}))


def test_example_config_is_valid_and_fixture_loads():
    import tomllib
    from pathlib import Path

    from iac_harness.modalities.loader import load_tasks

    root = Path(__file__).parent.parent
    cfg = tomllib.loads((root / "configs/e13-usersim-floci.toml").read_text())
    assert cfg["user_sim"]["provider"] == "codex" and cfg["user_sim"]["max_replies"] == 1
    tasks = load_tasks(root / cfg["modality"]["tasks_dir"])
    assert [t.task_id for t in tasks] == ["sim-fixture-bucket"] and tasks[0].user_card


def _ev(n):
    return [_final("mod-sqs-retention", "none", i, True, final_text=f"m{i}") for i in range(n)]


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_agent_judge_out_leaves_primary_untouched(tmp_path, monkeypatch):
    events = _ev(3)
    run = tmp_path / "r"
    _write_run(run, events, _verdicts(events, "ask"))
    before = (run / "judge.jsonl").read_bytes()
    monkeypatch.setattr(cli, "LlmJudge", FakeJudge)
    monkeypatch.setattr(cli, "build_text_model", lambda section, default: None)
    assert main(["agent-judge", str(run), "--out", "judge_luna.jsonl"]) == 0
    assert (run / "judge.jsonl").read_bytes() == before
    assert len((run / "judge_luna.jsonl").read_text().splitlines()) == 3


def test_agent_judge_out_resumes_and_redo(tmp_path, monkeypatch):
    events = _ev(3)
    run = tmp_path / "r"
    _write_run(run, events)
    _jsonl(run / "alt.jsonl", _verdicts(events[:2]))
    monkeypatch.setattr(cli, "LlmJudge", FakeJudge)
    monkeypatch.setattr(cli, "build_text_model", lambda section, default: None)
    assert main(["agent-judge", str(run), "--out", "alt.jsonl"]) == 0
    assert FakeJudge.last.calls == [True]  # só a terceira trajetória
    assert main(["agent-judge", str(run), "--out", "alt.jsonl", "--redo"]) == 0
    assert len(FakeJudge.last.calls) == 3


def test_agent_judge_provider_codex_ignores_config_model(tmp_path, monkeypatch):
    from iac_harness.llm.codex_cli import CodexText

    run = tmp_path / "r"
    _write_run(run, _ev(1))
    (run / "run_config.json").write_text(
        json.dumps({"config": {"judge": {"provider": "anthropic", "model": "claude-x"}}})
    )
    built = []
    monkeypatch.setattr(cli, "LlmJudge", lambda llm: built.append(llm) or FakeJudge())
    assert main(["agent-judge", str(run), "--provider", "codex", "--reasoning-effort", "low",
                 "--out", "o.jsonl"]) == 0  # fmt: skip
    assert isinstance(built[0], CodexText)
    assert built[0].model == "gpt-6-luna" and built[0].reasoning_effort == "low"


def test_agent_metrics_judge_file(tmp_path, capsys):
    events = [_final("t", "none", 1, True, final_text="x", expect="ask")]
    run = tmp_path / "r"
    _write_run(run, events, _verdicts(events, "done"))
    _jsonl(run / "alt.jsonl", _verdicts(events, "ask"))
    main(["agent-metrics", str(run), "--judge-file", "alt.jsonl"])
    alt = json.loads(capsys.readouterr().out)
    main(["agent-metrics", str(run)])
    base = json.loads(capsys.readouterr().out)
    assert alt["n_unjudged"] == 0
    assert alt["by_condition_model"] != base["by_condition_model"]


def test_agent_judge_compare_with_kappa(tmp_path, capsys):
    events = _ev(4)
    ids = [e["trajectory_id"] for e in events]
    run = tmp_path / "r"
    kinds_a = ["ask", "ask", "done", "done"]
    kinds_b = ["ask", "done", "done"]
    _write_run(
        run, events, [{"trajectory_id": i, "kind": k} for i, k in zip(ids, kinds_a, strict=True)]
    )
    rows_b = [{"trajectory_id": i, "kind": k} for i, k in zip(ids, kinds_b, strict=False)]
    _jsonl(run / "b.jsonl", [*rows_b, {"trajectory_id": "so-b", "kind": "ask"}])
    assert main(["agent-judge", str(run), "--compare-with", "b.jsonl"]) == 0
    out = json.loads(capsys.readouterr().out)
    # comuns: 3 ids; a = ask, ask, done; b = ask, done, done -> po = 2/3, pe = 2/3*1/3 + 1/3*2/3
    assert out["n"] == 3 and out["only_in_a"] == 1 and out["only_in_b"] == 1
    assert out["kind"]["k"] == 2 and out["kind"]["kappa"] == pytest.approx(0.4)
    assert out["asked"]["kappa"] == pytest.approx(0.4)
    assert out["asked_confusion"] == {"a_ask__b_ask": 1, "a_ask__b_other": 1, "a_other__b_other": 1}
    assert out["disagreements"] == [
        {"trajectory_id": ids[1], "task_id": "mod-sqs-retention", "condition": "none",
         "model": "Haiku 5.5", "kind_a": "ask", "kind_b": "done"}
    ]  # fmt: skip


def test_judge_pair_agreement_kappa_none_when_pe_is_one():
    from iac_harness.agentic.metrics import judge_pair_agreement

    rows = [{"trajectory_id": "x", "kind": "done"}, {"trajectory_id": "y", "kind": "done"}]
    out = judge_pair_agreement([], rows, rows)
    assert out["kind"]["kappa"] is None and out["asked"]["kappa"] is None
    assert out["kind"]["rate"] == 1.0
