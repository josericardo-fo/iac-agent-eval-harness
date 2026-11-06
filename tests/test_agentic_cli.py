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

    def __init__(self, model=None):
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
