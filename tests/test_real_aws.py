import json
import subprocess
from pathlib import Path

import pytest

from iac_harness.cli import build_executor, build_toolchain, final_cleanup
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.aws_sweep import ORDER, plan_deletions
from iac_harness.executors.floci import FlociExecutor
from iac_harness.modalities.execute import run_artifact
from iac_harness.modalities.spec import MODALITIES
from iac_harness.toolchain import Toolchain
from iac_harness.types import CheckStatus
from test_modalities import RecordingToolchain

ARN = "arn:aws:iam::931493484341:user/iac-harness"
_IAC = {"IAC_AWS_ACCESS_KEY_ID": "AKIAIACFAKE", "IAC_AWS_SECRET_ACCESS_KEY": "iac-secret-value"}


@pytest.fixture
def iac_env(monkeypatch):
    for k, v in _IAC.items():
        monkeypatch.setenv(k, v)
    # Credenciais do host que nunca podem entrar.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAHOST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-secret")


def test_aws_env_uses_iac_credentials_without_endpoint(iac_env):
    tc = Toolchain(mode="local", target="aws")
    env = tc._local_env()
    assert env["AWS_ACCESS_KEY_ID"] == "AKIAIACFAKE"
    assert env["AWS_SECRET_ACCESS_KEY"] == "iac-secret-value"
    assert env["AWS_REGION"] == env["AWS_DEFAULT_REGION"] == "us-east-1"
    assert "AWS_ENDPOINT_URL" not in env
    assert tc.network == "bridge"


@pytest.mark.parametrize("missing", list(_IAC))
def test_aws_target_without_iac_credentials_raises(iac_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        Toolchain(mode="docker", target="aws")


def test_aws_docker_argv_has_no_secret_value(iac_env, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    Toolchain(mode="docker", target="aws").run(["aws", "--version"], Path("."))
    cmd, kw = calls[0]
    joined = " ".join(cmd)
    assert "iac-secret-value" not in joined and "AKIAIACFAKE" not in joined
    assert "host-secret" not in joined and "AWS_ENDPOINT_URL" not in joined
    assert cmd[cmd.index("--network") + 1] == "bridge"
    i = cmd.index("AWS_SECRET_ACCESS_KEY")
    assert cmd[i - 1] == "-e"  # só o nome; o valor vai no env do cliente docker
    assert kw["env"]["AWS_SECRET_ACCESS_KEY"] == "iac-secret-value"
    assert kw["env"]["AWS_ACCESS_KEY_ID"] == "AKIAIACFAKE"


def test_emulator_target_is_unchanged(iac_env, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    tc = Toolchain(mode="docker")
    assert tc.target == "emulator" and tc.network == "iac-sandbox"
    assert tc.sandbox_env()["AWS_ACCESS_KEY_ID"] == "test"
    tc.run(["terraform", "version"], Path("."))
    cmd, kw = calls[0]
    assert "env" not in kw
    assert "AWS_ACCESS_KEY_ID=test" in cmd and "AWS_ENDPOINT_URL=http://floci:4566" in cmd
    assert "iac-secret-value" not in " ".join(cmd)


class _StsToolchain(Toolchain):
    """Toolchain aws que responde ao sts com um ARN fixo, sem docker."""

    def __init__(self, arn: str) -> None:
        super().__init__(mode="local", target="aws")
        self.arn = arn
        self.calls: list[list[str]] = []

    def run(self, args, workdir, timeout_s=None):
        self.calls.append(list(args))
        out = json.dumps({"Account": "931493484341", "Arn": self.arn})
        return subprocess.CompletedProcess(args, 0, out + "\n", "")


class _AwsToolchain(_StsToolchain):
    """Também responde ao aws_sweep.py: inventário fixo e sweep sem sobras."""

    INVENTORY = {"s3_buckets": ["fresh-snapshot"]}

    def run(self, args, workdir, timeout_s=None):
        if args[:1] != ["python3"]:
            return super().run(args, workdir, timeout_s)
        self.calls.append(list(args))
        if args[2] == "inventory":
            out = self.INVENTORY
        else:
            self.sent_baseline = json.loads((Path(workdir) / args[3]).read_text())
            out = {"deleted": {}, "failed": {}, "remaining": {}}
        return subprocess.CompletedProcess(args, 0, json.dumps(out) + "\n", "")


def _modes(tc):
    return [c[2] for c in tc.calls if c[0] == "python3"]


def test_baseline_is_loaded_from_existing_file(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    baseline_file.write_text(json.dumps({"s3_buckets": ["clean-account"]}))
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).reset()
    assert _modes(tc) == ["sweep"]  # sem inventário novo
    assert tc.sent_baseline == {"s3_buckets": ["clean-account"]}


def test_baseline_file_is_created_when_missing(iac_env, tmp_path):
    baseline_file = tmp_path / "sub" / "aws-baseline.json"
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).reset()
    assert _modes(tc) == ["inventory", "sweep"]
    assert json.loads(baseline_file.read_text()) == _AwsToolchain.INVENTORY
    assert tc.sent_baseline == _AwsToolchain.INVENTORY


def test_cleanup_sweeps_when_baseline_exists(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    baseline_file.write_text(json.dumps({"s3_buckets": []}))
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).cleanup()
    assert _modes(tc) == ["sweep"]


def test_cleanup_without_baseline_does_not_snapshot(iac_env, tmp_path):
    baseline_file = tmp_path / "aws-baseline.json"
    tc = _AwsToolchain(ARN)
    RealAwsExecutor(tc, expected_arn=ARN, baseline_file=baseline_file).cleanup()
    assert tc.calls == [] and not baseline_file.exists()


def test_cleanup_is_noop_on_floci(monkeypatch):
    def no_subprocess(*a, **kw):
        raise AssertionError("cleanup do Floci não deve rodar comandos")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    assert FlociExecutor(Toolchain(mode="local")).cleanup() is None


def test_final_cleanup_does_not_mask_original_error(capsys):
    class Failing:
        NAME = "X"

        def cleanup(self):
            raise RuntimeError("cleanup quebrou")

    with pytest.raises(ValueError, match="original"):
        try:
            raise ValueError("original")
        finally:
            final_cleanup(Failing())
    assert "cleanup quebrou" in capsys.readouterr().err
    with pytest.raises(RuntimeError, match="cleanup quebrou"):
        final_cleanup(Failing())


def test_wrong_arn_is_refused(iac_env):
    executor = RealAwsExecutor(_StsToolchain("arn:aws:iam::931493484341:root"), expected_arn=ARN)
    with pytest.raises(RuntimeError, match="recusada"):
        executor.reset()
    assert all(c[0] == "aws" for c in executor._tc.calls)  # nada foi apagado


def test_expected_arn_is_accepted(iac_env):
    executor = RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN)
    assert executor.identity() == {"account": "931493484341", "arn": ARN}


def test_override_is_none_and_never_unsupported(iac_env):
    executor = RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN)
    assert executor.override_for({"main.tf": 'provider "aws" {}'}) is None
    assert executor.classify_error("Error: not implemented (501)") is CheckStatus.FAIL


def test_plan_deletions_only_outside_baseline():
    baseline = {
        "ec2_vpcs": ["vpc-old"],
        "s3_buckets": ["mine"],
        "iam_users": ["mod-kept"],
        "kms_keys": ["key-old"],
    }
    current = {
        "ec2_vpcs": ["vpc-old", "vpc-new"],
        "s3_buckets": ["mine", "agent-bucket"],
        "iam_users": ["mod-kept", "del-new", "iac-harness", "someone"],
        "iam_groups": ["mod-group", "admins"],
        "kms_keys": ["key-old"],
        "sqs_queues": [],
        "unknown_category": ["x"],
    }
    plan = plan_deletions(current, baseline)
    assert plan == {
        "ec2_vpcs": ["vpc-new"],
        "s3_buckets": ["agent-bucket"],
        "iam_users": ["del-new"],
        "iam_groups": ["mod-group"],
    }
    assert list(plan) == [c for c in ORDER if c in plan]  # ordem de dependência
    assert plan_deletions(baseline, baseline) == {}


def test_build_wires_aws_kind(iac_env):
    cfg = {"sandbox": {"kind": "aws", "expected_arn": ARN, "region": "us-east-1"}}
    tc = build_toolchain(cfg)
    assert tc.target == "aws" and tc.network == "bridge"
    executor = build_executor(cfg, tc, allow_real_aws=True)
    assert isinstance(executor, RealAwsExecutor) and executor.expected_arn == ARN
    assert executor.baseline_file == Path("runs/aws-baseline.json")
    cfg["sandbox"]["baseline_file"] = "x/b.json"
    assert build_executor(cfg, tc, allow_real_aws=True).baseline_file == Path("x/b.json")
    with pytest.raises(ValueError, match="expected_arn"):
        build_executor({"sandbox": {"kind": "aws"}}, tc, allow_real_aws=True)


def test_abc_commands_refuse_aws_kind(iac_env):
    cfg = {"sandbox": {"kind": "aws", "expected_arn": ARN}}
    tc = build_toolchain(cfg)
    with pytest.raises(SystemExit, match="modality"):
        build_executor(cfg, tc)  # prevalidate, prevalidate-miac, run e resume
    assert isinstance(build_executor(cfg, tc, allow_real_aws=True), RealAwsExecutor)


def test_run_artifact_on_aws_never_flags_unsupported(iac_env, tmp_path):
    tc = RecordingToolchain(1, stderr="Error: NotImplemented: CreateThing")
    executor = RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN)
    res, _ = run_artifact(tc, executor, MODALITIES["cli"], "x", tmp_path, "r", "t", "reference")
    assert not res.ok and res.unsupported is False


def test_build_default_kind_stays_emulator():
    tc = build_toolchain({})
    assert tc.target == "emulator"
    assert type(build_executor({}, tc)) is FlociExecutor
