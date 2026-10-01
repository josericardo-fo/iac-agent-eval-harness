"""Integração real: exige Docker, imagem da toolchain e Floci (docker compose up -d)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx2
import pytest

from iac_harness.cli import (
    build_executor,
    build_gates,
    build_toolchain,
    load_config,
    prevalidate_task,
)
from iac_harness.datasets.iac_eval_v2 import IacEvalV2Adapter
from iac_harness.runner import COMMON_CHAIN, run_gates
from iac_harness.types import Artifact, Stage, TaskOracle
from iac_harness.workspace import create_workspace

ROOT = Path(__file__).parent.parent
COMPOSE = ROOT / "docker" / "compose.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "iac_eval_v2_sample.csv"

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.fixture(scope="module")
def floci():
    if not _docker_available():
        pytest.skip("docker indisponível")
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "--wait"], check=True, timeout=300
    )
    yield
    subprocess.run(["docker", "compose", "-f", str(COMPOSE), "down"], check=False, timeout=120)


def test_prevalidate_s3_reference_passes(floci, tmp_path):
    cfg = load_config(ROOT / "configs" / "smoke.toml")
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    gates = build_gates(cfg, toolchain, executor)
    adapter = IacEvalV2Adapter(tmp_path / "data")
    adapter.csv_path.parent.mkdir(parents=True)
    adapter.csv_path.write_bytes(FIXTURE.read_bytes())
    task, oracle = next(p for p in adapter.load() if p[0].task_id == "aws/task-269")

    executor.reset()
    row = prevalidate_task(task, oracle, gates, executor, tmp_path / "runs", "it")

    assert row["statuses"] == {
        "VALIDATE": "PASS", "PLAN": "PASS", "OPA": "PASS", "SANDBOX": "PASS",
    }  # fmt: skip
    assert row["harness_valid"] and row["sandbox_supported"]


def _bucket_exists(name: str) -> bool:
    return httpx2.head(f"http://127.0.0.1:4566/{name}", timeout=30).status_code == 200


# Primeiro contato com o EC2 é um CreateVpc (sem Describe*): sem o seed, a VPC default nunca
# existe e o RDS sem db_subnet_group_name falha com "No subnets available".
RDS_WITHOUT_SUBNET_GROUP = """\
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}

resource "aws_db_instance" "main" {
  engine              = "mysql"
  instance_class      = "db.t3.micro"
  allocated_storage   = 10
  username            = "admin"
  password            = "password123"
  skip_final_snapshot = true
}
"""


def test_reset_clears_state_and_seeds_default_vpc(floci, tmp_path):
    cfg = load_config(ROOT / "configs" / "smoke.toml")
    toolchain = build_toolchain(cfg)
    executor = build_executor(cfg, toolchain)
    gates = build_gates(cfg, toolchain, executor)

    executor.reset()
    assert httpx2.put("http://127.0.0.1:4566/reset-probe", timeout=30).status_code == 200
    assert _bucket_exists("reset-probe")

    executor.reset()
    assert not _bucket_exists("reset-probe")

    files = {
        "main.tf": 'provider "aws" {\n  region = "us-east-1"\n}\n\n' + RDS_WITHOUT_SUBNET_GROUP
    }
    ws = create_workspace(
        tmp_path, "it", "reset-seed", 0, "reference", "final", Artifact(files),
        executor.override_for(files),
    )  # fmt: skip
    chain = [s for s in COMMON_CHAIN if s is not Stage.OPA]  # sem política: o foco é o apply
    statuses, failing, _ = run_gates(chain, gates, ws, TaskOracle({}, ""))

    assert failing is None, failing.message
    assert statuses["SANDBOX"] == "PASS"
