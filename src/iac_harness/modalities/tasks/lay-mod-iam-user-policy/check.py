"""Usuário IAM "mod-readonly-user" existe e tem a política gerenciada ReadOnlyAccess anexada."""

import json
import sys

import boto3

USER = "mod-readonly-user"
POLICY_ARN = "arn:aws:iam::aws:policy/ReadOnlyAccess"


def main(region: str) -> tuple[bool, dict]:
    iam = boto3.client("iam", region_name=region)
    users = [u["UserName"] for u in iam.list_users().get("Users", [])]
    if USER not in users:
        return False, {"user_exists": False, "users": users}
    attached = [
        p["PolicyArn"]
        for p in iam.list_attached_user_policies(UserName=USER).get("AttachedPolicies", [])
    ]
    return POLICY_ARN in attached, {"user_exists": True, "attached": attached}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
