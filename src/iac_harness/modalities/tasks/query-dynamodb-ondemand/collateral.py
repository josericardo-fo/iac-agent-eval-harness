"""Retrato do efeito colateral: a consulta não pode criar, mudar nem remover nada.

Guarda a lista de tabelas e, em cada uma, ARN, TableId e data de criação (recriada = valores
novos), chave, modo de cobrança, capacidade provisionada, stream, SSE, proteção contra remoção,
tags, TTL e PITR. Fora: TableStatus e contadores
voláteis (ItemCount, TableSizeBytes, datas de aumento/redução de capacidade). A resposta
(answer.json) não entra.
"""

import json
import sys

import boto3


def main(region: str) -> dict:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = sorted(ddb.list_tables().get("TableNames", []))
    snap: dict = {"tables": tables}
    for name in tables:
        t = ddb.describe_table(TableName=name)["Table"]
        throughput = t.get("ProvisionedThroughput", {})
        tags = ddb.list_tags_of_resource(ResourceArn=t["TableArn"]).get("Tags", [])
        snap[name] = {
            "arn": t.get("TableArn"),
            "id": t.get("TableId"),
            "created": t.get("CreationDateTime"),
            "key_schema": t.get("KeySchema"),
            "attributes": sorted(
                t.get("AttributeDefinitions", []), key=lambda a: a["AttributeName"]
            ),
            "billing": t.get("BillingModeSummary", {}).get("BillingMode"),
            "rcu": throughput.get("ReadCapacityUnits"),
            "wcu": throughput.get("WriteCapacityUnits"),
            "stream": t.get("StreamSpecification"),
            "sse": t.get("SSEDescription"),
            "deletion_protection": t.get("DeletionProtectionEnabled"),
            "tags": sorted((x["Key"], x["Value"]) for x in tags),
        }
        ttl = ddb.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
        snap[name]["ttl"] = [ttl.get("TimeToLiveStatus"), ttl.get("AttributeName")]
        backups = ddb.describe_continuous_backups(TableName=name)["ContinuousBackupsDescription"]
        pitr = backups.get("PointInTimeRecoveryDescription", {})
        snap[name]["pitr"] = pitr.get("PointInTimeRecoveryStatus")
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
