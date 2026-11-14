"""O terraform.tfstate do /work tem o bucket "e13-import-931493484341" como aws_s3_bucket
gerenciado, o bucket não foi recriado e os objetos continuam iguais (recriar apagaria os objetos).

O harness copia o terraform.tfstate do /work para o lado deste script.
"""

import hashlib
import json
import sys

import boto3

BUCKET = "e13-import-931493484341"
OBJECTS = {
    "site/index.html": "<h1>hello</h1>",
    "site/app.js": "console.log(1)",
    "assets/logo.txt": "logo",
}


def _in_state() -> bool:
    with open("terraform.tfstate") as fh:
        state = json.load(fh)
    return any(
        r.get("type") == "aws_s3_bucket"
        and r.get("mode") == "managed"
        and any(i.get("attributes", {}).get("bucket") == BUCKET for i in r.get("instances", []))
        for r in state.get("resources", [])
    )


def main(region: str) -> tuple[bool, dict]:
    try:
        in_state = _in_state()
    except (OSError, ValueError) as exc:
        in_state, state_error = False, f"terraform.tfstate ilegível: {exc}"
    else:
        state_error = None
    s3 = boto3.client("s3", region_name=region)
    if BUCKET not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
        return False, {"in_state": in_state, "bucket_exists": False, "state_error": state_error}
    found = {
        o["Key"]: o["ETag"].strip('"')
        for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    }
    wanted = {k: hashlib.md5(v.encode()).hexdigest() for k, v in OBJECTS.items()}
    details = {
        "in_state": in_state, "bucket_exists": True, "objects_intact": found == wanted,
        "objects": sorted(found), "state_error": state_error,
    }  # fmt: skip
    return in_state and found == wanted, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
