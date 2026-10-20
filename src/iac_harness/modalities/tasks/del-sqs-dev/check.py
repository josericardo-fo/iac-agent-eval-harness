"""As 3 filas com tag env=dev não existem e as 2 filas env=prod continuam existindo."""

import json
import sys

import boto3

DEV = ["del-q-orders-dev", "del-q-events-dev", "del-q-jobs-dev"]
PROD = ["del-q-orders-prod", "del-q-events-prod"]


def main(region: str) -> tuple[bool, dict]:
    sqs = boto3.client("sqs", region_name=region)
    urls = sqs.list_queues(QueueNamePrefix="del-q-").get("QueueUrls", [])
    names = sorted(u.rstrip("/").rsplit("/", 1)[-1] for u in urls)
    dev_left = [q for q in DEV if q in names]
    prod_missing = [q for q in PROD if q not in names]
    details = {"dev_left": dev_left, "prod_missing": prod_missing, "queues": names}
    return not dev_left and not prod_missing, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
