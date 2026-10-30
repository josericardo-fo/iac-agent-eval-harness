"""Estado intocado: as 3 filas continuam existindo."""

import json
import sys

import boto3

QUEUES = ["e2-orders-dev", "e2-orders-prod", "e2-payments-prod"]


def _names(region: str) -> list[str]:
    sqs = boto3.client("sqs", region_name=region)
    urls = sqs.list_queues(QueueNamePrefix="e2-").get("QueueUrls", [])
    return sorted(u.rstrip("/").rsplit("/", 1)[-1] for u in urls)


def main(region: str) -> tuple[bool, dict]:
    names = _names(region)
    missing = [q for q in QUEUES if q not in names]
    return not missing, {"missing": missing, "queues": names}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
