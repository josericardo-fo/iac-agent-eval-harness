"""Backend: registro por `[sandbox] kind`, alvo do agente e procedência vindos do backend."""

from __future__ import annotations

import pytest

from iac_harness.agentic.targets import AwsTarget, FlociTarget
from iac_harness.cli.common import EXECUTORS, build_executor, build_toolchain
from iac_harness.cli.provenance import _sandbox_provenance
from iac_harness.executors import floci
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain
from test_real_aws import ARN, _StsToolchain, iac_env  # noqa: F401 (fixture)


def test_registry_maps_kind_to_backend_and_toolchain_target():
    assert EXECUTORS == {"floci": FlociExecutor, "aws": RealAwsExecutor}
    assert {k: cls.TOOLCHAIN_TARGET for k, cls in EXECUTORS.items()} == {
        "floci": "emulator",
        "aws": "aws",
    }


def test_unknown_kind_builds_emulator_toolchain_and_is_refused_by_the_executor():
    cfg = {"sandbox": {"kind": "moto"}}
    tc = build_toolchain(cfg)
    assert tc.target == "emulator"
    with pytest.raises(SystemExit, match="kind desconhecido: 'moto'"):
        build_executor(cfg, tc)


def test_floci_backend_provides_its_agent_target():
    tc = Toolchain(mode="docker")
    executor = FlociExecutor(tc)
    target = executor.agent_target()
    assert type(target) is FlociTarget and target.kind == "floci"
    assert target.toolchain is tc and target.executor is executor


def test_aws_backend_provides_its_agent_target(iac_env):  # noqa: F811
    tc = Toolchain(mode="docker", target="aws")
    executor = RealAwsExecutor(tc, expected_arn=ARN)
    target = executor.agent_target()
    assert type(target) is AwsTarget and target.kind == "aws"
    assert target.toolchain is tc and target.executor is executor


def test_floci_provenance_is_the_container_image(monkeypatch):
    answers = {
        "{{.Config.Image}}": "floci:2.1.0",
        "{{.Image}}": "sha256:abc",
        "{{json .RepoDigests}}": '["floci@sha256:def"]',
    }
    calls = []

    def query(args):
        calls.append(args)
        return next(answer for fmt, answer in answers.items() if fmt in args)

    monkeypatch.setattr(floci, "query", query)
    executor = FlociExecutor(Toolchain(mode="local"), container="c1")
    prov = _sandbox_provenance(executor)
    assert list(prov.items()) == [
        ("sandbox_kind", "floci"),
        ("sandbox_image", "floci:2.1.0"),
        ("sandbox_image_id", "sha256:abc"),
        ("sandbox_repo_digests", '["floci@sha256:def"]'),
    ]
    assert calls[0][-1] == "c1" and calls[2][-1] == "sha256:abc"


def test_floci_provenance_skips_digests_when_inspect_fails(monkeypatch):
    monkeypatch.setattr(floci, "query", lambda args: "erro: no such container")
    prov = FlociExecutor(Toolchain(mode="local")).provenance()
    assert prov["sandbox_image_id"] == "erro: no such container"
    assert prov["sandbox_repo_digests"] is None


def test_aws_provenance_runs_the_identity_guard(iac_env):  # noqa: F811
    prov = _sandbox_provenance(RealAwsExecutor(_StsToolchain(ARN), expected_arn=ARN))
    assert list(prov.items()) == [
        ("sandbox_kind", "aws"),
        ("aws_account", "931493484341"),
        ("aws_arn", ARN),
        ("aws_region", "us-east-1"),
    ]
    other = RealAwsExecutor(_StsToolchain("arn:aws:iam::1:user/x"), expected_arn=ARN)
    with pytest.raises(RuntimeError, match="identidade AWS recusada"):
        other.provenance()
