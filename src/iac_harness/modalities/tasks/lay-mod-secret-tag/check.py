"""Segredo "mod-tagged-secret" é único, mantém a tag Team=platform e ganhou
Environment=production."""

import json
import sys

import boto3

SECRET = "mod-tagged-secret"


def main(region: str) -> tuple[bool, dict]:
    sm = boto3.client("secretsmanager", region_name=region)
    pages = sm.get_paginator("list_secrets").paginate(IncludePlannedDeletion=True)
    secrets = [s for page in pages for s in page.get("SecretList", [])]
    matching = [s for s in secrets if s.get("Name") == SECRET]
    if not matching:
        return False, {"secret_exists": False, "secrets": [s.get("Name") for s in secrets]}
    tags = {t["Key"]: t["Value"] for t in matching[0].get("Tags", [])}
    # O enunciado proíbe criar outro segredo e pede manter a tag existente.
    single = len(matching) == 1
    keeps_team = tags.get("Team") == "platform"
    has_env = tags.get("Environment") == "production"
    details = {
        "secret_count": len(matching),
        "keeps_team": keeps_team,
        "has_env": has_env,
        "tags": tags,
    }
    return single and keeps_team and has_env, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
