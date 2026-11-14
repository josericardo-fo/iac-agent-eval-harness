"""Retrato do efeito colateral: a consulta não pode apagar, criar nem alterar nada.

Guarda, em us-east-1 e em us-west-2, os EIPs (alocação, associação, tags), os volumes (id,
tamanho, tipo, estado, tags), os snapshots próprios (id, volume de origem, tags) e as
instâncias. A resposta (answer.json) não entra.
"""

import json
import sys

import boto3

REGIONS = ("us-east-1", "us-west-2")


def _tags(item: dict) -> list:
    return sorted((t["Key"], t["Value"]) for t in item.get("Tags", []))


def main(region: str) -> dict:
    snap: dict = {}
    for name in REGIONS:
        ec2 = boto3.client("ec2", region_name=name)
        snap[name] = {
            "eips": sorted(
                [a["AllocationId"], a.get("AssociationId"), _tags(a)]
                for a in ec2.describe_addresses()["Addresses"]
            ),
            "volumes": sorted(
                [v["VolumeId"], v["Size"], v["VolumeType"], v["State"], _tags(v)]
                for v in ec2.describe_volumes()["Volumes"]
            ),
            "snapshots": sorted(
                [s["SnapshotId"], s.get("VolumeId"), _tags(s)]
                for s in ec2.describe_snapshots(OwnerIds=["self"])["Snapshots"]
            ),
            "instances": sorted(
                i["InstanceId"]
                for r in ec2.describe_instances()["Reservations"]
                for i in r["Instances"]
            ),
        }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
