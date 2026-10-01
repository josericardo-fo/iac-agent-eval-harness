import subprocess
from pathlib import Path

import pytest

from fakes import FakeBackend, FakeVerifier
from iac_harness.conditions import A
from iac_harness.executors.floci import FlociExecutor
from iac_harness.recorder.jsonl import JsonlRecorder
from iac_harness.runner import RunContext, run_task
from iac_harness.toolchain import EndpointNotAllowedError, Toolchain
from iac_harness.types import Stage, Task, TaskOracle
from iac_harness.workspace import OVERRIDE_FILENAME


@pytest.mark.parametrize(
    "endpoint",
    ["https://s3.amazonaws.com", "http://169.254.169.254", "http://floci.evil.com:4566"],
)
def test_disallowed_endpoint_is_rejected(endpoint):
    with pytest.raises(EndpointNotAllowedError):
        FlociExecutor(Toolchain(mode="local"), endpoint=endpoint)


def test_disallowed_host_endpoint_is_rejected():
    with pytest.raises(EndpointNotAllowedError):
        FlociExecutor(Toolchain(mode="local"), host_endpoint="https://example.com")


def test_allowed_hosts_are_accepted():
    for endpoint in ("http://floci:4566", "http://localhost:4566", "http://127.0.0.1:4566"):
        FlociExecutor(Toolchain(mode="local"), endpoint=endpoint)


def test_override_is_injected_into_every_workspace(tmp_path):
    executor = FlociExecutor(Toolchain(mode="local"))
    ctx = RunContext(
        run_id="r",
        runs_dir=tmp_path,
        dataset_version="v",
        override=lambda artifact: executor.override_for(artifact.files),
    )
    gates = {s: FakeVerifier(s) for s in Stage}
    run_task(
        Task("t", "p", 1), TaskOracle({}, ""), 1, [A], FakeBackend(), gates,
        JsonlRecorder(tmp_path / "e.jsonl"), ctx,
    )  # fmt: skip
    overrides = list(tmp_path.glob(f"r/**/{OVERRIDE_FILENAME}"))
    assert overrides, "override não foi injetado"
    assert all('s3 = "http://floci:4566"' in p.read_text() for p in overrides)


_HOST_SECRETS = {
    "AWS_PROFILE": "prod",
    "AWS_ACCESS_KEY_ID": "AKIAREAL",
    "AWS_SECRET_ACCESS_KEY": "real-secret",
    "AWS_SESSION_TOKEN": "real-token",
    "AWS_ENDPOINT_URL": "https://s3.amazonaws.com",
}


def test_local_env_does_not_leak_host_credentials(monkeypatch):
    for k, v in _HOST_SECRETS.items():
        monkeypatch.setenv(k, v)
    env = Toolchain(mode="local", endpoint="http://localhost:4566")._local_env()
    assert env["AWS_ACCESS_KEY_ID"] == "test" and env["AWS_SECRET_ACCESS_KEY"] == "test"
    assert env["AWS_ENDPOINT_URL"] == "http://localhost:4566"
    assert env["AWS_EC2_METADATA_DISABLED"] == "true" and env["TF_IN_AUTOMATION"] == "1"
    assert "AWS_PROFILE" not in env and "AWS_SESSION_TOKEN" not in env
    assert "real-secret" not in env.values() and "AKIAREAL" not in env.values()


def test_docker_command_passes_only_sandbox_env(monkeypatch):
    for k, v in _HOST_SECRETS.items():
        monkeypatch.setenv(k, v)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    Toolchain(mode="docker").run(["terraform", "version"], Path("."))
    cmd = " ".join(calls[0])
    assert "AWS_ACCESS_KEY_ID=test" in cmd and "AWS_ENDPOINT_URL=http://floci:4566" in cmd
    assert "AWS_PROFILE" not in cmd and "AWS_SESSION_TOKEN" not in cmd
    assert "AKIAREAL" not in cmd and "real-secret" not in cmd


def test_toolchain_endpoint_must_be_allowed():
    with pytest.raises(EndpointNotAllowedError):
        Toolchain(mode="local", endpoint="https://s3.amazonaws.com")
