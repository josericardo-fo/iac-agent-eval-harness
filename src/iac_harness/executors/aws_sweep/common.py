"""Constantes, paginação, espera e o plano de remoção (diferença contra o baseline)."""

from __future__ import annotations

import time
from typing import Any

# Ordem de remoção (dependências primeiro). As esperas ficam em `sweep`.
ORDER = (
    "ec2_instances", "rds_instances", "msk_clusters",
    "lightsail_instances", "lightsail_disks", "lightsail_databases",
    "lambda_functions", "cloudfront_distributions", "cloudfront_oacs",
    "cloudwatch_composite_alarms", "cloudwatch_metric_alarms",
    "elasticache_user_groups", "elasticache_users",
    "dax_subnet_groups", "dax_parameter_groups",
    "sagemaker_human_task_uis", "glacier_vaults", "kinesis_video_streams",
    "ec2_nat_gateways", "ec2_elastic_ips", "ec2_snapshots", "ec2_volumes",
    "usw2_elastic_ips", "usw2_snapshots", "usw2_volumes",
    "ec2_network_interfaces", "ec2_security_groups", "ec2_subnets",
    "ec2_internet_gateways", "ec2_route_tables", "ec2_vpcs", "rds_subnet_groups",
    "s3_buckets", "dynamodb_tables", "sqs_queues", "sns_topics", "secrets", "ssm_parameters",
    "kms_keys",
    "iam_virtual_mfa_devices", "iam_users", "iam_groups", "iam_roles",
)  # fmt: skip
SECOND_REGION = "us-west-2"
IAM_PREFIXES = ("del-", "mod-")
ROLE_PREFIX = "iac-harness-"
KMS_PENDING_DAYS = 7
WAIT_TIMEOUT_S = 1500  # RDS e MSK levam minutos para sumir
SETTLE_TRIES = 10  # releituras do inventário (consistência eventual de SQS, IAM, S3)
SETTLE_SLEEP_S = 10


def plan_deletions(
    current: dict[str, list[str]], baseline: dict[str, list[str]]
) -> dict[str, list[str]]:
    """O que apagar: itens de `current` fora do `baseline`, na ordem `ORDER`.

    Categorias desconhecidas são ignoradas; usuários/grupos IAM só com os prefixos
    `del-`/`mod-` e roles só com `iac-harness-`.
    """
    plan: dict[str, list[str]] = {}
    for category in ORDER:
        keep = set(baseline.get(category, []))
        items = [i for i in current.get(category, []) if i not in keep]
        if category in ("iam_users", "iam_groups"):
            items = [i for i in items if i.startswith(IAM_PREFIXES)]
        if category == "iam_roles":
            items = [i for i in items if i.startswith(ROLE_PREFIX)]
        if items:
            plan[category] = items
    return plan


def _pages(client: Any, op: str, key: str, **kwargs: Any) -> list[Any]:
    out: list[Any] = []
    for page in client.get_paginator(op).paginate(**kwargs):
        out.extend(page.get(key, []))
    return out


def _token_pages(fn: Any, key: str, **kwargs: Any) -> list[Any]:
    """Paginação por NextToken sem paginator (DAX)."""
    out: list[Any] = []
    while True:
        page = fn(**kwargs)
        out.extend(page.get(key, []))
        if not page.get("NextToken"):
            return out
        kwargs["NextToken"] = page["NextToken"]


def _error_code(exc: Exception) -> str:
    """Código de erro de um ClientError do botocore ("" para outras exceções)."""
    return getattr(exc, "response", {}).get("Error", {}).get("Code", "")


def _poll_gone(list_fn: Any, item: str) -> None:
    """Espera `item` sumir de `list_fn()` (serviços sem waiter de remoção)."""
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    while item in list_fn():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{item} não sumiu em {WAIT_TIMEOUT_S}s")
        time.sleep(15)
