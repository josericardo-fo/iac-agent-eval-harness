"""E13: casos reais completos; setup.py, tfstate no check e check final da referência."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities.execute import (
    ExecResult,
    apply_initial,
    prevalidate_pair,
    run_check,
    run_setup,
)
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.spec import MODALITIES
from test_modalities import RecordingToolchain

REAL = {
    "real-ssh-vpn-ip": "ask",
    "real-charges-after-delete": "act",
    "real-s3-delete-versions": "ask",
    "real-s3-lifecycle-versioned": "act",
    "real-s3-public-first-apply": "act",
    "real-tf-import-bucket": "act",
    "real-sg-console-drift": "ask",
    "real-dynamodb-empty-table": "act",
    "real-ssm-securestring-cost": "act",
}
WITH_SETUP = {"real-s3-delete-versions", "real-sg-console-drift", "real-dynamodb-empty-table"}


def _task(task_id: str):
    return next(t for t in load_tasks() if t.task_id == task_id)


def test_real_tasks_load_with_prompt_card_and_expect():
    tasks = {t.task_id: t for t in load_tasks() if t.task_id.startswith("real-")}
    assert set(tasks) == set(REAL)
    for task_id, task in tasks.items():
        assert task.origin == "real-world" and task.expect == REAL[task_id]
        assert task.prompt.strip() and task.user_card and task.user_card.strip()
        assert (task.check_final is not None) == (task.expect == "ask")  # só os `ask`
        assert (task.setup is not None) == (task_id in WITH_SETUP)
        assert set(task.references) == set(MODALITIES) - set(task.missing_references)


def test_real_tasks_have_fixed_shapes():
    assert _task("real-charges-after-delete").category == "query"
    assert _task("real-s3-public-first-apply").category == "create"
    assert _task("real-tf-import-bucket").hide_initial  # o /work não tem o bucket no estado


def test_real_tasks_are_in_strata_and_config():
    import tomllib

    root = Path(__file__).parent.parent
    strata = tomllib.loads((root / "configs" / "strata.toml").read_text())["tasks"]
    config = tomllib.loads((root / "configs" / "e13-real-aws.toml").read_text())
    assert set(config["modality"]["tasks"]) == set(REAL)
    for task_id in REAL:
        assert strata[task_id]["origin"] == "real-world"
    assert config["user_sim"]["max_replies"] == 1 and config["run"]["reps"] == 3
    assert config["agentic"]["conditions"] == ["none", "own-rules-v2"]


# ---------- setup.py ----------


class SetupToolchain(RecordingToolchain):
    def __init__(self, setup_rc=0):
        super().__init__(stdout='{"pass": true, "details": {}}')
        self.setup_rc = setup_rc

    def run(self, args, workdir, timeout_s=None):
        rc = self.setup_rc if args[:2] == ["python3", "setup.py"] else 0
        files = {p.name for p in Path(workdir).iterdir()}
        self.seen.append((list(args), Path(workdir), files))
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, rc, "", "boom" if rc else "")


def test_run_setup_runs_script_alone_in_a_temp_dir():
    tc = SetupToolchain()
    task = replace(_task("real-sg-console-drift"), setup="print('x')")
    initial = ExecResult(True, "ok", raw="raw")
    assert run_setup(tc, task, initial) is initial
    args, workdir, files = tc.seen[0]
    assert args == ["python3", "setup.py", "us-east-1"] and files == {"setup.py"}


def test_run_setup_failure_becomes_initial_failure():
    tc = SetupToolchain(setup_rc=1)
    task = replace(_task("real-sg-console-drift"), setup="raise SystemExit(1)")
    result = run_setup(tc, task, ExecResult(True, "ok", raw="raw"))
    assert not result.ok and "setup.py falhou" in result.output and "boom" in result.output


@pytest.mark.parametrize("setup", [None, "print('x')"])
def test_run_setup_skipped_without_script_or_after_failed_apply(setup):
    tc = SetupToolchain()
    task = replace(_task("real-sg-console-drift"), setup=setup)
    failed = ExecResult(False, "apply falhou")
    assert run_setup(tc, task, failed) is failed
    if setup is None:
        ok = ExecResult(True, "ok")
        assert run_setup(tc, task, ok) is ok
    assert tc.seen == []


def test_apply_initial_runs_setup_after_terraform(tmp_path):
    tc = SetupToolchain()
    task = _task("real-dynamodb-empty-table")
    result, _ = apply_initial(tc, FlociExecutor(tc), task, MODALITIES["cli"], tmp_path, "r")
    assert result.ok
    assert tc.calls[-1] == ["python3", "setup.py", "us-east-1"]
    assert tc.calls[0][:2] == ["terraform", "init"]


def test_prevalidate_stops_when_setup_fails(tmp_path):
    tc = SetupToolchain(setup_rc=1)
    row = prevalidate_pair(
        _task("real-sg-console-drift"), MODALITIES["cli"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert row["initial_ok"] is False and row["baseline_pass"] is None and not row["valid"]


# ---------- terraform.tfstate no check ----------


def test_run_check_receives_workspace_tfstate(tmp_path):
    (tmp_path / "terraform.tfstate").write_text('{"resources": []}')
    tc = RecordingToolchain(stdout='{"pass": true, "details": {}}')
    run_check(tc, _task("real-tf-import-bucket"), tmp_path)
    assert tc.seen[0][2] == {"check.py", "terraform.tfstate"}


def test_run_check_without_tfstate_sends_only_the_check():
    tc = RecordingToolchain(stdout='{"pass": false, "details": {}}')
    run_check(tc, _task("real-tf-import-bucket"))
    assert tc.seen[0][2] == {"check.py"}


# ---------- check final da referência ----------


@pytest.mark.parametrize(("final", "valid"), [(True, True), (False, False)])
def test_prevalidate_ask_reference_must_pass_check_final(tmp_path, final, valid):
    # baseline passa, referência reprova no check do turno 1, e passa/reprova no final.
    outputs = iter(json.dumps({"pass": v, "details": {}}) for v in (True, False, final))

    class CheckToolchain(RecordingToolchain):
        def run(self, args, workdir, timeout_s=None):
            self.stdout = next(outputs) if args[:2] == ["python3", "check.py"] else ""
            return super().run(args, workdir, timeout_s)

    tc = CheckToolchain()
    task = replace(_task("amb-del-sqs"), check_final="print('final')")
    row = prevalidate_pair(task, MODALITIES["cli"], tc, FlociExecutor(tc), tmp_path, "r")
    assert row["ref_final_pass"] is final and row["valid"] is valid
