#!/usr/bin/env python3
"""Inventário paginado dos recursos comuns da conta, com ids e tags.

Cada serviço é coletado à parte: um serviço sem suporte ou sem permissão vira um item em
`errors` e o inventário continua.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from _common import emit, endpoint, region, run, session

Resource = dict


def _res(rtype: str, rid: str, region_name: str, tags: dict | None, **extra) -> Resource:
    return {"type": rtype, "id": rid, "region": region_name, "tags": tags or {}, **extra}


def _kv(tags: list | None) -> dict:
    """Lista `[{"Key": k, "Value": v}]` (EC2, IAM, RDS, S3) → dicionário."""
    return {t["Key"]: t.get("Value", "") for t in tags or []}


def _pages(client, op: str, key: str, **kwargs):
    for page in client.get_paginator(op).paginate(**kwargs):
        yield from page.get(key, [])


def _s3(session, region_name):
    s3 = session.client("s3")
    out = []
    for b in _pages(s3, "list_buckets", "Buckets"):
        try:
            tags = _kv(s3.get_bucket_tagging(Bucket=b["Name"])["TagSet"])
        except Exception:  # noqa: BLE001 (bucket sem tags responde NoSuchTagSet)
            tags = {}
        out.append(_res("s3.bucket", b["Name"], "global", tags))
    return out


def _dynamodb(session, region_name):
    ddb = session.client("dynamodb", region_name=region_name)
    out = []
    for name in _pages(ddb, "list_tables", "TableNames"):
        arn = ddb.describe_table(TableName=name)["Table"]["TableArn"]
        tags = _kv(ddb.list_tags_of_resource(ResourceArn=arn).get("Tags"))
        out.append(_res("dynamodb.table", name, region_name, tags, arn=arn))
    return out


def _sqs(session, region_name):
    sqs = session.client("sqs", region_name=region_name)
    out = []
    for url in _pages(sqs, "list_queues", "QueueUrls"):
        tags = sqs.list_queue_tags(QueueUrl=url).get("Tags", {})
        out.append(_res("sqs.queue", url.rsplit("/", 1)[-1], region_name, tags, url=url))
    return out


def _iam(session, region_name):
    iam = session.client("iam")
    out = []
    for u in _pages(iam, "list_users", "Users"):
        tags = _kv(iam.list_user_tags(UserName=u["UserName"]).get("Tags"))
        out.append(_res("iam.user", u["UserName"], "global", tags, arn=u["Arn"]))
    for r in _pages(iam, "list_roles", "Roles"):
        tags = _kv(iam.list_role_tags(RoleName=r["RoleName"]).get("Tags"))
        out.append(_res("iam.role", r["RoleName"], "global", tags, arn=r["Arn"]))
    for p in _pages(iam, "list_policies", "Policies", Scope="Local"):
        tags = _kv(iam.list_policy_tags(PolicyArn=p["Arn"]).get("Tags"))
        out.append(_res("iam.policy", p["PolicyName"], "global", tags, arn=p["Arn"]))
    return out


def _ec2(session, region_name):
    ec2 = session.client("ec2", region_name=region_name)
    out = []
    for rsv in _pages(ec2, "describe_instances", "Reservations"):
        for i in rsv["Instances"]:
            state = i.get("State", {}).get("Name")
            if state != "terminated":
                out.append(
                    _res("ec2.instance", i["InstanceId"], region_name, _kv(i.get("Tags")),
                         state=state, instance_type=i.get("InstanceType"))
                )  # fmt: skip
    simple = (
        ("describe_vpcs", "Vpcs", "VpcId", "ec2.vpc"),
        ("describe_subnets", "Subnets", "SubnetId", "ec2.subnet"),
        ("describe_security_groups", "SecurityGroups", "GroupId", "ec2.security_group"),
    )
    for op, key, id_key, rtype in simple:
        for item in _pages(ec2, op, key):
            out.append(_res(rtype, item[id_key], region_name, _kv(item.get("Tags"))))
    for n in _pages(ec2, "describe_nat_gateways", "NatGateways"):
        if n.get("State") not in ("deleted", "deleting"):
            out.append(
                _res("ec2.nat_gateway", n["NatGatewayId"], region_name, _kv(n.get("Tags")),
                     state=n.get("State"))
            )  # fmt: skip
    for a in ec2.describe_addresses().get("Addresses", []):  # sem paginador
        rid = a.get("AllocationId") or a.get("PublicIp")
        out.append(_res("ec2.elastic_ip", rid, region_name, _kv(a.get("Tags"))))
    return out


def _rds(session, region_name):
    rds = session.client("rds", region_name=region_name)
    return [
        _res("rds.instance", d["DBInstanceIdentifier"], region_name, _kv(d.get("TagList")),
             arn=d.get("DBInstanceArn"), state=d.get("DBInstanceStatus"))
        for d in _pages(rds, "describe_db_instances", "DBInstances")
    ]  # fmt: skip


def _lambda(session, region_name):
    lam = session.client("lambda", region_name=region_name)
    out = []
    for f in _pages(lam, "list_functions", "Functions"):
        tags = lam.list_tags(Resource=f["FunctionArn"]).get("Tags", {})
        out.append(_res("lambda.function", f["FunctionName"], region_name, tags,
                        arn=f["FunctionArn"]))  # fmt: skip
    return out


def _secrets(session, region_name):
    sm = session.client("secretsmanager", region_name=region_name)
    # Só metadados: o valor do segredo nunca é lido.
    return [
        _res("secretsmanager.secret", s["Name"], region_name, _kv(s.get("Tags")), arn=s["ARN"])
        for s in _pages(sm, "list_secrets", "SecretList")
    ]


def _msk(session, region_name):
    kafka = session.client("kafka", region_name=region_name)
    return [
        _res("msk.cluster", c["ClusterName"], region_name, c.get("Tags"),
             arn=c["ClusterArn"], state=c.get("State"))
        for c in _pages(kafka, "list_clusters_v2", "ClusterInfoList")
    ]  # fmt: skip


# serviço → (coletor, é global?). Globais rodam uma vez só, na primeira região.
COLLECTORS: dict[str, tuple[Callable, bool]] = {
    "s3": (_s3, True),
    "iam": (_iam, True),
    "dynamodb": (_dynamodb, False),
    "sqs": (_sqs, False),
    "ec2": (_ec2, False),
    "rds": (_rds, False),
    "lambda": (_lambda, False),
    "secretsmanager": (_secrets, False),
    "kafka": (_msk, False),
}


def collect(session, regions: list[str], collectors: dict[str, tuple[Callable, bool]]) -> dict:
    resources: list[Resource] = []
    errors: list[dict] = []
    for i, region_name in enumerate(regions):
        for service, (fn, is_global) in collectors.items():
            if is_global and i > 0:
                continue
            try:
                resources.extend(fn(session, region_name))
            except Exception as exc:  # noqa: BLE001 (serviço sem suporte não para o resto)
                errors.append(
                    {"service": service, "region": region_name,
                     "error": f"{type(exc).__name__}: {exc}"}
                )  # fmt: skip
    return {"resources": resources, "errors": errors}


def filter_by_tags(resources: list[Resource], tags: dict[str, str]) -> list[Resource]:
    return [r for r in resources if all(r["tags"].get(k) == v for k, v in tags.items())]


def _parse_tag(spec: str) -> tuple[str, str]:
    key, sep, value = spec.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"use key=value: {spec!r}")
    return key, value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List common AWS resources (S3, DynamoDB, SQS, IAM, EC2/VPC, RDS, Lambda, "
        "Secrets Manager, MSK) with ids and tags, as JSON. A service that fails is recorded "
        "under 'errors' and the rest continues."
    )
    parser.add_argument(
        "--tag", action="append", type=_parse_tag, default=[], metavar="KEY=VALUE",
        help="keep only resources with this tag (repeatable; all must match)",
    )  # fmt: skip
    parser.add_argument(
        "--regions", nargs="+", metavar="REGION", help="regions to scan (default: AWS_REGION)"
    )
    parser.add_argument(
        "--save", type=Path, metavar="PATH",
        help="write the full inventory to PATH (a baseline) and print only a summary",
    )  # fmt: skip
    args = parser.parse_args()

    regions = args.regions or [region()]
    sess = session(regions[0])
    result = collect(sess, regions, COLLECTORS)
    result["resources"] = filter_by_tags(result["resources"], dict(args.tag))
    inventory = {
        "account": sess.client("sts").get_caller_identity()["Account"],
        "regions": regions,
        "endpoint": endpoint(),
        "tag_filter": dict(args.tag),
        **result,
    }
    if args.save:
        args.save.write_text(json.dumps(inventory, indent=2, default=str))
        emit(
            {
                "saved": str(args.save),
                "counts": dict(Counter(r["type"] for r in result["resources"])),
                "errors": result["errors"],
            }
        )
    emit(inventory)
    return 0


if __name__ == "__main__":
    run(main)
