"""Algum stream do Kinesis Video existe na região, fora de remoção.

O enunciado não fixa nome, retenção nem media type: vale qualquer stream ("CREATING" conta,
porque o enunciado só pede a criação).
"""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    kv = boto3.client("kinesisvideo", region_name=region)
    streams = {}
    for page in kv.get_paginator("list_streams").paginate():
        for s in page.get("StreamInfoList", []):
            streams[s["StreamName"]] = s.get("Status")
    ok = any(status != "DELETING" for status in streams.values())
    return ok, {"streams": streams}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
