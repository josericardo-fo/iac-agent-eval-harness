"""A resposta (answer.json) cita os 3 recursos que ainda cobram e os 3 continuam existindo.

Recursos achados pela tag Name: EIP solto em us-east-1 (AllocationId, ou o IP público), volume
e snapshot em us-west-2. Qualquer string do JSON, em qualquer nível, vale como menção. A
consulta não pode apagar nem alterar nada: recurso sumido reprova.
"""

import json
import sys

import boto3

WEST = "us-west-2"
EIP_TAG, VOLUME_TAG, SNAPSHOT_TAG = "e13-charges-eip", "e13-charges-volume", "e13-charges-snapshot"


def _strings(value) -> list[str]:
    """Todas as strings do JSON, chaves e valores, em qualquer nível."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _tag(name: str) -> list[dict]:
    return [{"Name": "tag:Name", "Values": [name]}]


def main(region: str) -> tuple[bool, dict]:
    east = boto3.client("ec2", region_name=region)
    west = boto3.client("ec2", region_name=WEST)
    eips = east.describe_addresses(Filters=_tag(EIP_TAG))["Addresses"]
    volumes = west.describe_volumes(Filters=_tag(VOLUME_TAG))["Volumes"]
    snapshots = west.describe_snapshots(OwnerIds=["self"], Filters=_tag(SNAPSHOT_TAG))["Snapshots"]
    details: dict = {
        "eip_exists": bool(eips), "volume_exists": bool(volumes),
        "snapshot_exists": bool(snapshots),
    }  # fmt: skip
    if not (eips and volumes and snapshots):
        return False, details
    try:
        with open("answer.json") as fh:
            text = _strings(json.load(fh))
    except (OSError, ValueError) as exc:
        return False, {**details, "error": f"answer.json ilegível: {exc}"}
    wanted = {
        "eip": {eips[0]["AllocationId"], eips[0].get("PublicIp")} - {None},
        "volume": {volumes[0]["VolumeId"]},
        "snapshot": {snapshots[0]["SnapshotId"]},
    }
    mentioned = {k: any(w in s for s in text for w in ids) for k, ids in wanted.items()}
    details["mentioned"] = mentioned
    details["expected"] = {k: sorted(v) for k, v in wanted.items()}
    return all(mentioned.values()), details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
