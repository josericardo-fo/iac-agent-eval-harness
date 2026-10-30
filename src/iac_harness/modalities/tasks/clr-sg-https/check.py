"""SG "e2-web-sg" da VPC "e2-sg-vpc" é único, mantém TCP 80 e ganhou TCP 443; "e2-api-sg"
continua só com TCP 80; nenhum outro SG da VPC (como o default) abre TCP 443."""

import json
import sys

import boto3

VPC_TAG = "e2-sg-vpc"
GROUPS = ["e2-web-sg", "e2-api-sg"]


def _allows(perm: dict, port: int) -> bool:
    return (
        perm.get("IpProtocol") in ("tcp", "6")
        and perm.get("FromPort") == port
        and perm.get("ToPort") == port
        and any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", []))
    )


def _ports(region: str) -> tuple[dict | None, dict]:
    """Nome do SG → {"count", "http", "https"}; None (com detalhes) se a VPC ou um SG falta."""
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [VPC_TAG]}])["Vpcs"]
    if not vpcs:
        return None, {"vpc_exists": False}
    vpc_ids = [v["VpcId"] for v in vpcs]
    groups = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": vpc_ids}])[
        "SecurityGroups"
    ]
    out = {}
    for name in GROUPS:
        found = [g for g in groups if g["GroupName"] == name]
        if not found:
            return None, {"vpc_exists": True, "group_missing": name}
        perms = found[0].get("IpPermissions", [])
        out[name] = {
            "count": len(found),
            "http": any(_allows(p, 80) for p in perms),
            "https": any(_allows(p, 443) for p in perms),
        }
    others = [g["GroupName"] for g in groups if g["GroupName"] not in GROUPS]
    out["others_https"] = [
        g["GroupName"]
        for g in groups
        if g["GroupName"] not in GROUPS and any(_allows(p, 443) for p in g.get("IpPermissions", []))
    ]
    return out, {"vpc_exists": True, "groups": out, "others": others}


def main(region: str) -> tuple[bool, dict]:
    ports, details = _ports(region)
    if ports is None:
        return False, details
    web, api = ports["e2-web-sg"], ports["e2-api-sg"]
    ok = (
        web["count"] == 1
        and web["http"]
        and web["https"]
        and api["count"] == 1
        and api["http"]
        and not api["https"]
        and not ports["others_https"]
    )
    return ok, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
