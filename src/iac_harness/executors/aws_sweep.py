"""Inventário e limpeza da conta AWS real (roda DENTRO da toolchain, com o boto3 da imagem).

Uso: `python3 aws_sweep.py inventory` imprime o inventário em JSON;
`python3 aws_sweep.py sweep baseline.json` apaga o que não está no baseline e imprime
`{"deleted": ..., "failed": ..., "remaining": ...}`.

Escopo: a região do `AWS_DEFAULT_REGION` (us-east-1) + IAM. Fica de fora, sempre: recursos
default (VPC, subnets, SG, route table principal e IGW da VPC default), chaves KMS gerenciadas
pela AWS, usuários/grupos IAM fora dos prefixos `del-`/`mod-`, roles IAM fora do prefixo
`iac-harness-`, dispositivos MFA virtuais atribuídos a um usuário (inclui o MFA do root), o
usuário `default` do ElastiCache e os grupos `default*` do DAX. Os defaults
saem já no inventário: categoria ausente do baseline (tratada como vazia) nunca apaga um default.
Só o que não estava no baseline é apagado. O módulo não importa boto3 no topo: a lógica de
diferença é testável no host.
Lacuna conhecida e aceita: subnets e route tables criadas pelo agente DENTRO da VPC default não
são apagadas (são gratuitas).
"""

from __future__ import annotations

import json
import os
import sys
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
    "ec2_nat_gateways", "ec2_elastic_ips",
    "ec2_network_interfaces", "ec2_security_groups", "ec2_subnets",
    "ec2_internet_gateways", "ec2_route_tables", "ec2_vpcs", "rds_subnet_groups",
    "s3_buckets", "dynamodb_tables", "sqs_queues", "sns_topics", "secrets", "kms_keys",
    "iam_virtual_mfa_devices", "iam_users", "iam_groups", "iam_roles",
)  # fmt: skip
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


# ---------- inventário (boto3) ----------


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


# ---------- remoção (boto3) ----------


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


def _empty_bucket(session: Any, name: str) -> Any:
    """Esvazia versões e delete markers; devolve o client da região do bucket."""
    region = session.client("s3").get_bucket_location(Bucket=name).get("LocationConstraint")
    s3 = session.client("s3", region_name=region or "us-east-1")
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=name):
        objs = [
            {"Key": o["Key"], "VersionId": o["VersionId"]}
            for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if objs:
            s3.delete_objects(Bucket=name, Delete={"Objects": objs, "Quiet": True})
    return s3


def _delete_iam_user_attachments(iam: Any, name: str) -> None:
    for k in iam.list_access_keys(UserName=name)["AccessKeyMetadata"]:
        iam.delete_access_key(UserName=name, AccessKeyId=k["AccessKeyId"])
    try:
        iam.delete_login_profile(UserName=name)
    except iam.exceptions.NoSuchEntityException:
        pass
    except iam.exceptions.ClientError as exc:
        # Sem iam:DeleteLoginProfile (a política inline do harness tem limite de tamanho), a AWS
        # nega até quando o perfil nem existe. As tarefas não criam perfil de login; se existir,
        # o delete_user falha com DeleteConflict e o item aparece em `failed`/`remaining`.
        if exc.response.get("Error", {}).get("Code") != "AccessDenied":
            raise
    for p in iam.list_user_policies(UserName=name)["PolicyNames"]:
        iam.delete_user_policy(UserName=name, PolicyName=p)
    for p in iam.list_attached_user_policies(UserName=name)["AttachedPolicies"]:
        iam.detach_user_policy(UserName=name, PolicyArn=p["PolicyArn"])
    for g in iam.list_groups_for_user(UserName=name)["Groups"]:
        iam.remove_user_from_group(UserName=name, GroupName=g["GroupName"])


def _delete_iam_group(iam: Any, name: str) -> None:
    for u in iam.get_group(GroupName=name)["Users"]:
        iam.remove_user_from_group(GroupName=name, UserName=u["UserName"])
    for p in iam.list_group_policies(GroupName=name)["PolicyNames"]:
        iam.delete_group_policy(GroupName=name, PolicyName=p)
    for p in iam.list_attached_group_policies(GroupName=name)["AttachedPolicies"]:
        iam.detach_group_policy(GroupName=name, PolicyArn=p["PolicyArn"])
    iam.delete_group(GroupName=name)


def _delete_iam_role(iam: Any, name: str) -> None:
    for p in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]:
        iam.detach_role_policy(RoleName=name, PolicyArn=p["PolicyArn"])
    for p in iam.list_role_policies(RoleName=name)["PolicyNames"]:
        iam.delete_role_policy(RoleName=name, PolicyName=p)
    iam.delete_role(RoleName=name)


def _delete_cloudfront_distribution(cloudfront: Any, dist_id: str) -> None:
    """Desabilita (se preciso), espera o Deployed e apaga com o ETag atual."""
    current = cloudfront.get_distribution_config(Id=dist_id)
    config = current["DistributionConfig"]
    if config["Enabled"]:
        config["Enabled"] = False
        cloudfront.update_distribution(
            Id=dist_id, IfMatch=current["ETag"], DistributionConfig=config
        )
    cloudfront.get_waiter("distribution_deployed").wait(
        Id=dist_id, WaiterConfig={"Delay": 60, "MaxAttempts": WAIT_TIMEOUT_S // 60}
    )
    etag = cloudfront.get_distribution(Id=dist_id)["ETag"]
    cloudfront.delete_distribution(Id=dist_id, IfMatch=etag)


def _deleters(session: Any) -> dict[str, Any]:
    """Categoria → função que apaga um item (pode esperar; erros sobem para `sweep`)."""
    ec2 = session.client("ec2")
    rds = session.client("rds")
    lightsail = session.client("lightsail")
    kafka = session.client("kafka")
    iam = session.client("iam")
    cloudfront = session.client("cloudfront")
    elasticache = session.client("elasticache")
    dax = session.client("dax")

    def ec2_instance(i: str) -> None:
        ec2.terminate_instances(InstanceIds=[i])
        ec2.get_waiter("instance_terminated").wait(
            InstanceIds=[i], WaiterConfig={"Delay": 15, "MaxAttempts": WAIT_TIMEOUT_S // 15}
        )

    def rds_instance(i: str) -> None:
        rds.delete_db_instance(
            DBInstanceIdentifier=i, SkipFinalSnapshot=True, DeleteAutomatedBackups=True
        )
        rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=i, WaiterConfig={"Delay": 30, "MaxAttempts": WAIT_TIMEOUT_S // 30}
        )

    def msk_cluster(arn: str) -> None:
        kafka.delete_cluster(ClusterArn=arn)
        _poll_gone(
            lambda: [c["ClusterArn"] for c in _pages(kafka, "list_clusters_v2", "ClusterInfoList")],
            arn,
        )

    def lightsail_instance(name: str) -> None:
        for d in lightsail.get_disks().get("disks", []):
            if d.get("attachedTo") == name:
                lightsail.detach_disk(diskName=d["name"])
        lightsail.delete_instance(instanceName=name, forceDeleteAddOns=True)
        _poll_gone(lambda: [i["name"] for i in lightsail.get_instances()["instances"]], name)

    def lightsail_disk(name: str) -> None:
        deadline = time.monotonic() + WAIT_TIMEOUT_S
        while lightsail.get_disk(diskName=name)["disk"].get("isAttached"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"disco {name} continua anexado")
            time.sleep(10)
        lightsail.delete_disk(diskName=name, forceDeleteAddOns=True)

    def lightsail_database(name: str) -> None:
        lightsail.delete_relational_database(relationalDatabaseName=name, skipFinalSnapshot=True)
        _poll_gone(
            lambda: [
                d["name"]
                for d in lightsail.get_relational_databases().get("relationalDatabases", [])
            ],
            name,
        )

    def cloudfront_oac(oac_id: str) -> None:
        etag = cloudfront.get_origin_access_control(Id=oac_id)["ETag"]
        cloudfront.delete_origin_access_control(Id=oac_id, IfMatch=etag)

    def elasticache_user_group(group_id: str) -> None:
        # Usuário membro de grupo não pode ser apagado: espera o grupo sumir.
        elasticache.delete_user_group(UserGroupId=group_id)
        _poll_gone(
            lambda: [
                g["UserGroupId"] for g in _pages(elasticache, "describe_user_groups", "UserGroups")
            ],
            group_id,
        )

    def glacier_vault(name: str) -> None:
        glacier = session.client("glacier")
        glacier.delete_vault_notifications(vaultName=name)
        glacier.delete_vault(vaultName=name)

    def kinesis_video_stream(arn: str) -> None:
        kv = session.client("kinesisvideo")
        kv.delete_stream(StreamARN=arn)
        _poll_gone(
            lambda: [s["StreamARN"] for s in _pages(kv, "list_streams", "StreamInfoList")], arn
        )

    def nat_gateway(i: str) -> None:
        # O EIP do NAT só é liberado depois que o NAT chega a `deleted` (leva ~1 min).
        ec2.delete_nat_gateway(NatGatewayId=i)
        _poll_gone(lambda: _nat_gateways(ec2), i)

    def elastic_ip(allocation_id: str) -> None:
        addr = ec2.describe_addresses(AllocationIds=[allocation_id])["Addresses"][0]
        if addr.get("AssociationId"):
            ec2.disassociate_address(AssociationId=addr["AssociationId"])
        ec2.release_address(AllocationId=allocation_id)

    def network_interface(i: str) -> None:
        try:
            ec2.delete_network_interface(NetworkInterfaceId=i)
        except Exception as exc:
            # Some sozinha com o término da instância: já apagada não é falha.
            if _error_code(exc) != "InvalidNetworkInterfaceID.NotFound":
                raise

    def igw(i: str) -> None:
        g = ec2.describe_internet_gateways(InternetGatewayIds=[i])["InternetGateways"][0]
        for a in g.get("Attachments", []):
            ec2.detach_internet_gateway(InternetGatewayId=i, VpcId=a["VpcId"])
        ec2.delete_internet_gateway(InternetGatewayId=i)

    def route_table(i: str) -> None:
        t = ec2.describe_route_tables(RouteTableIds=[i])["RouteTables"][0]
        for a in t.get("Associations", []):
            if not a.get("Main"):
                ec2.disassociate_route_table(AssociationId=a["RouteTableAssociationId"])
        ec2.delete_route_table(RouteTableId=i)

    def s3_bucket(name: str) -> None:
        _empty_bucket(session, name).delete_bucket(Bucket=name)

    def dynamodb_table(name: str) -> None:
        ddb = session.client("dynamodb")
        ddb.delete_table(TableName=name)
        ddb.get_waiter("table_not_exists").wait(TableName=name)

    def kms_key(key_id: str) -> None:
        session.client("kms").schedule_key_deletion(
            KeyId=key_id, PendingWindowInDays=KMS_PENDING_DAYS
        )

    def iam_user(name: str) -> None:
        _delete_iam_user_attachments(iam, name)
        iam.delete_user(UserName=name)

    return {
        "ec2_instances": ec2_instance,
        "rds_instances": rds_instance,
        "msk_clusters": msk_cluster,
        "lightsail_instances": lightsail_instance,
        "lightsail_disks": lightsail_disk,
        "lightsail_databases": lightsail_database,
        "lambda_functions": lambda n: session.client("lambda").delete_function(FunctionName=n),
        "cloudfront_distributions": lambda i: _delete_cloudfront_distribution(cloudfront, i),
        "cloudfront_oacs": cloudfront_oac,
        "cloudwatch_composite_alarms": lambda n: session.client("cloudwatch").delete_alarms(
            AlarmNames=[n]
        ),
        "cloudwatch_metric_alarms": lambda n: session.client("cloudwatch").delete_alarms(
            AlarmNames=[n]
        ),
        "elasticache_user_groups": elasticache_user_group,
        "elasticache_users": lambda i: elasticache.delete_user(UserId=i),
        "dax_subnet_groups": lambda n: dax.delete_subnet_group(SubnetGroupName=n),
        "dax_parameter_groups": lambda n: dax.delete_parameter_group(ParameterGroupName=n),
        "sagemaker_human_task_uis": lambda n: session.client("sagemaker").delete_human_task_ui(
            HumanTaskUiName=n
        ),
        "glacier_vaults": glacier_vault,
        "kinesis_video_streams": kinesis_video_stream,
        "ec2_nat_gateways": nat_gateway,
        "ec2_elastic_ips": elastic_ip,
        "ec2_network_interfaces": network_interface,
        "ec2_security_groups": lambda i: ec2.delete_security_group(GroupId=i),
        "ec2_subnets": lambda i: ec2.delete_subnet(SubnetId=i),
        "ec2_internet_gateways": igw,
        "ec2_route_tables": route_table,
        "ec2_vpcs": lambda i: ec2.delete_vpc(VpcId=i),
        "rds_subnet_groups": lambda n: rds.delete_db_subnet_group(DBSubnetGroupName=n),
        "s3_buckets": s3_bucket,
        "dynamodb_tables": dynamodb_table,
        "sqs_queues": lambda u: session.client("sqs").delete_queue(QueueUrl=u),
        "sns_topics": lambda a: session.client("sns").delete_topic(TopicArn=a),
        "secrets": lambda a: session.client("secretsmanager").delete_secret(
            SecretId=a, ForceDeleteWithoutRecovery=True
        ),
        "kms_keys": kms_key,
        "iam_virtual_mfa_devices": lambda s: iam.delete_virtual_mfa_device(SerialNumber=s),
        "iam_users": iam_user,
        "iam_groups": lambda n: _delete_iam_group(iam, n),
        "iam_roles": lambda n: _delete_iam_role(iam, n),
    }


def sweep(session: Any, baseline: dict[str, list[str]]) -> dict[str, Any]:
    """Apaga o que não está no baseline e relê o inventário até estabilizar.

    Itens que falharam (ex.: recurso ainda em "creating") são tentados de novo a cada
    releitura; os que já foram apagados só aguardam sumir do inventário.
    """
    deleters = _deleters(session)
    deleted: dict[str, list[str]] = {}
    failed: dict[str, dict[str, str]] = {}

    def delete_all(plan: dict[str, list[str]]) -> None:
        for category, items in plan.items():
            for item in items:
                if item in deleted.get(category, []):
                    continue
                try:
                    deleters[category](item)
                    deleted.setdefault(category, []).append(item)
                    if item in failed.get(category, {}):
                        del failed[category][item]
                        if not failed[category]:
                            del failed[category]
                except Exception as exc:  # noqa: BLE001 (registra e segue; o restante é conferido)
                    failed.setdefault(category, {})[item] = f"{type(exc).__name__}: {exc}"[:500]

    delete_all(plan_deletions(inventory(session), baseline))
    remaining: dict[str, list[str]] = {}
    for attempt in range(SETTLE_TRIES):
        remaining = plan_deletions(inventory(session), baseline)
        if not remaining or attempt == SETTLE_TRIES - 1:
            break
        time.sleep(SETTLE_SLEEP_S)
        delete_all(remaining)
    return {"deleted": deleted, "failed": failed, "remaining": remaining}


def main(argv: list[str]) -> int:
    import boto3

    session = boto3.Session(region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    if argv[:1] == ["inventory"]:
        print(json.dumps(inventory(session)))
        return 0
    if argv[:1] == ["sweep"] and len(argv) == 2:
        with open(argv[1]) as fh:
            baseline = json.load(fh)
        print(json.dumps(sweep(session, baseline)))
        return 0
    print("uso: aws_sweep.py inventory | sweep <baseline.json>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
