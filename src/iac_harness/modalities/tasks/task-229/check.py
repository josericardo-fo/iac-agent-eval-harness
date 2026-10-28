"""Algum subnet group do DAX na região, fora do "default*" da AWS, com pelo menos 1 subnet.

O enunciado não fixa nome, VPC nem número de subnets: vale qualquer grupo criado.
"""

import json
import sys

import boto3


def main(region: str) -> tuple[bool, dict]:
    dax = boto3.client("dax", region_name=region)
    groups, token = {}, None
    while True:
        page = dax.describe_subnet_groups(**({"NextToken": token} if token else {}))
        for g in page.get("SubnetGroups", []):
            groups[g["SubnetGroupName"]] = {
                "vpc": g.get("VpcId"),
                "subnets": [s.get("SubnetIdentifier") for s in g.get("Subnets", [])],
            }
        token = page.get("NextToken")
        if not token:
            break
    ok = any(not n.startswith("default") and g["subnets"] for n, g in groups.items())
    return ok, {"subnet_groups": groups}


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
