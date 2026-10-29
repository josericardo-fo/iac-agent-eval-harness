"""Retrato do efeito colateral: o que deve ficar igual depois de mudar a retenção da fila
"mod-retention-queue".

Guarda a lista de filas, a URL e todos os atributos da fila (ARN, CreatedTimestamp: recriada =
valores novos) menos MessageRetentionPeriod (alvo), LastModifiedTimestamp (muda com qualquer
SetQueueAttributes) e os contadores ApproximateNumberOfMessages* (voláteis). Guarda também as tags.

Limite: URL e ARN não mudam ao recriar; a recriação aparece pelo CreatedTimestamp (resolução de
segundo no Floci 2.1.0) ou por atributos com outro padrão na fila nova.
"""

import json
import sys

import boto3

QUEUE = "mod-retention-queue"
EXCLUDED = {"MessageRetentionPeriod", "LastModifiedTimestamp"}


def main(region: str) -> dict:
    sqs = boto3.client("sqs", region_name=region)
    urls = sorted(sqs.list_queues().get("QueueUrls", []))
    snap: dict = {"queues": urls}
    matching = [u for u in urls if u.rstrip("/").rsplit("/", 1)[-1] == QUEUE]
    if not matching:
        return snap
    attrs = sqs.get_queue_attributes(QueueUrl=matching[0], AttributeNames=["All"])["Attributes"]
    snap["attributes"] = {
        k: v
        for k, v in sorted(attrs.items())
        if k not in EXCLUDED and not k.startswith("ApproximateNumberOfMessages")
    }
    snap["tags"] = sqs.list_queue_tags(QueueUrl=matching[0]).get("Tags", {})
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
