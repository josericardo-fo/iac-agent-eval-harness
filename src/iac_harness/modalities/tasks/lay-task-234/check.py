"""Alguma VPC (não default) tem bloco CIDR IPv4 e um bloco IPv6 associado."""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = {}
    for vpc in ec2.describe_vpcs()["Vpcs"]:
        if vpc.get("IsDefault"):
            continue
        v6 = [
            a.get("Ipv6CidrBlock")
            for a in vpc.get("Ipv6CidrBlockAssociationSet", [])
            if a.get("Ipv6CidrBlockState", {}).get("State") in ("associated", "associating")
        ]
        vpcs[vpc["VpcId"]] = {"cidr": vpc.get("CidrBlock"), "ipv6": v6}
    return any(v["cidr"] and v["ipv6"] for v in vpcs.values()), {"vpcs": vpcs}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
