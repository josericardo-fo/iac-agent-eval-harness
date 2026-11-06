"""Integração do container do agente (E3): exige Docker, Floci e ANTHROPIC_BASE_URL no ambiente.

Usa o argv real do ClaudeCodeAgent (redes, env, usuário, montagens) e troca só o comando final.
Nenhuma chamada a LLM.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from iac_harness.agentic.agents import DEFAULT_AGENT_IMAGE, ClaudeCodeAgent
from iac_harness.agentic.conditions import resolve_condition
from iac_harness.agentic.targets import FlociTarget
from iac_harness.executors.floci import FlociExecutor
from iac_harness.toolchain import Toolchain

ROOT = Path(__file__).parent.parent
COMPOSE = ROOT / "docker" / "compose.yaml"

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def agent_env():
    if not _docker_available():
        pytest.skip("docker indisponível")
    if not (os.environ.get("ANTHROPIC_BASE_URL") and os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        pytest.skip("defina ANTHROPIC_BASE_URL e ANTHROPIC_AUTH_TOKEN (set -a; source .env)")
    subprocess.run(
        ["docker", "build", "-f", "docker/agent.Dockerfile", "-t", DEFAULT_AGENT_IMAGE, "."],
        cwd=ROOT, check=True, capture_output=True, timeout=1800,
    )  # fmt: skip
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--profile", "agent", "up", "-d", "--wait",
         "floci", "egress-proxy"],
        check=True, timeout=300,
    )  # fmt: skip


def _run_in_agent(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    tc = Toolchain(mode="docker")
    target = FlociTarget(tc, FlociExecutor(tc))
    agent = ClaudeCodeAgent()
    work = tmp_path / "work"
    work.mkdir()
    work.chmod(0o777)
    argv, client_env = agent.docker_argv(
        "unused", work, resolve_condition("none", ROOT), "unused", target, "iac-agent-itest"
    )
    argv = argv[: argv.index(agent.image) + 1] + ["sh", "-c", script]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, env=client_env)


def test_internet_is_unreachable(agent_env, tmp_path):
    proc = _run_in_agent(tmp_path, "curl -sS --max-time 5 https://example.com")
    assert proc.returncode != 0


def test_direct_route_is_unreachable(agent_env, tmp_path):
    proc = _run_in_agent(tmp_path, 'curl -sS --noproxy "*" --max-time 5 http://1.1.1.1')
    assert proc.returncode != 0


def test_aws_points_to_floci(agent_env, tmp_path):
    proc = _run_in_agent(tmp_path, "aws sts get-caller-identity --query Account --output text")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "000000000000"


def test_env_has_no_iac_aws_and_runs_as_agent(agent_env, tmp_path):
    proc = _run_in_agent(tmp_path, 'env; echo "user=$(id -un)"; touch /work/ok')
    assert proc.returncode == 0, proc.stderr
    assert "IAC_AWS" not in proc.stdout
    assert "user=agent" in proc.stdout and "HOME=/home/agent" in proc.stdout
    assert (tmp_path / "work" / "ok").exists()


def test_claude_cli_works(agent_env, tmp_path):
    proc = _run_in_agent(tmp_path, "claude --version")
    assert proc.returncode == 0, proc.stderr
    assert "Claude Code" in proc.stdout
