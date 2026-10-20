"""Fila "mod-retention-queue" existe e retém mensagens por 345600 s (4 dias)."""

import json
import sys

import boto3

QUEUE = "mod-retention-queue"


def main(region: str) -> tuple[bool, dict]:
    sqs = boto3.client("sqs", region_name=region)
    urls = sqs.list_queues(QueueNamePrefix=QUEUE).get("QueueUrls", [])
    matching = [u for u in urls if u.rstrip("/").rsplit("/", 1)[-1] == QUEUE]
    if not matching:
        return False, {"queue_exists": False, "queues": urls}
    attrs = sqs.get_queue_attributes(
        QueueUrl=matching[0], AttributeNames=["MessageRetentionPeriod"]
    )["Attributes"]
    retention = attrs.get("MessageRetentionPeriod")
    return retention == "345600", {"queue_exists": True, "retention": retention}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
