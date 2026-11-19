"""Remoção de bancos e afins: RDS, MSK, ElastiCache, DAX e DynamoDB."""

from __future__ import annotations

from typing import Any

from .common import WAIT_TIMEOUT_S, _pages, _poll_gone


def deleters(session: Any) -> dict[str, Any]:
    """Bancos e afins: categoria → função que apaga um item."""
    rds = session.client("rds")
    kafka = session.client("kafka")
    elasticache = session.client("elasticache")
    dax = session.client("dax")

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

    def elasticache_user_group(group_id: str) -> None:
        # Usuário membro de grupo não pode ser apagado: espera o grupo sumir.
        elasticache.delete_user_group(UserGroupId=group_id)
        _poll_gone(
            lambda: [
                g["UserGroupId"] for g in _pages(elasticache, "describe_user_groups", "UserGroups")
            ],
            group_id,
        )

    def dynamodb_table(name: str) -> None:
        ddb = session.client("dynamodb")
        ddb.delete_table(TableName=name)
        ddb.get_waiter("table_not_exists").wait(TableName=name)

    return {
        "rds_instances": rds_instance,
        "msk_clusters": msk_cluster,
        "elasticache_user_groups": elasticache_user_group,
        "elasticache_users": lambda i: elasticache.delete_user(UserId=i),
        "dax_subnet_groups": lambda n: dax.delete_subnet_group(SubnetGroupName=n),
        "dax_parameter_groups": lambda n: dax.delete_parameter_group(ParameterGroupName=n),
        "rds_subnet_groups": lambda n: rds.delete_db_subnet_group(DBSubnetGroupName=n),
        "dynamodb_tables": dynamodb_table,
    }
