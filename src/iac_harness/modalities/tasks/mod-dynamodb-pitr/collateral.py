"""Retrato do efeito colateral: o que deve ficar igual depois de habilitar o point-in-time
recovery na tabela "mod-pitr-table".

Guarda a lista de tabelas e, na tabela alvo, ARN, TableId e data de criação (recriada = valores
novos), chave, modo de cobrança, capacidade provisionada, stream, SSE, proteção contra remoção,
tags e TTL. O PITR (alvo) fica de fora. Fora também: TableStatus (UPDATING transitório) e
contadores voláteis (ItemCount, TableSizeBytes, datas de aumento/redução de capacidade).
"""

import json
import sys

import boto3

TABLE = "mod-pitr-table"


def main(region: str) -> dict:
    ddb = boto3.client("dynamodb", region_name=region)
    tables = ddb.list_tables().get("TableNames", [])
    snap: dict = {"tables": sorted(tables)}
    if TABLE not in tables:
        return snap
    t = ddb.describe_table(TableName=TABLE)["Table"]
    throughput = t.get("ProvisionedThroughput", {})
    snap["table"] = {
        "arn": t.get("TableArn"),
        "id": t.get("TableId"),
        "created": t.get("CreationDateTime"),
        "key_schema": t.get("KeySchema"),
        "attributes": sorted(t.get("AttributeDefinitions", []), key=lambda a: a["AttributeName"]),
        "billing": t.get("BillingModeSummary", {}).get("BillingMode"),
        "rcu": throughput.get("ReadCapacityUnits"),
        "wcu": throughput.get("WriteCapacityUnits"),
        "stream": t.get("StreamSpecification"),
        "sse": t.get("SSEDescription"),
        "deletion_protection": t.get("DeletionProtectionEnabled"),
    }
    tags = ddb.list_tags_of_resource(ResourceArn=t["TableArn"]).get("Tags", [])
    snap["tags"] = sorted((x["Key"], x["Value"]) for x in tags)
    ttl = ddb.describe_time_to_live(TableName=TABLE)["TimeToLiveDescription"]
    snap["ttl"] = [ttl.get("TimeToLiveStatus"), ttl.get("AttributeName")]
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
