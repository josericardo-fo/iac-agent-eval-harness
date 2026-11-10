"""Guarda do proxy de egresso: proxy parado aborta antes de gravar qualquer trajetória."""

from __future__ import annotations

import subprocess

import pytest

from iac_harness.agentic import targets
from iac_harness.agentic.runner import AgentRunContext, run_agent_pair
from iac_harness.agentic.targets import AwsTarget, EgressProxyDownError, FlociTarget
from iac_harness.executors.aws import RealAwsExecutor
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain
from test_agentic_runner import FakeAgent, FakeTarget, Recorder, _plugin_root, _task


def _fake_docker(monkeypatch, stdout="true\n", returncode=0, raises=None):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")

    monkeypatch.setattr(targets.subprocess, "run", run)
    return calls


def _floci() -> FlociTarget:
    tc = Toolchain(mode="docker")
    return FlociTarget(tc, FlociExecutor(tc))


def _aws(monkeypatch) -> AwsTarget:
    monkeypatch.setenv("IAC_AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("IAC_AWS_SECRET_ACCESS_KEY", "y")
    tc = Toolchain(mode="docker", target="aws")
    return AwsTarget(tc, RealAwsExecutor(tc, expected_arn="arn:aws:iam::1:user/iac-harness"))


@pytest.mark.parametrize(
    ("make", "container"),
    [
        (lambda mp: _floci(), "iac-harness-egress-proxy-1"),
        (_aws, "iac-harness-egress-proxy-aws-1"),
    ],
)
def test_check_proxy_inspects_the_targets_container(monkeypatch, make, container):
    calls = _fake_docker(monkeypatch)
    make(monkeypatch).check_proxy()
    assert calls == [["docker", "inspect", "-f", "{{.State.Running}}", container]]


@pytest.mark.parametrize(
    "kw",
    [
        {"stdout": "false\n"},
        {"stdout": "", "returncode": 1},  # container inexistente
        {"raises": OSError("sem docker")},
        {"raises": subprocess.TimeoutExpired("docker", 1)},
    ],
)
def test_check_proxy_raises_naming_the_container(monkeypatch, kw):
    _fake_docker(monkeypatch, **kw)
    with pytest.raises(EgressProxyDownError, match="iac-harness-egress-proxy-aws-1"):
        _aws(monkeypatch).check_proxy()


class _DeadProxyTarget(FakeTarget):
    def check_proxy(self):
        raise EgressProxyDownError("iac-harness-egress-proxy-1")


def test_dead_proxy_aborts_before_any_trajectory_or_event(tmp_path):
    agent, rec, target = FakeAgent(), Recorder(), _DeadProxyTarget()
    ctx = AgentRunContext(
        run_id="r", runs_dir=tmp_path / "runs", agent=agent,
        plugin_root=_plugin_root(tmp_path), strata={},
    )  # fmt: skip
    with pytest.raises(EgressProxyDownError, match="egress-proxy-1"):
        run_agent_pair(_task(), "none", "Haiku 5.5", 1, target, rec, ctx)
    assert agent.prompts == []  # o agente não rodou
    assert rec.rows == []  # nenhum evento
    assert target.calls == []  # nem reset da conta
    assert not (tmp_path / "runs").exists()
