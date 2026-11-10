import json

import pytest

from fakes import FakeBackend, FakeExecutor, FakeVerifier
from iac_harness import cli
from iac_harness.cli import discard_incomplete, main, pair_completion
from iac_harness.runner import SandboxGate
from iac_harness.types import Stage, Task, TaskOracle
from iac_harness.workspace import pair_dir

CONDITIONS = ["A", "B"]


def ev(task_id, rep, condition, event):
    return {"task_id": task_id, "rep": rep, "condition": condition, "event": event}


def synthetic_events():
    return [
        # t1 rep 1: completo
        ev("aws/t1", 1, "A", "attempt"),
        ev("aws/t1", 1, "A", "final"),
        ev("aws/t1", 1, "B", "attempt"),
        ev("aws/t1", 1, "B", "final"),
        # t1 rep 2: só A terminou
        ev("aws/t1", 2, "A", "attempt"),
        ev("aws/t1", 2, "A", "final"),
        ev("aws/t1", 2, "B", "attempt"),
        # t2 rep 1: só tentativa
        ev("aws/t2", 1, "A", "attempt"),
    ]


def write_events(run_dir, events):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_pair_completion():
    complete, incomplete = pair_completion(synthetic_events(), CONDITIONS)
    assert complete == {("aws/t1", 1)}
    assert incomplete == {("aws/t1", 2), ("aws/t2", 1)}


def test_pair_completion_agent_unit_counts_any_final():
    # Agente: a condição faz parte da unidade; um `final` com condition="none" completa.
    fields = ("task_id", "condition", "model", "rep")
    events = [
        {**ev("t1", 1, "none", "final"), "model": "Haiku"},
        {**ev("t2", 1, "own-rules", "attempt"), "model": "Haiku"},
    ]
    complete, incomplete = pair_completion(events, None, fields)
    assert complete == {("t1", "none", "Haiku", 1)}
    assert incomplete == {("t2", "own-rules", "Haiku", 1)}


def test_discard_incomplete_moves_events_and_removes_dirs(tmp_path):
    run_dir = tmp_path / "run1"
    write_events(run_dir, synthetic_events())
    for task_id, rep in [("aws/t1", 1), ("aws/t1", 2), ("aws/t2", 1)]:
        d = pair_dir(tmp_path, "run1", task_id, rep) / "A" / "final"
        d.mkdir(parents=True)
        (d / "main.tf").write_text("#")

    complete, discarded = discard_incomplete(tmp_path, "run1", CONDITIONS)

    assert complete == {("aws/t1", 1)}
    assert discarded == {("aws/t1", 2), ("aws/t2", 1)}
    kept = read_events(run_dir / "events.jsonl")
    assert kept == synthetic_events()[:4]
    moved = read_events(run_dir / "events.discarded.jsonl")
    assert moved == synthetic_events()[4:]
    assert (pair_dir(tmp_path, "run1", "aws/t1", 1) / "A" / "final" / "main.tf").exists()
    assert not pair_dir(tmp_path, "run1", "aws/t1", 2).exists()
    assert not pair_dir(tmp_path, "run1", "aws/t2", 1).exists()


def test_discard_appends_to_existing_discarded(tmp_path):
    run_dir = tmp_path / "run1"
    write_events(run_dir, synthetic_events())
    (run_dir / "events.discarded.jsonl").write_text(json.dumps(ev("old", 1, "A", "attempt")) + "\n")
    discard_incomplete(tmp_path, "run1", CONDITIONS)
    moved = read_events(run_dir / "events.discarded.jsonl")
    assert moved[0]["task_id"] == "old" and len(moved) == 5


@pytest.mark.parametrize(
    "extra",
    [
        ["--config", "x.toml"],
        ["--tasks", "aws/t1"],
        ["--reps", "2"],
        ["--reps", "0"],
        ["--limit", "0"],
        ["--prevalidation", "pre.jsonl"],
        ["--no-prevalidation"],
    ],
)
def test_resume_rejects_run_defining_options(tmp_path, extra):
    with pytest.raises(SystemExit, match="--resume não aceita"):
        main(["run", "--resume", str(tmp_path), *extra])


def test_run_without_config_or_resume_fails():
    with pytest.raises(SystemExit, match="--config"):
        main(["run", "--no-prevalidation"])


class FakeAdapter:
    version = "rev-fake"

    def load(self):
        return [(Task(t, "prompt", 1), TaskOracle({}, "")) for t in ("aws/t1", "aws/t2", "aws/t3")]


def test_resume_end_to_end(tmp_path, monkeypatch):
    executor = FakeExecutor()
    executor.override_for = lambda files: None
    gates = {
        Stage.VALIDATE: FakeVerifier(Stage.VALIDATE),
        Stage.PLAN: FakeVerifier(Stage.PLAN),
        Stage.OPA: FakeVerifier(Stage.OPA),
        Stage.SANDBOX: SandboxGate(executor),
    }
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_build_run", lambda cfg: (None, executor, gates, FakeAdapter()))
    monkeypatch.setattr(cli, "_build_backend", lambda cfg: backend)
    monkeypatch.setattr(cli, "_git_provenance", lambda: {"git_commit": "abc", "git_dirty": False})

    run_dir = tmp_path / "runs" / "run1"
    cfg = {"run": {"conditions": CONDITIONS}, "agent": {"provider": "fake"}}
    write_events(run_dir, synthetic_events())
    with (run_dir / "events.jsonl").open("a") as fh:
        fh.write('{"task_id": "aws/t2"')  # última linha truncada pela queda
    (run_dir / "run_config.json").write_text(
        json.dumps(
            {
                "config": cfg,
                "reps": 2,
                "tasks": ["aws/t1", "aws/t2"],
                "prevalidation": None,
                "prevalidation_sha256": None,
                "no_prevalidation": True,
            }
        )
    )
    # diretório órfão do par incompleto: precisa sumir para create_workspace não falhar
    (pair_dir(tmp_path / "runs", "run1", "aws/t1", 2) / "A" / "final").mkdir(parents=True)

    assert main(["run", "--resume", str(run_dir)]) == 0

    events = read_events(run_dir / "events.jsonl")
    complete, incomplete = pair_completion(events, CONDITIONS)
    assert complete == {("aws/t1", 1), ("aws/t1", 2), ("aws/t2", 1), ("aws/t2", 2)}
    assert not incomplete
    assert events[:4] == synthetic_events()[:4]  # par completo intacto
    assert backend.calls == 3  # uma geração compartilhada por par rodado (tudo passa)
    saved = json.loads((run_dir / "run_config.json").read_text())
    (resume,) = saved["resumes"]
    assert resume["skipped_pairs"] == 1 and resume["discarded_pairs"] == 2
    assert resume["git_commit"] == "abc" and resume["timestamp"]
    discarded = (run_dir / "events.discarded.jsonl").read_text().splitlines()
    assert discarded[-1] == '{"task_id": "aws/t2"'


def test_resume_aborts_when_prevalidation_changed(tmp_path):
    pre = tmp_path / "pre.jsonl"
    pre.write_text('{"task_id": "aws/t1"}\n')
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "run_config.json").write_text(
        json.dumps(
            {
                "config": {},
                "reps": 1,
                "tasks": [],
                "prevalidation": str(pre),
                "prevalidation_sha256": "0" * 64,
                "no_prevalidation": False,
            }
        )
    )
    with pytest.raises(SystemExit, match="mudou"):
        main(["run", "--resume", str(run_dir)])


def test_discard_moves_truncated_last_line(tmp_path, capsys):
    run_dir = tmp_path / "run1"
    write_events(run_dir, synthetic_events())
    with (run_dir / "events.jsonl").open("a") as fh:
        fh.write('{"task_id": "aws/t2", "rep"')  # queda no meio da gravação

    complete, discarded = discard_incomplete(tmp_path, "run1", CONDITIONS)

    assert complete == {("aws/t1", 1)}
    assert discarded == {("aws/t1", 2), ("aws/t2", 1)}
    assert read_events(run_dir / "events.jsonl") == synthetic_events()[:4]
    lines = (run_dir / "events.discarded.jsonl").read_text().splitlines()
    assert lines[-1] == '{"task_id": "aws/t2", "rep"'
    assert [json.loads(ln) for ln in lines[:-1]] == synthetic_events()[4:]
    assert "truncada" in capsys.readouterr().err


def test_discard_aborts_on_invalid_line_in_the_middle(tmp_path):
    run_dir = tmp_path / "run1"
    write_events(run_dir, synthetic_events())
    text = (run_dir / "events.jsonl").read_text().splitlines()
    text.insert(2, "{lixo")
    (run_dir / "events.jsonl").write_text("\n".join(text) + "\n")
    with pytest.raises(SystemExit, match="corrompido"):
        discard_incomplete(tmp_path, "run1", CONDITIONS)
    assert not (run_dir / "events.discarded.jsonl").exists()


def test_discard_is_idempotent_after_crash_before_replace(tmp_path):
    run_dir = tmp_path / "run1"
    write_events(run_dir, synthetic_events())
    # simula queda depois do append no discarded e antes do replace do events.jsonl
    (run_dir / "events.discarded.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in synthetic_events()[4:])
    )
    discard_incomplete(tmp_path, "run1", CONDITIONS)
    assert read_events(run_dir / "events.discarded.jsonl") == synthetic_events()[4:]
    assert read_events(run_dir / "events.jsonl") == synthetic_events()[:4]


def test_run_with_invalid_condition_leaves_no_run_dir(tmp_path, monkeypatch):
    config = tmp_path / "cfg.toml"
    runs_dir = tmp_path / "runs"
    config.write_text(
        f"""
[run]
name = "x"
conditions = ["Z"]
runs_dir = "{runs_dir}"

[dataset]
revision = "rev"

[agent]
provider = "anthropic"
model = "m"
"""
    )
    monkeypatch.setattr(cli, "_build_run", lambda cfg: (None, None, {}, FakeAdapter()))

    def no_backend(cfg):
        raise AssertionError("backend não deveria ser montado")

    monkeypatch.setattr(cli, "_build_backend", no_backend)
    with pytest.raises(KeyError):
        main(["run", "--config", str(config), "--no-prevalidation"])
    with pytest.raises(SystemExit, match="tarefas não encontradas"):
        main(["run", "--config", str(config), "--no-prevalidation", "--tasks", "aws/nada"])
    assert not runs_dir.exists()


def _agent_resume_setup(tmp_path, monkeypatch):
    """Run de agente com t1 completa e t2 órfã (diretório sem nenhum evento)."""
    from types import SimpleNamespace

    from iac_harness.agentic.runner import unit_dir

    runs_dir, run_id = tmp_path / "runs", "r1"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    cfg = {"agentic": {"models": ["M"], "conditions": ["none"]}}
    (run_dir / "run_config.json").write_text(
        json.dumps({"config": cfg, "reps": 1, "tasks": ["t1", "t2"]})
    )
    final = {
        "event": "final", "task_id": "t1", "condition": "none", "model": "M", "rep": 1,
        "trajectory_id": "t1|none|M|1",
    }  # fmt: skip
    (run_dir / "events.jsonl").write_text(json.dumps(final) + "\n")
    done = unit_dir(runs_dir, run_id, "t1", "none", "M", 1)
    (done / "work").mkdir(parents=True)
    (done / "work" / "answer.txt").write_text("ok")
    orphan = unit_dir(runs_dir, run_id, "t2", "none", "M", 1)
    (orphan / "work" / ".terraform").mkdir(parents=True)
    (orphan / "work" / ".terraform" / "provider").write_text("x")
    # Como os arquivos de container (outro dono): o rmtree do host não consegue apagar.
    (orphan / "work" / ".terraform").chmod(0o500)

    tasks = [SimpleNamespace(task_id="t1"), SimpleNamespace(task_id="t2")]
    ran: list[list] = []
    monkeypatch.setattr(cli, "_select_modality_run", lambda *a, **k: (tasks, None))
    monkeypatch.setattr(cli, "_build_agent", lambda cfg: None)
    monkeypatch.setattr(cli, "_build_targets", lambda cfg: [])
    monkeypatch.setattr(cli, "_agent_loop", lambda cfg, plan, *a, **k: ran.append(plan))
    monkeypatch.setattr(cli, "_agent_summary", lambda events: "")
    return run_dir, done, orphan, ran


def test_agent_resume_discards_orphan_unit_dir_and_reruns_only_it(tmp_path, monkeypatch, capsys):
    run_dir, done, orphan, ran = _agent_resume_setup(tmp_path, monkeypatch)
    try:
        assert main(["agent-resume", str(run_dir)]) == 0
    finally:
        for p in run_dir.rglob(".terraform"):
            p.chmod(0o700)
    assert done.exists()  # a unidade completa fica intacta
    assert not orphan.exists()  # liberado: o worker pode recriar com exist_ok=False
    assert [(t.task_id, c, m, r) for t, c, m, r in ran[0]] == [("t2", "none", "M", 1)]
    moved = list((run_dir / "discarded").rglob("provider"))  # evidência preservada
    assert len(moved) == 1
    assert "1 incompletos descartados" in capsys.readouterr().err
    saved = json.loads((run_dir / "run_config.json").read_text())
    assert saved["resumes"][-1]["discarded_units"] == 1
