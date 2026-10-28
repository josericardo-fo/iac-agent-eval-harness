"""Algum ElastiCache user group da região tem exatamente 3 usuários existentes.

Conta UserIds mais as adições pendentes (menos as remoções pendentes), para aceitar grupo ainda
em "creating"/"modifying"; grupo em "deleting" não conta. O usuário "default" da conta conta como
usuário se estiver no grupo (o enunciado não exclui).
"""

import json
import sys

import boto3


def _pages(client, op: str, key: str) -> list[dict]:
    return [item for page in client.get_paginator(op).paginate() for item in page.get(key, [])]


def main(region: str) -> tuple[bool, dict]:
    ec = boto3.client("elasticache", region_name=region)
    users = {u["UserId"] for u in _pages(ec, "describe_users", "Users")}
    groups = {}
    for g in _pages(ec, "describe_user_groups", "UserGroups"):
        pending = g.get("PendingChanges", {})
        members = (set(g.get("UserIds", [])) | set(pending.get("UserIdsToAdd", []))) - set(
            pending.get("UserIdsToRemove", [])
        )
        groups[g["UserGroupId"]] = {
            "status": g.get("Status"),
            "members": sorted(members),
            "existing": sorted(members & users),
        }
    ok = any(
        g["status"] != "deleting" and len(g["members"]) == 3 and len(g["existing"]) == 3
        for g in groups.values()
    )
    return ok, {"user_groups": groups, "users": sorted(users)}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
