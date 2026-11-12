"""SG "mod-web-sg" da VPC "mod-sg-vpc" é único, mantém a entrada TCP 80 e ganhou TCP 443,
ambas a partir de 0.0.0.0/0."""

import json
import sys

import boto3

VPC_TAG = "mod-sg-vpc"
GROUP = "mod-web-sg"


def _allows(perm: dict, port: int) -> bool:
    return (
        perm.get("IpProtocol") in ("tcp", "6")
        and perm.get("FromPort") == port
        and perm.get("ToPort") == port
        and any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", []))
    )


def main(region: str) -> tuple[bool, dict]:
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [VPC_TAG]}])["Vpcs"]
    if not vpcs:
        return False, {"vpc_exists": False}
    vpc_ids = [v["VpcId"] for v in vpcs]
    groups = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [GROUP]},
            {"Name": "vpc-id", "Values": vpc_ids},
        ]
    )["SecurityGroups"]
    if not groups:
        return False, {"vpc_exists": True, "group_exists": False, "vpc_ids": vpc_ids}
    ingress = {
        g["GroupId"]: [
            {k: p.get(k) for k in ("IpProtocol", "FromPort", "ToPort", "IpRanges")}
            for p in g.get("IpPermissions", [])
        ]
        for g in groups
    }
    # O enunciado proíbe criar outro grupo e pede manter as regras existentes.
    perms = groups[0].get("IpPermissions", [])
    single = len(groups) == 1
    keeps_http = any(_allows(p, 80) for p in perms)
    has_https = any(_allows(p, 443) for p in perms)
    details = {
        "vpc_exists": True,
        "group_count": len(groups),
        "keeps_http": keeps_http,
        "has_https": has_https,
        "ingress": ingress,
    }
    return single and keeps_http and has_https, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
