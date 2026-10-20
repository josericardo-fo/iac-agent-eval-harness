import subprocess
from pathlib import Path

import pytest

from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities.execute import (
    parse_check_output,
    prevalidate_pair,
    run_artifact,
    run_check,
)
from iac_harness.modalities.loader import TASKS_DIR, load_task, load_tasks
from iac_harness.modalities.spec import MODALITIES
from iac_harness.workspace import OVERRIDE_FILENAME
from test_floci import FakeToolchain

EXPECTED_TASKS = {
    "task-108", "task-115", "task-230", "task-231", "task-234",
    "mod-s3-versioning", "mod-dynamodb-pitr", "mod-sg-https",
}  # fmt: skip


class RecordingToolchain(FakeToolchain):
    """Registra (args, workdir, arquivos no workdir) e devolve a saída configurada."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        super().__init__(returncode, stderr)
        self.stdout = stdout
        self.seen: list[tuple[list[str], Path, set[str]]] = []

    def run(self, args, workdir, timeout_s=None):
        files = {p.name for p in Path(workdir).iterdir()}
        self.seen.append((list(args), Path(workdir), files))
        self.calls.append(list(args))
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def test_load_tasks_has_all_eight_with_required_files():
    tasks = load_tasks()
    assert {t.task_id for t in tasks} == EXPECTED_TASKS
    for t in tasks:
        assert t.prompt.strip() and t.check.strip()
        assert set(t.references) == set(MODALITIES)
        assert all(body.strip() for body in t.references.values())
        assert (t.category == "modify") == (t.initial_tf is not None)
        assert "## Intent" not in t.prompt
        assert "terraform" not in t.prompt.lower()


def test_load_task_rejects_missing_initial_in_modify(tmp_path):
    src = TASKS_DIR / "mod-s3-versioning"
    dst = tmp_path / "mod-s3-versioning"
    dst.mkdir()
    for p in src.iterdir():
        if p.name != "initial.tf":
            (dst / p.name).write_text(p.read_text())
    with pytest.raises(ValueError, match="initial.tf"):
        load_task(dst)


def test_modality_commands():
    assert MODALITIES["cli"].command == (("bash", "-euo", "pipefail", "run.sh"),)
    assert MODALITIES["boto3"].command == (("python3", "main.py"),)
    init, apply = MODALITIES["terraform"].command
    assert init[:2] == ("terraform", "init")
    assert apply[:3] == ("terraform", "apply", "-auto-approve")
    assert {m.reference for m in MODALITIES.values()} == {
        "reference.tf", "reference.sh", "reference.py",
    }  # fmt: skip


@pytest.mark.parametrize("name", ["terraform", "cli", "boto3"])
def test_run_artifact_writes_file_and_runs_commands(tmp_path, name):
    tc = RecordingToolchain()
    modality = MODALITIES[name]
    res, path = run_artifact(
        tc, FlociExecutor(tc), modality, "conteúdo", tmp_path, "r", "t", "reference"
    )
    assert res.ok
    assert (path / modality.filename).read_text() == "conteúdo"
    assert (path / OVERRIDE_FILENAME).exists() == (name == "terraform")
    assert [tuple(a) for a, _, _ in tc.seen] == list(modality.command)


def test_run_artifact_stops_at_first_failure_and_flags_unsupported(tmp_path):
    tc = RecordingToolchain(1, stderr="Error: NotImplemented: CreateThing")
    res, _ = run_artifact(
        tc, FlociExecutor(tc), MODALITIES["terraform"], "x", tmp_path, "r", "t", "reference"
    )
    assert not res.ok and res.unsupported
    assert len(tc.seen) == 1  # o apply não roda depois do init falhar


def test_run_artifact_truncates_output(tmp_path):
    tc = RecordingToolchain(1, stderr="e" * 5000)
    res, _ = run_artifact(
        tc, FlociExecutor(tc), MODALITIES["cli"], "x", tmp_path, "r", "t", "reference",
        max_chars=100,
    )  # fmt: skip
    assert len(res.output) < 200 and "characters omitted" in res.output


def test_check_runs_outside_workspace(tmp_path):
    tc = RecordingToolchain(stdout='{"pass": true, "details": {"a": 1}}\n')
    task = load_tasks()[0]
    assert run_check(tc, task) == (True, {"a": 1})
    args, workdir, files = tc.seen[0]
    assert args == ["python3", "check.py", task.region]
    assert files == {"check.py"}  # só o check, nada do artefato
    assert not workdir.is_relative_to(tmp_path)
    assert not workdir.exists()  # diretório temporário removido


def test_check_nonzero_exit_fails_even_with_pass_true():
    tc = RecordingToolchain(1, stdout='{"pass": true, "details": {}}', stderr="boom")
    passed, details = run_check(tc, load_tasks()[0])
    assert not passed and details["returncode"] == 1


@pytest.mark.parametrize(
    "stdout",
    ["", "não é json", '{"details": {}}', '{"pass": "yes"}', "[1, 2]", "Traceback ...\n"],
)
def test_parse_check_output_invalid_is_fail(stdout):
    passed, details = parse_check_output(stdout)
    assert passed is False and "error" in details


def test_parse_check_output_uses_last_line():
    out = 'aviso qualquer\n{"pass": false, "details": {"x": 2}}\n'
    assert parse_check_output(out) == (False, {"x": 2})


def _task(task_id: str):
    return next(t for t in load_tasks() if t.task_id == task_id)


def test_terraform_modify_inherits_tfstate_from_initial(tmp_path):
    class StatefulToolchain(RecordingToolchain):
        """O apply grava um tfstate no workspace, como o Terraform de verdade."""

        def run(self, args, workdir, timeout_s=None):
            if args[:2] == ["terraform", "apply"]:
                (Path(workdir) / "terraform.tfstate").write_text(f"state:{Path(workdir).name}")
            if args[:2] == ["terraform", "init"]:
                (Path(workdir) / ".terraform.lock.hcl").write_text("lock")
            return super().run(args, workdir, timeout_s)

    tc = StatefulToolchain(stdout='{"pass": false, "details": {}}')
    task = _task("mod-s3-versioning")
    row = prevalidate_pair(task, MODALITIES["terraform"], tc, FlociExecutor(tc), tmp_path, "r")
    assert row["initial_ok"] and row["ref_exec_ok"]
    ref_init = next(s for s in tc.seen if s[0][:2] == ["terraform", "init"] and
                    s[1].name == "reference")  # fmt: skip
    assert {"terraform.tfstate", ".terraform.lock.hcl"} <= ref_init[2]
    ref_dir = ref_init[1]
    assert (ref_dir / "main.tf").read_text() == task.references["terraform"]


@pytest.mark.parametrize("name", ["cli", "boto3"])
def test_imperative_modify_applies_initial_without_inheriting(tmp_path, name):
    tc = RecordingToolchain(stdout='{"pass": false, "details": {}}')
    row = prevalidate_pair(
        _task("mod-sg-https"), MODALITIES[name], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert row["initial_ok"]
    commands = [a for a, _, _ in tc.seen if a[0] != "python3" or a[1] != "check.py"]
    assert commands[0][:2] == ["terraform", "init"]  # estado inicial sempre via Terraform
    assert tuple(commands[-1]) == MODALITIES[name].command[-1]
    ref_files = next(f for a, w, f in tc.seen if w.name == "reference")
    assert "terraform.tfstate" not in ref_files


def test_prevalidate_pair_order_and_validity(tmp_path):
    outputs = iter(['{"pass": false, "details": {}}', '{"pass": true, "details": {}}'])

    class CheckToolchain(RecordingToolchain):
        def run(self, args, workdir, timeout_s=None):
            if args[:2] == ["python3", "check.py"]:
                self.stdout = next(outputs)
            else:
                self.stdout = ""
            return super().run(args, workdir, timeout_s)

    tc = CheckToolchain()
    row = prevalidate_pair(
        _task("task-234"), MODALITIES["cli"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert [a[0] for a, _, _ in tc.seen] == ["python3", "bash", "python3"]
    assert row["baseline_pass"] is False and row["ref_check_pass"] is True
    assert row["valid"] and row["initial_ok"] is None


def test_prevalidate_pair_stops_when_initial_fails(tmp_path):
    tc = RecordingToolchain(1, stderr="Error: boom")
    row = prevalidate_pair(
        _task("mod-dynamodb-pitr"), MODALITIES["boto3"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert row["initial_ok"] is False and not row["valid"]
    assert row["baseline_pass"] is None and row["ref_exec_ok"] is None
