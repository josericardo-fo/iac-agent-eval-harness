"""Algum cluster MSK (provisionado) da região tem 3 broker nodes e não está em FAILED/DELETING."""

import json
import sys

import boto3

BAD_STATES = ("FAILED", "DELETING")


def main(region: str) -> tuple[bool, dict]:
    kafka = boto3.client("kafka", region_name=region)
    clusters = {
        c["ClusterName"]: {"brokers": c.get("NumberOfBrokerNodes"), "state": c.get("State")}
        for c in kafka.list_clusters().get("ClusterInfoList", [])
    }
    ok = any(c["brokers"] == 3 and c["state"] not in BAD_STATES for c in clusters.values())
    return ok, {"clusters": clusters}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
