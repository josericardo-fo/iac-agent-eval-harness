import json
import subprocess
from pathlib import Path

import pytest

from iac_harness import cli
from iac_harness.cli import main
from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities import prompts
from iac_harness.modalities.execute import _run_commands, truncate_middle
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.metrics import compute_modality_metrics
from iac_harness.modalities.runner import ModRunContext, run_pair
from iac_harness.modalities.spec import MODALITIES
from iac_harness.toolchain import Toolchain
from iac_harness.types import AgentReply, Artifact, Usage

SECRET = "SEGREDO-DO-CHECK"  # aparece só nos details do check; nunca pode chegar ao agente


def _task(task_id):
    return next(t for t in load_tasks() if t.task_id == task_id)


class ScriptedToolchain(Toolchain):
    """Execução do artefato falha nas primeiras `fail_runs` vezes; o check imprime SECRET."""

    def __init__(
        self, fail_runs=0, error="Error: InvalidParameterValue: boom", check_pass=True, stdout=""
    ):
        super().__init__(mode="local")
        self.fail_runs, self.error, self.check_pass = fail_runs, error, check_pass
        self.stdout = stdout
        self.calls: list[list[str]] = []

    def run(self, args, workdir, timeout_s=None):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["python3", "check.py"]:
            out = json.dumps({"pass": self.check_pass, "details": {"secret": SECRET}})
            return subprocess.CompletedProcess(args, 0, out, "")
        is_artifact = Path(workdir).name.startswith("attempt_")
        last = args in [list(c) for m in MODALITIES.values() for c in m.command[-1:]]
        if is_artifact and last and self.fail_runs > 0:
            self.fail_runs -= 1
            return subprocess.CompletedProcess(args, 1, self.stdout, self.error)
        return subprocess.CompletedProcess(args, 0, "ok", "")


class FakeExec(FlociExecutor):
    def __init__(self, tc):
        super().__init__(tc)
        self.resets = 0

    def reset(self):
        self.resets += 1


class ScriptedBackend:
    """Devolve as respostas na ordem; guarda (task, history) de cada chamada."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, task, history):
        self.calls.append((task, list(history)))
        files = self.replies.pop(0)
        return AgentReply(Artifact(files), Usage(10, 5), "fake", "req", "raw", {"pv": "mod-v1"})


class ListRecorder:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def _run(tmp_path, task_id, modality, replies, **tc_kwargs):
    tc = ScriptedToolchain(**tc_kwargs)
    ex = FakeExec(tc)
    backend = ScriptedBackend(replies)
    rec = ListRecorder()
    ctx = ModRunContext("r", tmp_path, tc, ex, max_attempts=3)
    final = run_pair(_task(task_id), MODALITIES[modality], 1, backend, rec, ctx)
    return final, backend, rec, tc, ex


# ---------- prompts ----------


@pytest.mark.parametrize("name", ["terraform", "cli", "boto3"])
def test_system_prompt_per_modality(name):
    m = MODALITIES[name]
    text = prompts.system_prompt(m)
    assert f'title="{m.filename}"' in text
    assert "Do not set endpoints or credentials" in text
    others = [o.filename for o in MODALITIES.values() if o.name != name]
    assert not any(f"file {o}" in text for o in others)
    if name == "terraform":
        assert "~> 6.0" in text and "Terraform 1.15" in text
    if name == "cli":
        assert "bash -euo pipefail run.sh" in text
    if name == "boto3":
        assert "python3 main.py" in text


def test_modality_blocks_are_similar_length():
    lengths = [len(b) for b in prompts.MODALITY_BLOCKS.values()]
    notes = [len(b) for b in prompts.MODIFY_NOTES.values()]
    assert max(lengths) < 2.5 * min(lengths) and max(notes) < 1.5 * min(notes)


@pytest.mark.parametrize("name", ["terraform", "cli", "boto3"])
def test_user_prompt_includes_initial_only_on_modify(name):
    m = MODALITIES[name]
    create, modify = _task("task-108"), _task("mod-sg-https")
    assert prompts.user_prompt(create, m) == create.prompt
    text = prompts.user_prompt(modify, m)
    assert modify.prompt.strip() in text and modify.initial_tf.strip() in text
    assert prompts.MODIFY_NOTES[name] in text


@pytest.mark.parametrize("name", ["terraform", "cli", "boto3"])
def test_system_prompt_declares_available_tools(name):
    text = prompts.system_prompt(MODALITIES[name])
    assert "Available:" in text
    if name == "cli":
        assert "jq" in text


def test_repair_template_has_only_the_error():
    tpl = prompts.repair_template(MODALITIES["cli"])
    msg = tpl.format(previous_code="X", stage="S", error_class="E", message="the error")
    assert "the error" in msg and 'title="run.sh"' in msg and "X" not in msg


# ---------- runner ----------


def test_success_first_attempt_runs_check_once(tmp_path):
    final, backend, rec, tc, ex = _run(tmp_path, "task-108", "cli", [{"run.sh": "aws ok"}])
    assert final["termination"] == "EXEC_OK" and final["attempts"] == 1
    assert final["check_pass"] is True and final["check_details"] == {"secret": SECRET}
    assert sum(c[:2] == ["python3", "check.py"] for c in tc.calls) == 1
    assert backend.calls[0][1] == []  # tentativa 1 com histórico vazio
    assert ex.resets == 1
    assert [e["event"] for e in rec.events] == ["attempt", "final"]
    att = rec.events[0]
    assert att["exec_ok"] and att["artifact_hash"] and att["input_tokens"] == 10
    assert (Path(att["artifact_path"]) / "run.sh").read_text() == "aws ok"
    assert Path(att["artifact_path"]).parts[-3:] == ("1", "cli", "attempt_1")


def test_repair_gets_execution_error_never_check_details(tmp_path):
    replies = [{"main.py": "v1"}, {"main.py": "v2"}]
    final, backend, rec, tc, ex = _run(tmp_path, "task-230", "boto3", replies, fail_runs=1)
    assert final["termination"] == "EXEC_OK" and final["attempts"] == 2
    (turn,) = backend.calls[1][1]
    assert "InvalidParameterValue: boom" in turn.feedback.message
    assert turn.artifact.files == {"main.py": "v1"}
    dumped = json.dumps([str(c) for c in backend.calls]) + json.dumps(
        [e.get("feedback_sent") for e in rec.events]
    )
    assert SECRET not in dumped
    assert ex.resets == 2  # cada tentativa recomeça do estado limpo


def test_budget_exhausted_then_check_on_last_state(tmp_path):
    replies = [{"run.sh": "a"}, {"run.sh": "b"}, {"run.sh": "c"}]
    final, backend, rec, tc, _ = _run(tmp_path, "task-108", "cli", replies, fail_runs=5,
                                      check_pass=False)  # fmt: skip
    assert final["termination"] == "BUDGET_EXHAUSTED" and final["attempts"] == 3
    assert final["check_pass"] is False
    assert len(backend.calls) == 3
    assert tc.calls[-1][:2] == ["python3", "check.py"]
    assert rec.events[2]["feedback_sent"] is None  # última tentativa: sem repair


def test_format_failure_becomes_repair_with_clear_message(tmp_path):
    replies = [{"main.tf": "wrong file"}, {"run.sh": "aws ok"}]
    final, backend, rec, tc, _ = _run(tmp_path, "task-108", "cli", replies)
    assert final["termination"] == "EXEC_OK" and final["attempts"] == 2
    (turn,) = backend.calls[1][1]
    assert 'title="run.sh"' in turn.feedback.message
    assert turn.raw_text == "raw"  # o histórico mostra a resposta original
    assert rec.events[0]["format_ok"] is False and not rec.events[0]["exec_ok"]
    assert sum(c[0] == "bash" for c in tc.calls) == 1  # só a tentativa 2 executou


def test_format_failure_on_all_attempts(tmp_path):
    final, *_ = _run(tmp_path, "task-108", "boto3", [{}, {}, {}])
    assert final["termination"] == "FORMAT_FAIL" and final["attempts"] == 3


def test_unsupported_gets_repair(tmp_path):
    final, backend, rec, *_ = _run(
        tmp_path, "task-234", "cli", [{"run.sh": "a"}, {"run.sh": "b"}], fail_runs=1,
        error="An error occurred (NotImplemented) when calling CreateVpc",
    )  # fmt: skip
    assert final["termination"] == "EXEC_OK" and final["attempts"] == 2
    (turn,) = backend.calls[1][1]
    assert "NotImplemented" in turn.feedback.message
    assert rec.events[0]["unsupported"] is True and rec.events[0]["termination"] is None


def test_long_output_keeps_error_at_the_end(tmp_path):
    error = "Error: InvalidParameterValue: the real error"
    tc = ScriptedToolchain(fail_runs=1, error=error, stdout="x" * 10_000)
    workdir = tmp_path / "attempt_1"
    workdir.mkdir()
    result = _run_commands(tc, MODALITIES["cli"].command, workdir, 2000)
    assert not result.ok
    assert error in result.output and "characters omitted" in result.output
    assert result.output.startswith("exit code 1") and len(result.output) < 2100
    assert error in result.raw and len(result.raw) > 10_000


def test_truncate_middle_keeps_short_text():
    assert truncate_middle("short", 2000) == "short"


def test_repair_feedback_keeps_error_of_long_output(tmp_path):
    error = "Error: InvalidParameterValue: the real error"
    replies = [{"run.sh": "a"}, {"run.sh": "b"}]
    final, backend, rec, *_ = _run(tmp_path, "task-108", "cli", replies, fail_runs=1,
                                   error=error, stdout="x" * 10_000)  # fmt: skip
    assert final["termination"] == "EXEC_OK" and final["attempts"] == 2
    (turn,) = backend.calls[1][1]
    msg = turn.feedback.message
    assert error in msg and "characters omitted" in msg and "exit code" in msg
    assert rec.events[0]["feedback_sent"] == msg


def test_exec_fail_with_state_pass_is_not_success(tmp_path):
    replies = [{"run.sh": "a"}, {"run.sh": "b"}, {"run.sh": "c"}]
    final, _, rec, *_ = _run(tmp_path, "task-108", "cli", replies, fail_runs=5)
    assert final["termination"] == "BUDGET_EXHAUSTED"
    assert final["check_pass"] is True and final["exec_ok"] is False
    assert final["success"] is False
    assert rec.events[0]["raw_output"]


def test_modify_terraform_applies_initial_each_attempt_and_inherits(tmp_path):
    replies = [{"main.tf": "v1"}, {"main.tf": "v2"}]
    final, backend, rec, tc, ex = _run(tmp_path, "mod-dynamodb-pitr", "terraform", replies,
                                       fail_runs=1)  # fmt: skip
    assert final["termination"] == "EXEC_OK"
    pair = tmp_path / "r" / "mod-dynamodb-pitr" / "1" / "terraform"
    assert {p.name for p in pair.iterdir()} == {
        "initial_1", "initial_2", "attempt_1", "attempt_2",
    }  # fmt: skip
    task = backend.calls[0][0]
    assert _task("mod-dynamodb-pitr").initial_tf.strip() in task.prompt


def test_initial_failure_is_harness_failure(tmp_path):
    class BrokenInitial(ScriptedToolchain):
        def run(self, args, workdir, timeout_s=None):
            if Path(workdir).name.startswith("initial"):
                return subprocess.CompletedProcess(args, 1, "", "Error: boom")
            return super().run(args, workdir, timeout_s)

    tc = BrokenInitial()
    backend = ScriptedBackend([])
    rec = ListRecorder()
    ctx = ModRunContext("r", tmp_path, tc, FakeExec(tc))
    final = run_pair(_task("mod-sg-https"), MODALITIES["cli"], 1, backend, rec, ctx)
    assert final["termination"] == "INITIAL_FAIL" and not final["check_pass"]
    assert backend.calls == []


# ---------- métricas ----------


def _final(modality, category, passed, attempts, tokens, termination, success=None):
    return {
        "event": "final", "modality": modality, "category": category, "check_pass": passed,
        "success": passed if success is None else success,
        "attempts": attempts, "input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens,
        "termination": termination,
    }  # fmt: skip


def test_modality_metrics_synthetic():
    events = [
        _final("cli", "create", True, 1, 100, "EXEC_OK"),
        _final("cli", "create", False, 3, 300, "BUDGET_EXHAUSTED"),
        _final("cli", "modify", True, 2, 200, "EXEC_OK"),
        _final("cli", "modify", True, 3, 200, "BUDGET_EXHAUSTED", success=False),
        _final("terraform", "create", False, 1, 50, "EXEC_OK"),
        {"event": "attempt", "modality": "cli", "category": "create"},
    ]
    m = compute_modality_metrics(events)
    assert m["cli"]["create"]["success"] == "1/2"
    assert m["cli"]["create"]["mean_attempts"] == 2
    assert m["cli"]["create"]["mean_total_tokens"] == 200
    assert m["cli"]["create"]["termination"] == {"BUDGET_EXHAUSTED": 1, "EXEC_OK": 1}
    assert m["cli"]["modify"]["success"] == "1/2" and m["cli"]["modify"]["success_rate"] == 0.5
    assert m["cli"]["modify"]["state_pass"] == "2/2"
    assert m["cli"]["modify"]["state_pass_rate"] == 1.0
    assert m["terraform"]["create"]["success"] == "0/1"


# ---------- resume ----------


def test_modality_resume_skips_completed(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "run1"
    run_dir.mkdir(parents=True)
    events = [
        {"task_id": "task-108", "modality": "cli", "rep": 1, "event": "attempt"},
        {
            "task_id": "task-108",
            "modality": "cli",
            "rep": 1,
            "event": "final",
            **_final("cli", "create", True, 1, 10, "EXEC_OK"),
        },
        {"task_id": "task-108", "modality": "boto3", "rep": 1, "event": "attempt"},
    ]
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    orphan = run_dir / "task-108" / "1" / "boto3" / "attempt_1"
    orphan.mkdir(parents=True)
    cfg = {"run": {"name": "x"}, "agent": {"provider": "anthropic", "model": "m"}}
    (run_dir / "run_config.json").write_text(
        json.dumps({"config": cfg, "reps": 1, "tasks": ["task-108"],
                    "modalities": ["cli", "boto3"]})
    )  # fmt: skip
    ran = []
    monkeypatch.setattr(
        cli, "_modality_loop",
        lambda cfg, tasks, mods, reps, rd, rid, skip=frozenset(): ran.append(
            [(t.task_id, m, r) for r in range(1, reps + 1) for t in tasks for m in mods
             if (t.task_id, m, r) not in skip]
        ),
    )  # fmt: skip
    monkeypatch.setattr(cli, "_git_provenance", lambda: {"git_commit": "abc", "git_dirty": False})

    assert main(["modality-run", "--resume", str(run_dir)]) == 0

    assert ran == [[("task-108", "boto3", 1)]]
    assert not orphan.parent.exists()  # unidade incompleta: diretório removido
    kept = [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines()]
    assert kept == events[:2]
    assert json.loads((run_dir / "run_config.json").read_text())["resumes"][0]["skipped_pairs"] == 1


def test_modality_resume_rejects_run_options(tmp_path):
    with pytest.raises(SystemExit, match="--resume não aceita"):
        main(["modality-run", "--resume", str(tmp_path), "--reps", "2"])
