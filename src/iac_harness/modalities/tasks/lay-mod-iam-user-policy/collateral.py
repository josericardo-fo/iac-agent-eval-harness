"""Retrato do efeito colateral: o que deve ficar igual depois de anexar a ReadOnlyAccess ao
usuário "mod-readonly-user".

Guarda a lista de usuários e, no usuário alvo, UserId, Arn, CreateDate (recriado = valores
novos), path, tags, grupos, políticas inline e as políticas gerenciadas anexadas menos a
ReadOnlyAccess (alvo).
"""

import json
import sys

import boto3

USER = "mod-readonly-user"
POLICY_ARN = "arn:aws:iam::aws:policy/ReadOnlyAccess"


def main(region: str) -> dict:
    iam = boto3.client("iam", region_name=region)
    users = iam.list_users().get("Users", [])
    snap: dict = {"users": sorted(u["UserName"] for u in users)}
    if USER not in snap["users"]:
        return snap
    u = iam.get_user(UserName=USER)["User"]
    attached = iam.list_attached_user_policies(UserName=USER).get("AttachedPolicies", [])
    snap["user"] = {
        "id": u.get("UserId"),
        "arn": u.get("Arn"),
        "created": u.get("CreateDate"),
        "path": u.get("Path"),
        "tags": sorted((t["Key"], t["Value"]) for t in u.get("Tags", [])),
        "groups": sorted(
            g["GroupName"] for g in iam.list_groups_for_user(UserName=USER).get("Groups", [])
        ),
        "inline": sorted(iam.list_user_policies(UserName=USER).get("PolicyNames", [])),
        "attached": sorted(p["PolicyArn"] for p in attached if p["PolicyArn"] != POLICY_ARN),
    }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
