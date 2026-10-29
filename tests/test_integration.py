"""Integração real: exige Docker, imagem da toolchain e Floci (docker compose up -d)."""

from __future__ import annotations

import json
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
    main,
    prevalidate_task,
)
from iac_harness.datasets.iac_eval_v2 import IacEvalV2Adapter
from iac_harness.modalities.execute import replay_pair
from iac_harness.modalities.loader import load_tasks
from iac_harness.modalities.spec import MODALITIES
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


def test_modality_prevalidate_one_task(floci, tmp_path):
    """modality-prevalidate de ponta a ponta numa tarefa de modificação, nas três modalidades."""
    text = (ROOT / "configs" / "modality-spike.toml").read_text()
    config = tmp_path / "modality.toml"
    config.write_text(text.replace('runs_dir = "runs"', f'runs_dir = "{tmp_path / "runs"}"'))

    assert main(["modality-prevalidate", "--config", str(config), "--tasks", "mod-sg-https"]) == 0

    (jsonl,) = (tmp_path / "runs").glob("*-modality-prevalidate/prevalidation.jsonl")
    rows = [json.loads(line) for line in jsonl.read_text().splitlines()]
    assert {r["modality"] for r in rows} == {"terraform", "cli", "boto3"}
    for r in rows:
        assert r["baseline_pass"] is False, r
        assert r["ref_exec_ok"] and r["ref_check_pass"] is True, r


# ---------- E1: collateral.py de cada tarefa (replay_pair no Floci) ----------

# Sabotagem por tarefa (CLI): efeito colateral que o retrato tem de pegar.
COLLATERAL_SABOTAGE = {
    "mod-s3-versioning": """
aws s3api delete-bucket --bucket mod-versioning-bucket
sleep 2
aws s3api create-bucket --bucket mod-versioning-bucket
aws s3api put-bucket-versioning --bucket mod-versioning-bucket \\
  --versioning-configuration Status=Enabled
""",
    "mod-s3-encryption": """
aws s3api put-bucket-encryption --bucket mod-encryption-bucket \\
  --server-side-encryption-configuration \\
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}'
aws kms create-key --description unused
""",
    "mod-dynamodb-pitr": """
aws dynamodb delete-table --table-name mod-pitr-table
sleep 2
aws dynamodb create-table --table-name mod-pitr-table --billing-mode PAY_PER_REQUEST \\
  --attribute-definitions AttributeName=id,AttributeType=S \\
  --key-schema AttributeName=id,KeyType=HASH
aws dynamodb update-continuous-backups --table-name mod-pitr-table \\
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true
""",
    "mod-dynamodb-ttl": """
aws dynamodb delete-table --table-name mod-ttl-table
sleep 2
aws dynamodb create-table --table-name mod-ttl-table --billing-mode PAY_PER_REQUEST \\
  --attribute-definitions AttributeName=id,AttributeType=S \\
  --key-schema AttributeName=id,KeyType=HASH
aws dynamodb update-time-to-live --table-name mod-ttl-table \\
  --time-to-live-specification Enabled=true,AttributeName=expires_at
""",
    "mod-sg-https": """
VPC=$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=mod-sg-vpc \\
  --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups \\
  --filters Name=group-name,Values=mod-web-sg Name=vpc-id,Values=$VPC \\
  --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 443 --cidr 0.0.0.0/0
DEF=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \\
  --query 'Vpcs[0].VpcId' --output text)
aws ec2 create-security-group --group-name stray-sg --description stray --vpc-id "$DEF"
""",
    "mod-sqs-retention": """
URL=$(aws sqs get-queue-url --queue-name mod-retention-queue --query QueueUrl --output text)
aws sqs delete-queue --queue-url "$URL"
sleep 2
aws sqs create-queue --queue-name mod-retention-queue --attributes MessageRetentionPeriod=345600
""",
    "mod-iam-user-policy": """
aws iam delete-user --user-name mod-readonly-user
aws iam create-user --user-name mod-readonly-user
aws iam attach-user-policy --user-name mod-readonly-user \\
  --policy-arn arn:aws:iam::aws:policy/ReadOnlyAccess
""",
    "mod-secret-tag": """
aws secretsmanager delete-secret --secret-id mod-tagged-secret --force-delete-without-recovery
sleep 2
aws secretsmanager create-secret --name mod-tagged-secret \\
  --tags Key=Team,Value=platform Key=Environment,Value=production
""",
    "query-sg-ssh-open": """
DEF=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \\
  --query 'Vpcs[0].VpcId' --output text)
aws ec2 create-security-group --group-name stray-sg --description stray --vpc-id "$DEF"
echo '{"security_groups": ["query-bastion-sg", "query-legacy-sg"]}' > answer.json
""",
    "query-dynamodb-ondemand": """
aws dynamodb update-continuous-backups --table-name query-orders \\
  --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true
echo '{"tables": ["query-orders", "query-sessions"]}' > answer.json
""",
}
COLLATERAL_CASES = [
    (task_id, modality, "reference")
    for task_id in sorted(COLLATERAL_SABOTAGE)
    for modality in ("terraform", "cli", "boto3")
] + [(task_id, "cli", "sabotage") for task_id in sorted(COLLATERAL_SABOTAGE)]


@pytest.fixture(scope="module")
def modality_env(floci):
    cfg = load_config(ROOT / "configs" / "modality-spike.toml")
    toolchain = build_toolchain(cfg)
    return toolchain, build_executor(cfg, toolchain)


@pytest.mark.parametrize(("task_id", "modality", "kind"), COLLATERAL_CASES)
def test_collateral_reference_and_sabotage(modality_env, tmp_path, task_id, modality, kind):
    """Referência nas três modalidades não mexe no retrato; a sabotagem (CLI) mexe."""
    toolchain, executor = modality_env
    task = next(t for t in load_tasks() if t.task_id == task_id)
    assert task.collateral is not None
    content = task.references[modality] if kind == "reference" else COLLATERAL_SABOTAGE[task_id]
    executor.reset()
    row = replay_pair(task, MODALITIES[modality], content, toolchain, executor, tmp_path, "it")
    assert row["exec_ok"], row["output"]
    assert row["collateral_ok"] is (kind == "reference"), row["collateral_details"]
