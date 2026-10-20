"""Usuário "del-svc-legacy" não existe; grupo "del-ops" existe com ReadOnlyAccess anexada."""

import json
import sys

import boto3

USER = "del-svc-legacy"
GROUP = "del-ops"
POLICY_ARN = "arn:aws:iam::aws:policy/ReadOnlyAccess"


def main(region: str) -> tuple[bool, dict]:
    iam = boto3.client("iam", region_name=region)
    users = [u["UserName"] for u in iam.list_users().get("Users", [])]
    groups = [g["GroupName"] for g in iam.list_groups().get("Groups", [])]
    user_removed = USER not in users
    if GROUP not in groups:
        return False, {"user_removed": user_removed, "group_exists": False, "groups": groups}
    attached = [
        p["PolicyArn"]
        for p in iam.list_attached_group_policies(GroupName=GROUP).get("AttachedPolicies", [])
    ]
    details = {"user_removed": user_removed, "group_exists": True, "attached": attached}
    return user_removed and POLICY_ARN in attached, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
