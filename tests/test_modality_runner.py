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
    result = _run_commands(
        tc, FlociExecutor.classify_error, MODALITIES["cli"].command, workdir, 2000
    )
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


def _final(
    modality, category, passed, attempts, tokens, termination, success=None, task_id="t1", rep=1
):
    return {
        "event": "final", "task_id": task_id, "rep": rep, "model_id": "m",
        "modality": modality, "category": category, "check_pass": passed,
        "success": passed if success is None else success,
        "attempts": attempts, "input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens,
        "termination": termination,
    }  # fmt: skip


def _attempt(modality, exec_ok, task_id="t1", rep=1, attempt=1, unsupported=False):
    return {
        "event": "attempt", "task_id": task_id, "rep": rep, "modality": modality,
        "attempt": attempt, "exec_ok": exec_ok, "unsupported": unsupported,
    }  # fmt: skip


def test_modality_metrics_synthetic():
    events = [
        _final("cli", "create", True, 1, 100, "EXEC_OK", task_id="a"),
        _final("cli", "create", False, 3, 300, "BUDGET_EXHAUSTED", task_id="b"),
        _final("cli", "modify", True, 2, 200, "EXEC_OK", task_id="c"),
        _final("cli", "modify", True, 3, 200, "BUDGET_EXHAUSTED", success=False, task_id="d"),
        _final("terraform", "create", False, 1, 50, "EXEC_OK", task_id="a"),
        _attempt("cli", True, task_id="a"),
    ]
    m = compute_modality_metrics(events, prices={"m": (1.0, 0.0)})
    create = m["modalities"]["cli"]["by_category"]["create"]
    assert (create["success"]["k"], create["success"]["n"]) == (1, 2)
    assert create["mean_attempts"] == 2
    assert create["tokens"]["mean_total"] == 200 and create["tokens"]["total"] == 400
    assert create["cost_usd"] == pytest.approx(400 / 1_000_000)
    assert create["termination"] == {"BUDGET_EXHAUSTED": 1, "EXEC_OK": 1}
    modify = m["modalities"]["cli"]["by_category"]["modify"]
    assert modify["success"]["rate"] == 0.5 and modify["state_pass"]["rate"] == 1.0
    assert m["modalities"]["cli"]["overall"]["success"]["n"] == 4
    assert m["modalities"]["terraform"]["overall"]["success"]["k"] == 0
    assert m["modalities"]["cli"]["overall"]["by_difficulty"] == {}


def test_modality_metrics_first_attempt_success():
    events = [
        _final("cli", "create", True, 1, 10, "EXEC_OK", task_id="a"),
        _final("cli", "create", True, 2, 10, "EXEC_OK", task_id="b"),
        _final("cli", "create", False, 1, 10, "EXEC_OK", task_id="c"),
    ]
    block = compute_modality_metrics(events)["modalities"]["cli"]["overall"]
    assert block["first_attempt_success"]["k"] == 1
    assert block["success"]["k"] == 2
    assert block["attempts_to_success"] == 1.5


def test_modality_metrics_repair_rate_and_unsupported():
    events = [
        # a: falhou a execução e reparou; b: falhou e não reparou; c: nunca falhou
        _attempt("cli", False, task_id="a", unsupported=True),
        _attempt("cli", True, task_id="a", attempt=2),
        _final("cli", "create", True, 2, 10, "EXEC_OK", task_id="a"),
        _attempt("cli", False, task_id="b"),
        _attempt("cli", False, task_id="b", attempt=2, unsupported=True),
        _final("cli", "create", False, 2, 10, "BUDGET_EXHAUSTED", task_id="b"),
        _attempt("cli", True, task_id="c"),
        _final("cli", "create", True, 1, 10, "EXEC_OK", task_id="c"),
    ]
    block = compute_modality_metrics(events)["modalities"]["cli"]["overall"]
    assert (block["repair_rate"]["k"], block["repair_rate"]["n"]) == (1, 2)
    assert block["unsupported_attempts"] == 2


def test_modality_metrics_paired_mcnemar():
    # 10 pares (task, rep): terraform acerta todos; cli erra 6 -> McNemar b=6, c=0
    events = []
    for i in range(10):
        events.append(_final("terraform", "create", True, 1, 10, "EXEC_OK", task_id=f"t{i}"))
        events.append(_final("cli", "create", i >= 6, 1, 30, "EXEC_OK", task_id=f"t{i}"))
    cmp = compute_modality_metrics(events, n_boot=200)["comparisons"]
    (tf_cli,) = cmp["overall"]["success"]
    assert tf_cli["pair"] == "terraformxcli" and tf_cli["n_pairs"] == 10
    assert (tf_cli["only_first"], tf_cli["only_second"]) == (6, 0)
    assert tf_cli["mcnemar_p"] == pytest.approx(2 / 2**6)
    assert tf_cli["holm_p"] == tf_cli["mcnemar_p"]  # um único par: Holm não muda
    assert tf_cli["diff_mean"] == pytest.approx(-0.6)
    assert cmp["overall"]["first_attempt_success"][0]["only_first"] == 6
    assert cmp["overall"]["total_tokens"][0]["diff_mean"] == pytest.approx(20)
    assert cmp["by_category"]["create"]["success"][0]["only_first"] == 6


def test_modality_metrics_by_difficulty():
    events = [
        _final("cli", "create", True, 1, 10, "EXEC_OK", task_id="task-108"),
        _final("cli", "create", False, 1, 10, "EXEC_OK", task_id="task-375"),
        _final("cli", "create", True, 1, 10, "EXEC_OK", task_id="authored-x"),
    ]
    m = compute_modality_metrics(events, difficulty={"task-108": 2, "task-375": 4})
    by_d = m["modalities"]["cli"]["overall"]["by_difficulty"]
    assert set(by_d) == {"2", "4"}
    assert (by_d["2"]["k"], by_d["4"]["k"]) == (1, 0)


# ---------- resume ----------


def test_modality_resume_skips_completed(tmp_path, monkeypatch):
    run_dir = tmp_path / "runs" / "run1"
    run_dir.mkdir(parents=True)
    events = [
        _attempt("cli", True, task_id="task-108"),
        {
            "task_id": "task-108",
            "modality": "cli",
            "rep": 1,
            "event": "final",
            **_final("cli", "create", True, 1, 10, "EXEC_OK", task_id="task-108"),
        },
        _attempt("boto3", True, task_id="task-108"),
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
        lambda cfg, tasks, mods, reps, rd, rid, tc, ex, skip=frozenset(): ran.append(
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


# ---------- consulta ----------


@pytest.mark.parametrize("name", ["terraform", "cli", "boto3"])
def test_query_prompt_has_note_without_initial(name):
    task = _task("query-sg-ssh-open")
    text = prompts.user_prompt(task, MODALITIES[name])
    assert task.prompt.strip() in text and prompts.QUERY_NOTES[name] in text
    assert "resource " not in text and "query-bastion-sg" not in text


def test_query_notes_are_similar_length():
    notes = [len(n) for n in prompts.QUERY_NOTES.values()]
    assert max(notes) < 1.2 * min(notes)


def test_query_missing_answer_is_repaired_with_fixed_message(tmp_path):
    class AnswerToolchain(ScriptedToolchain):
        """A 2ª execução de run.sh grava answer.json; a 1ª não."""

        runs = 0

        def run(self, args, workdir, timeout_s=None):
            if args[0] == "bash":
                self.runs += 1
                if self.runs == 2:
                    (Path(workdir) / "answer.json").write_text('{"security_groups": []}')
            return super().run(args, workdir, timeout_s)

    tc = AnswerToolchain()
    backend = ScriptedBackend([{"run.sh": "a"}, {"run.sh": "b"}])
    rec = ListRecorder()
    ctx = ModRunContext("r", tmp_path, tc, FakeExec(tc), max_attempts=3)
    final = run_pair(_task("query-sg-ssh-open"), MODALITIES["cli"], 1, backend, rec, ctx)
    assert final["termination"] == "EXEC_OK" and final["attempts"] == 2
    (turn,) = backend.calls[1][1]
    assert turn.feedback.message == "answer.json was not produced or is not valid JSON."
    assert rec.events[0]["answer"] is None and not rec.events[0]["exec_ok"]
    assert rec.events[1]["answer"] == '{"security_groups": []}'


def test_query_terraform_does_not_inherit_tfstate(tmp_path):
    class StateToolchain(ScriptedToolchain):
        def run(self, args, workdir, timeout_s=None):
            if args[:2] == ["terraform", "apply"]:
                state = '{"outputs": {"answer": {"value": {"security_groups": []}}}}'
                (Path(workdir) / "terraform.tfstate").write_text(state)
            return super().run(args, workdir, timeout_s)

    tc = StateToolchain()
    backend = ScriptedBackend([{"main.tf": "x"}])
    ctx = ModRunContext("r", tmp_path, tc, FakeExec(tc), max_attempts=3)
    final = run_pair(_task("query-sg-ssh-open"), MODALITIES["terraform"], 1, backend,
                     ListRecorder(), ctx)  # fmt: skip
    assert final["termination"] == "EXEC_OK"
    attempt = tmp_path / "r" / "query-sg-ssh-open" / "1" / "terraform" / "attempt_1"
    assert json.loads((attempt / "terraform.tfstate").read_text())["outputs"]  # do próprio apply
    assert (attempt / "answer.json").exists()
