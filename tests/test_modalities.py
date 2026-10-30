import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities.execute import (
    ANSWER_ERROR,
    CHECK_SETTLE_INTERVAL_S,
    CHECK_SETTLE_WINDOW_S,
    ExecResult,
    collect_answer,
    inherits_state,
    parse_check_output,
    prevalidate_pair,
    replay_pair,
    run_artifact,
    run_check,
    with_answer,
)
from iac_harness.modalities.loader import TASKS_DIR, load_task, load_tasks
from iac_harness.modalities.spec import MODALITIES
from iac_harness.workspace import OVERRIDE_FILENAME
from test_floci import FakeToolchain

EXPECTED_TASKS = {
    "task-108", "task-115", "task-230", "task-231", "task-234",
    "task-110", "task-118", "task-130", "task-201", "task-233", "task-373", "task-375",
    "mod-s3-versioning", "mod-dynamodb-pitr", "mod-sg-https",
    "mod-s3-encryption", "mod-sqs-retention", "mod-iam-user-policy", "mod-dynamodb-ttl",
    "mod-secret-tag",
    "del-s3-bucket", "del-sqs-dev", "del-iam-user",
    "query-sg-ssh-open", "query-dynamodb-ondemand",
    # só na AWS real (docs/selecao-tarefas-modalidades.md)
    "task-367", "task-267", "task-416", "task-069", "task-198", "task-228", "task-229",
    "awsb-create-s3-bucket-with-config", "awsb-move-s3-contents-within-bucket",
    "awsb-sync-s3-buckets-with-metadata", "awsb-dynamodb-strip-ec2-fields-from-prod",
    "awsb-lambda-versioned-alias-update",
    # E2: pares claro × ambíguo (as claras mod-s3-versioning e mod-sqs-retention já estão acima)
    "amb-dynamodb-pitr", "clr-dynamodb-pitr", "amb-s3-versioning", "amb-sqs-retention",
    "amb-sg-https", "clr-sg-https", "amb-del-sqs", "clr-del-sqs", "amb-del-s3", "clr-del-s3",
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


def test_load_tasks_has_all_with_required_files():
    tasks = load_tasks()
    assert {t.task_id for t in tasks} == EXPECTED_TASKS
    for t in tasks:
        assert t.prompt.strip() and t.check.strip()
        assert set(t.references) == set(MODALITIES) - set(t.missing_references)
        assert all(body.strip() for body in t.references.values())
        assert (t.category != "create") == (t.initial_tf is not None)
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


def _copy_task_as_delete(tmp_path, with_initial):
    src = TASKS_DIR / "mod-s3-versioning"
    dst = tmp_path / "mod-s3-versioning"
    dst.mkdir()
    for p in src.iterdir():
        if p.name != "initial.tf" or with_initial:
            (dst / p.name).write_text(p.read_text())
    meta = dst / "meta.toml"
    meta.write_text(meta.read_text().replace('category = "modify"', 'category = "delete"'))
    return dst


def test_load_task_accepts_delete_with_initial(tmp_path):
    task = load_task(_copy_task_as_delete(tmp_path, with_initial=True))
    assert task.category == "delete" and task.initial_tf is not None


def test_load_task_rejects_missing_initial_in_delete(tmp_path):
    with pytest.raises(ValueError, match="initial.tf"):
        load_task(_copy_task_as_delete(tmp_path, with_initial=False))


def test_load_task_expect_defaults_to_act_and_reads_ask():
    assert _task("mod-s3-versioning").expect == "act"
    assert _task("amb-s3-versioning").expect == "ask"
    assert _task("clr-dynamodb-pitr").expect == "act"


def test_load_task_rejects_invalid_expect(tmp_path):
    dst = _copy_task_as_delete(tmp_path, with_initial=True)
    meta = dst / "meta.toml"
    meta.write_text(meta.read_text() + 'expect = "maybe"\n')
    with pytest.raises(ValueError, match="expect"):
        load_task(dst)


def test_ask_pairs_share_initial_tf():
    from iac_harness.modalities.metrics import ASK_PAIRS

    for amb, clr in ASK_PAIRS.items():
        assert _task(amb).expect == "ask" and _task(clr).expect == "act"
        assert _task(amb).initial_tf == _task(clr).initial_tf


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
    task = _task("mod-s3-versioning")  # sem upstream/: só o check vai ao diretório
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


@pytest.mark.parametrize(
    ("baseline", "ref_check", "valid"),
    [(True, False, True), (False, True, False), (True, True, False), (False, False, False)],
)
def test_prevalidate_pair_inverted_gate_for_ask(tmp_path, baseline, ref_check, valid):
    outputs = iter(json.dumps({"pass": v, "details": {}}) for v in (baseline, ref_check))

    class CheckToolchain(RecordingToolchain):
        def run(self, args, workdir, timeout_s=None):
            self.stdout = next(outputs) if args[:2] == ["python3", "check.py"] else ""
            return super().run(args, workdir, timeout_s)

    tc = CheckToolchain()
    row = prevalidate_pair(
        _task("amb-del-sqs"), MODALITIES["cli"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert row["expect"] == "ask" and row["valid"] is valid


def test_prevalidate_pair_stops_when_initial_fails(tmp_path):
    tc = RecordingToolchain(1, stderr="Error: boom")
    row = prevalidate_pair(
        _task("mod-dynamodb-pitr"), MODALITIES["boto3"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert row["initial_ok"] is False and not row["valid"]
    assert row["baseline_pass"] is None and row["ref_exec_ok"] is None


# ---------- consulta ----------


def _query_toolchain(write_state: bool):
    """Apply grava tfstate com o output "answer"; o check registra se recebeu o answer.json."""

    class QueryToolchain(RecordingToolchain):
        def run(self, args, workdir, timeout_s=None):
            if write_state and args[:2] == ["terraform", "apply"]:
                state = {"outputs": {"answer": {"value": {"security_groups": ["a"]}}}}
                (Path(workdir) / "terraform.tfstate").write_text(json.dumps(state))
            if args[:2] == ["python3", "check.py"]:
                self.stdout = '{"pass": false, "details": {}}'
                if (Path(workdir) / "answer.json").exists():
                    self.stdout = '{"pass": true, "details": {}}'
            return super().run(args, workdir, timeout_s)

    return QueryToolchain()


def test_terraform_query_does_not_inherit_tfstate(tmp_path):
    tc = _query_toolchain(write_state=True)
    row = prevalidate_pair(
        _task("query-sg-ssh-open"), MODALITIES["terraform"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    ref_init = next(f for a, w, f in tc.seen if a[:2] == ["terraform", "init"] and
                    w.name == "reference")  # fmt: skip
    assert "terraform.tfstate" not in ref_init
    assert row["ref_exec_ok"] and row["valid"]  # o check recebeu o answer.json


@pytest.mark.parametrize(
    ("value", "expected"),
    [({"tables": ["x"]}, {"tables": ["x"]}), ('{"tables": ["x"]}', {"tables": ["x"]})],
)
def test_collect_answer_terraform_decodes_object_and_json_string(tmp_path, value, expected):
    state = {"outputs": {"answer": {"value": value}}}
    (tmp_path / "terraform.tfstate").write_text(json.dumps(state))
    assert collect_answer(tmp_path, MODALITIES["terraform"]).ok
    assert json.loads((tmp_path / "answer.json").read_text()) == expected


@pytest.mark.parametrize("name", ["terraform", "cli", "boto3"])
def test_collect_answer_missing_is_fixed_error(tmp_path, name):
    res = collect_answer(tmp_path, MODALITIES[name])
    assert not res.ok and res.output == ANSWER_ERROR[name]
    (tmp_path / "answer.json").write_text("não é json")
    (tmp_path / "terraform.tfstate").write_text('{"outputs": {}}')
    assert not collect_answer(tmp_path, MODALITIES[name]).ok


def test_run_check_receives_answer_json(tmp_path):
    (tmp_path / "answer.json").write_text('{"tables": []}')
    tc = RecordingToolchain(stdout='{"pass": true, "details": {}}')
    run_check(tc, _task("query-dynamodb-ondemand"), tmp_path)
    assert tc.seen[0][2] == {"check.py", "answer.json"}


class SequenceCheckToolchain(RecordingToolchain):
    """Devolve uma saída por chamada do check.py (a última se repete); demais comandos vazios."""

    def __init__(self, check_outputs: list[str], target: str = "aws") -> None:
        super().__init__()
        self.target = target
        self.check_outputs = check_outputs
        self.check_runs = 0

    def run(self, args, workdir, timeout_s=None):
        if args[:2] == ["python3", "check.py"]:
            self.stdout = self.check_outputs[min(self.check_runs, len(self.check_outputs) - 1)]
            self.check_runs += 1
        else:
            self.stdout = ""
        return super().run(args, workdir, timeout_s)


def _record_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr("iac_harness.modalities.execute.time.sleep", sleeps.append)
    return sleeps


def test_run_check_settle_on_aws_retries_until_pass(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    fail = '{"pass": false, "details": {"n": 0}}'
    tc = SequenceCheckToolchain([fail, fail, '{"pass": true, "details": {"n": 1}}'])
    passed, details = run_check(tc, load_tasks()[0], settle=True)
    assert passed and details == {"n": 1, "check_attempts": 3}
    assert sleeps == [CHECK_SETTLE_INTERVAL_S] * 2


def test_run_check_settle_on_aws_gives_up_after_window(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    tc = SequenceCheckToolchain(['{"pass": false, "details": {"last": true}}'])
    passed, details = run_check(tc, load_tasks()[0], settle=True)
    attempts = 1 + CHECK_SETTLE_WINDOW_S // CHECK_SETTLE_INTERVAL_S
    assert not passed and details == {"last": True, "check_attempts": attempts}
    assert tc.check_runs == attempts == 7
    assert sum(sleeps) == CHECK_SETTLE_WINDOW_S


def test_run_check_settle_on_emulator_runs_once(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    tc = SequenceCheckToolchain(['{"pass": false, "details": {}}'], target="emulator")
    assert run_check(tc, load_tasks()[0], settle=True) == (False, {})
    assert tc.check_runs == 1 and sleeps == []


def test_prevalidate_pair_on_aws_does_not_retry_baseline(monkeypatch, tmp_path):
    sleeps = _record_sleeps(monkeypatch)
    tc = SequenceCheckToolchain(['{"pass": false, "details": {}}', '{"pass": true, "details": {}}'])
    row = prevalidate_pair(
        _task("task-234"), MODALITIES["cli"], tc, FlociExecutor(tc), tmp_path, "r"
    )
    assert tc.check_runs == 2 and sleeps == []
    assert row["baseline_pass"] is False and row["baseline_details"] == {}
    assert row["ref_check_pass"] is True and row["ref_check_details"] == {"check_attempts": 1}
    assert row["valid"]


# ---------- tarefas só na AWS real (hide_initial, answer, missing_references, upstream) ----------


def _copy_task(tmp_path, task_id="mod-s3-versioning", extra_meta="", skip=()):
    src = TASKS_DIR / task_id
    dst = tmp_path / task_id
    dst.mkdir()
    for p in src.iterdir():
        if p.is_file() and p.name not in skip:
            (dst / p.name).write_text(p.read_text())
    meta = dst / "meta.toml"
    meta.write_text(meta.read_text() + extra_meta)
    return dst


def test_load_task_new_fields_default_off():
    task = _task("mod-s3-versioning")
    assert not task.hide_initial and not task.answer
    assert task.missing_references == () and task.missing_reason == "" and task.upstream == {}


def test_load_task_reads_new_fields_and_upstream(tmp_path):
    meta = (
        "hide_initial = true\nanswer = true\n"
        'missing_references = ["terraform"]\nmissing_reason = "sem recurso nativo"\n'
    )
    dst = _copy_task(tmp_path, extra_meta=meta, skip=("reference.tf",))
    (dst / "upstream").mkdir()
    (dst / "upstream" / "check.py").write_text("print('orig')")
    task = load_task(dst)
    assert task.hide_initial and task.answer
    assert task.missing_references == ("terraform",)
    assert task.missing_reason == "sem recurso nativo"
    assert set(task.references) == {"cli", "boto3"}
    assert task.upstream == {"check.py": "print('orig')"}


def test_load_task_requires_missing_reason(tmp_path):
    dst = _copy_task(tmp_path, extra_meta='missing_references = ["terraform"]\n')
    with pytest.raises(ValueError, match="missing_reason"):
        load_task(dst)


def test_load_task_rejects_unknown_missing_modality(tmp_path):
    meta = 'missing_references = ["pulumi"]\nmissing_reason = "x"\n'
    with pytest.raises(ValueError, match="missing_references"):
        load_task(_copy_task(tmp_path, extra_meta=meta))


def test_load_task_still_requires_reference_of_other_modalities(tmp_path):
    meta = 'missing_references = ["terraform"]\nmissing_reason = "x"\n'
    with pytest.raises(FileNotFoundError):
        load_task(_copy_task(tmp_path, extra_meta=meta, skip=("reference.tf", "reference.sh")))


def test_inherits_state_false_with_hide_initial():
    task = _task("mod-s3-versioning")
    assert inherits_state(task, MODALITIES["terraform"])
    assert not inherits_state(replace(task, hide_initial=True), MODALITIES["terraform"])


def test_with_answer_applies_when_answer_flag(tmp_path):
    task = replace(_task("mod-s3-versioning"), answer=True)
    res = with_answer(task, MODALITIES["cli"], ExecResult(True, "ok"), tmp_path)
    assert not res.ok and res.output == ANSWER_ERROR["cli"]
    (tmp_path / "answer.json").write_text('{"x": 1}')
    assert with_answer(task, MODALITIES["cli"], ExecResult(True, "ok"), tmp_path).ok
    plain = _task("mod-s3-versioning")
    assert with_answer(plain, MODALITIES["cli"], ExecResult(True, "ok"), tmp_path / "x").ok


def test_prevalidate_pair_skips_modality_without_reference(tmp_path):
    task = replace(_task("mod-s3-versioning"), missing_references=("terraform",))
    tc = RecordingToolchain()
    row = prevalidate_pair(task, MODALITIES["terraform"], tc, FlociExecutor(tc), tmp_path, "r")
    assert row["ref_missing"] is True and row["valid"] is None
    assert tc.seen == []  # nada aplicado
    tc2 = RecordingToolchain(stdout='{"pass": false, "details": {}}')
    row2 = prevalidate_pair(task, MODALITIES["cli"], tc2, FlociExecutor(tc2), tmp_path, "r")
    assert row2["ref_missing"] is False and row2["initial_ok"]


def test_run_check_writes_upstream_files():
    seen = {}

    class UpstreamToolchain(RecordingToolchain):
        def run(self, args, workdir, timeout_s=None):
            up = Path(workdir) / "upstream"
            seen.update({p.name: p.read_text() for p in up.iterdir()})
            return super().run(args, workdir, timeout_s)

    tc = UpstreamToolchain(stdout='{"pass": true, "details": {}}')
    task = replace(load_tasks()[0], upstream={"check.py": "orig", "instruction.md": "doc"})
    assert run_check(tc, task)[0]
    assert tc.seen[0][2] == {"check.py", "upstream"}
    assert seen == {"check.py": "orig", "instruction.md": "doc"}


# ---------- replay (E1) ----------

COLLATERAL_TASKS = {
    "mod-s3-versioning", "mod-s3-encryption", "mod-dynamodb-pitr", "mod-dynamodb-ttl",
    "mod-sg-https", "mod-sqs-retention", "mod-iam-user-policy", "mod-secret-tag",
    "query-sg-ssh-open", "query-dynamodb-ondemand",
}  # fmt: skip


def test_collateral_loaded_only_for_e1_tasks():
    assert {t.task_id for t in load_tasks() if t.collateral is not None} == COLLATERAL_TASKS


class ReplayToolchain(RecordingToolchain):
    """Retratos na ordem dada (str = stdout do collateral.py); check e artefato configuráveis."""

    def __init__(self, snapshots, check_pass=True, snapshot_rc=0):
        super().__init__()
        self.snapshots = list(snapshots)
        self.check_pass, self.snapshot_rc = check_pass, snapshot_rc

    def run(self, args, workdir, timeout_s=None):
        if args[:2] == ["python3", "collateral.py"]:
            self.stdout, self.returncode = self.snapshots.pop(0), self.snapshot_rc
        elif args[:2] == ["python3", "check.py"]:
            self.stdout = json.dumps({"pass": self.check_pass, "details": {}})
            self.returncode = 0
        else:
            self.stdout, self.returncode = "", 0
        return super().run(args, workdir, timeout_s)


def _snap(**state):
    return json.dumps({"snapshot": state})


def _replay(tmp_path, tc, task_id="mod-sg-https", modality="cli", content="echo final"):
    return replay_pair(
        _task(task_id), MODALITIES[modality], content, tc, FlociExecutor(tc), tmp_path, "r", rep=2
    )


def test_replay_pair_order_and_equal_snapshots(tmp_path):
    tc = ReplayToolchain([_snap(a=1), _snap(a=1)])
    row = _replay(tmp_path, tc)
    order = [a[1] if a[0] == "python3" else a[0] for a, _, _ in tc.seen]
    assert order == ["terraform", "terraform", "collateral.py", "bash", "check.py",
                     "collateral.py"]  # fmt: skip
    replay_dir = next(w for a, w, _ in tc.seen if a[0] == "bash")
    assert replay_dir == tmp_path / "r" / "mod-sg-https" / "2" / "cli" / "replay"
    assert (replay_dir / "run.sh").read_text() == "echo final"
    assert row["initial_ok"] and row["exec_ok"] and row["check_pass"] is True
    assert row["collateral_ok"] is True and row["collateral_details"] == {"changed": {}}


def test_replay_pair_different_snapshots_gives_diff(tmp_path):
    before = _snap(id="sg-1", groups=["a"], keep="x")
    after = _snap(id="sg-2", groups=["a", "b"], keep="x")
    row = _replay(tmp_path, ReplayToolchain([before, after]))
    assert row["collateral_ok"] is False
    assert row["collateral_details"]["changed"] == {
        "id": {"before": "sg-1", "after": "sg-2"},
        "groups": {"before": ["a"], "after": ["a", "b"]},
    }


@pytest.mark.parametrize(
    ("snapshots", "rc"),
    [(['{"error": "boom"}', _snap(a=1)], 0), ([_snap(a=1), "não é json"], 0),
     ([_snap(a=1), _snap(a=1)], 1)],
)  # fmt: skip
def test_replay_pair_snapshot_failure_is_none(tmp_path, snapshots, rc):
    row = _replay(tmp_path, ReplayToolchain(snapshots, snapshot_rc=rc))
    assert row["collateral_ok"] is None
    details = row["collateral_details"]
    assert details["before_error"] or details["after_error"]
    assert row["check_pass"] is True  # o check roda igual


def test_replay_pair_terraform_modify_inherits_state(tmp_path):
    class StateToolchain(ReplayToolchain):
        def run(self, args, workdir, timeout_s=None):
            if args[:2] == ["terraform", "apply"]:
                (Path(workdir) / "terraform.tfstate").write_text("state")
            return super().run(args, workdir, timeout_s)

    tc = StateToolchain([_snap(a=1), _snap(a=1)])
    _replay(tmp_path, tc, modality="terraform", content="resource x {}")
    replay_init = next(f for a, w, f in tc.seen if w.name == "replay" and a[0] == "terraform")
    assert "terraform.tfstate" in replay_init


def test_replay_pair_initial_failure_skips_artifact(tmp_path):
    tc = RecordingToolchain(1, stderr="Error: boom")
    row = _replay(tmp_path, tc)
    assert row["initial_ok"] is False and row["exec_ok"] is None
    assert row["collateral_ok"] is None
    assert all(a[0] == "terraform" for a, _, _ in tc.seen)
