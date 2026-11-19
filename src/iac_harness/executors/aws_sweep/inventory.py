"""Inventário da conta (boto3): categoria → ids dos recursos, já sem os defaults."""

from __future__ import annotations

import sys
from typing import Any

from .common import IAM_PREFIXES, ROLE_PREFIX, SECOND_REGION, _error_code, _pages, _token_pages


def _cloudfront_items(fn: Any, list_key: str) -> list[Any]:
    """Listas do CloudFront: {list_key: {Items, NextMarker, IsTruncated}} paginadas por Marker."""
    out: list[Any] = []
    kwargs: dict[str, Any] = {}
    while True:
        page = fn(**kwargs)[list_key]
        out.extend(page.get("Items", []))
        if not page.get("IsTruncated"):
            return out
        kwargs["Marker"] = page["NextMarker"]


def _nat_gateways(ec2: Any) -> list[str]:
    """NAT gateways ainda existentes (os `deleted` continuam listados por um tempo)."""
    return [
        n["NatGatewayId"]
        for n in _pages(ec2, "describe_nat_gateways", "NatGateways")
        if n["State"] != "deleted"
    ]


def _ec2_storage(ec2: Any) -> tuple[list[str], list[str]]:
    """(snapshots próprios, volumes) da região do client; os snapshots públicos de outros donos
    ficam de fora."""
    snapshots = _pages(ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"])
    volumes = _pages(ec2, "describe_volumes", "Volumes")
    return [s["SnapshotId"] for s in snapshots], [v["VolumeId"] for v in volumes]


def _lambda_functions(session: Any) -> list[str]:
    """Funções Lambda; `AccessDeniedException` vira lista vazia (opção B, registro E5).

    A AWS restringiu o Lambda da conta (caso 179333530400472, 30/10/2026), até para o root.
    Só esse código é tolerado: qualquer outro erro, ou de outro serviço, continua abortando.
    """
    try:
        return [
            f["FunctionName"]
            for f in _pages(session.client("lambda"), "list_functions", "Functions")
        ]
    except Exception as exc:
        if _error_code(exc) != "AccessDeniedException":
            raise
        print("aws_sweep: lambda negado (AccessDeniedException); sem Lambda", file=sys.stderr)
        return []


def inventory(session: Any) -> dict[str, list[str]]:
    ec2 = session.client("ec2")
    vpcs = _pages(ec2, "describe_vpcs", "Vpcs")
    default_vpcs = {v["VpcId"] for v in vpcs if v.get("IsDefault")}
    reservations = _pages(ec2, "describe_instances", "Reservations")
    instances = [
        i["InstanceId"]
        for r in reservations
        for i in r["Instances"]
        if i["State"]["Name"] != "terminated"
    ]
    enis = [
        n["NetworkInterfaceId"]
        for n in _pages(ec2, "describe_network_interfaces", "NetworkInterfaces")
        if not n.get("RequesterManaged")
    ]
    sgs = [
        g["GroupId"]
        for g in _pages(ec2, "describe_security_groups", "SecurityGroups")
        if g["GroupName"] != "default"
    ]
    subnets = [
        s["SubnetId"]
        for s in _pages(ec2, "describe_subnets", "Subnets")
        if not s.get("DefaultForAz") and s["VpcId"] not in default_vpcs
    ]
    igws = [
        g["InternetGatewayId"]
        for g in _pages(ec2, "describe_internet_gateways", "InternetGateways")
        if not any(a["VpcId"] in default_vpcs for a in g.get("Attachments", []))
    ]
    rts = [
        t["RouteTableId"]
        for t in _pages(ec2, "describe_route_tables", "RouteTables")
        if not any(a.get("Main") for a in t.get("Associations", []))
        and t["VpcId"] not in default_vpcs
    ]
    rds = session.client("rds")
    lightsail = session.client("lightsail")
    kafka = session.client("kafka")
    s3 = session.client("s3")
    kms = session.client("kms")
    iam = session.client("iam")
    cloudfront = session.client("cloudfront")
    cloudwatch = session.client("cloudwatch")
    elasticache = session.client("elasticache")
    dax = session.client("dax")
    ec2_west = session.client("ec2", region_name=SECOND_REGION)
    snapshots, volumes = _ec2_storage(ec2)
    snapshots_west, volumes_west = _ec2_storage(ec2_west)
    keys = []
    for k in _pages(kms, "list_keys", "Keys"):
        meta = kms.describe_key(KeyId=k["KeyId"])["KeyMetadata"]
        if meta["KeyManager"] == "CUSTOMER" and not meta["KeyState"].startswith("Pending"):
            keys.append(k["KeyId"])
    return {
        "ec2_instances": instances,
        "ec2_nat_gateways": _nat_gateways(ec2),
        "ec2_elastic_ips": [
            a["AllocationId"] for a in ec2.describe_addresses().get("Addresses", [])
        ],
        "ec2_snapshots": snapshots,
        "ec2_volumes": volumes,
        "usw2_elastic_ips": [
            a["AllocationId"] for a in ec2_west.describe_addresses().get("Addresses", [])
        ],
        "usw2_snapshots": snapshots_west,
        "usw2_volumes": volumes_west,
        "ec2_network_interfaces": enis,
        "ec2_security_groups": sgs,
        "ec2_subnets": subnets,
        "ec2_internet_gateways": igws,
        "ec2_route_tables": rts,
        "ec2_vpcs": [v["VpcId"] for v in vpcs if not v.get("IsDefault")],
        "rds_instances": [
            d["DBInstanceIdentifier"] for d in _pages(rds, "describe_db_instances", "DBInstances")
        ],
        "rds_subnet_groups": [
            g["DBSubnetGroupName"]
            for g in _pages(rds, "describe_db_subnet_groups", "DBSubnetGroups")
            if g["DBSubnetGroupName"] != "default"
        ],
        "lightsail_instances": [i["name"] for i in lightsail.get_instances().get("instances", [])],
        "lightsail_disks": [d["name"] for d in lightsail.get_disks().get("disks", [])],
        "lightsail_databases": [
            d["name"] for d in lightsail.get_relational_databases().get("relationalDatabases", [])
        ],
        "lambda_functions": _lambda_functions(session),
        "cloudfront_distributions": [
            d["Id"] for d in _cloudfront_items(cloudfront.list_distributions, "DistributionList")
        ],
        "cloudfront_oacs": [
            o["Id"]
            for o in _cloudfront_items(
                cloudfront.list_origin_access_controls, "OriginAccessControlList"
            )
        ],
        "cloudwatch_composite_alarms": [
            a["AlarmName"]
            for a in _pages(
                cloudwatch, "describe_alarms", "CompositeAlarms", AlarmTypes=["CompositeAlarm"]
            )
        ],
        "cloudwatch_metric_alarms": [
            a["AlarmName"]
            for a in _pages(
                cloudwatch, "describe_alarms", "MetricAlarms", AlarmTypes=["MetricAlarm"]
            )
        ],
        "elasticache_user_groups": [
            g["UserGroupId"] for g in _pages(elasticache, "describe_user_groups", "UserGroups")
        ],
        "elasticache_users": [
            u["UserId"]
            for u in _pages(elasticache, "describe_users", "Users")
            if u["UserId"] != "default"
        ],
        "dax_subnet_groups": [
            g["SubnetGroupName"]
            for g in _token_pages(dax.describe_subnet_groups, "SubnetGroups")
            if not g["SubnetGroupName"].startswith("default")
        ],
        "dax_parameter_groups": [
            g["ParameterGroupName"]
            for g in _token_pages(dax.describe_parameter_groups, "ParameterGroups")
            if not g["ParameterGroupName"].startswith("default")
        ],
        "sagemaker_human_task_uis": [
            u["HumanTaskUiName"]
            for u in _pages(
                session.client("sagemaker"), "list_human_task_uis", "HumanTaskUiSummaries"
            )
        ],
        "glacier_vaults": [
            v["VaultName"] for v in _pages(session.client("glacier"), "list_vaults", "VaultList")
        ],
        "kinesis_video_streams": [
            s["StreamARN"]
            for s in _pages(session.client("kinesisvideo"), "list_streams", "StreamInfoList")
        ],
        "msk_clusters": [
            c["ClusterArn"] for c in _pages(kafka, "list_clusters_v2", "ClusterInfoList")
        ],
        "s3_buckets": [b["Name"] for b in s3.list_buckets().get("Buckets", [])],
        "dynamodb_tables": _pages(session.client("dynamodb"), "list_tables", "TableNames"),
        "sqs_queues": _pages(session.client("sqs"), "list_queues", "QueueUrls"),
        "sns_topics": [
            t["TopicArn"] for t in _pages(session.client("sns"), "list_topics", "Topics")
        ],
        "secrets": [
            s["ARN"] for s in _pages(session.client("secretsmanager"), "list_secrets", "SecretList")
        ],
        "ssm_parameters": [
            p["Name"] for p in _pages(session.client("ssm"), "describe_parameters", "Parameters")
        ],
        "kms_keys": keys,
        # Só os não atribuídos: o MFA do root e os de usuários nunca entram no inventário.
        "iam_virtual_mfa_devices": [
            d["SerialNumber"]
            for d in _pages(
                iam, "list_virtual_mfa_devices", "VirtualMFADevices", AssignmentStatus="Unassigned"
            )
            if "User" not in d
        ],
        "iam_users": [
            u["UserName"]
            for u in _pages(iam, "list_users", "Users")
            if u["UserName"].startswith(IAM_PREFIXES)
        ],
        "iam_groups": [
            g["GroupName"]
            for g in _pages(iam, "list_groups", "Groups")
            if g["GroupName"].startswith(IAM_PREFIXES)
        ],
        "iam_roles": [
            r["RoleName"]
            for r in _pages(iam, "list_roles", "Roles")
            if r["RoleName"].startswith(ROLE_PREFIX)
        ],
    }
