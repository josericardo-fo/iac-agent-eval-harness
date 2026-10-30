"""Estado intocado: a VPC "e2-sg-vpc" tem exatamente os SGs do initial (default, e2-web-sg,
e2-api-sg) e o ingress de cada um, normalizado (sem ordem), é igual ao inicial."""

import json
import sys

import boto3

VPC_TAG = "e2-sg-vpc"
HTTP = frozenset({("tcp", 80, 80, "cidr", "0.0.0.0/0")})
SELF_ALL = frozenset({("-1", None, None, "sg", "self")})
# Ingress inicial de cada SG. O default vem vazio no Floci e com a autorreferência na AWS real.
INITIAL = {
    "default": (frozenset(), SELF_ALL),
    "e2-web-sg": (HTTP,),
    "e2-api-sg": (HTTP,),
}
PROTOCOLS = {"6": "tcp", "17": "udp", "1": "icmp"}


def _rules(group: dict) -> frozenset:
    """Ingress como conjunto de regras atômicas (protocolo, portas, tipo de origem, origem)."""
    out = set()
    for perm in group.get("IpPermissions", []):
        proto = PROTOCOLS.get(str(perm.get("IpProtocol")), str(perm.get("IpProtocol")))
        ports = (proto, perm.get("FromPort"), perm.get("ToPort"))
        out.update((*ports, "cidr", r.get("CidrIp")) for r in perm.get("IpRanges", []))
        out.update((*ports, "cidr6", r.get("CidrIpv6")) for r in perm.get("Ipv6Ranges", []))
        out.update((*ports, "prefix", r.get("PrefixListId")) for r in perm.get("PrefixListIds", []))
        for pair in perm.get("UserIdGroupPairs", []):
            ref = pair.get("GroupId")
            out.add((*ports, "sg", "self" if ref == group["GroupId"] else ref))
    return frozenset(out)


def main(region: str) -> tuple[bool, dict]:
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [VPC_TAG]}])["Vpcs"]
    if not vpcs:
        return False, {"vpc_exists": False}
    groups = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [v["VpcId"] for v in vpcs]}]
    )["SecurityGroups"]
    names = sorted(g["GroupName"] for g in groups)
    rules = {g["GroupName"]: sorted(map(list, _rules(g)), key=str) for g in groups}
    details = {"vpc_count": len(vpcs), "groups": names, "ingress": rules}
    ok = (
        len(vpcs) == 1
        and names == sorted(INITIAL)
        and all(_rules(g) in INITIAL[g["GroupName"]] for g in groups)
    )
    return ok, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
