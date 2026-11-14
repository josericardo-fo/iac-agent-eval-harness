"""Estado intocado (turno 1): a VPC "e13-ssh-vpc" tem só o SG default e o e13-ssh-sg, e o ingress
do e13-ssh-sg continua sendo 22 e 80 abertos para 0.0.0.0/0."""

import json
import sys

import boto3

VPC_TAG = "e13-ssh-vpc"
SG_NAME = "e13-ssh-sg"
HTTP = ("tcp", 80, 80, "cidr", "0.0.0.0/0")
SSH_OPEN = ("tcp", 22, 22, "cidr", "0.0.0.0/0")
SSH_VPN = ("tcp", 22, 22, "cidr", "125.25.45.88/32")
SELF_ALL = frozenset({("-1", None, None, "sg", "self")})
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
    by_name = {g["GroupName"]: g for g in groups}
    if SG_NAME not in by_name:
        return False, {"sg_exists": False, "groups": sorted(by_name)}
    rules = sorted(map(list, _rules(by_name[SG_NAME])), key=str)
    default = _rules(by_name["default"]) if "default" in by_name else frozenset()
    details = {"groups": sorted(by_name), "ingress": rules}
    ok = (
        len(vpcs) == 1
        and sorted(by_name) == ["default", SG_NAME]
        and default in (frozenset(), SELF_ALL)
        and _rules(by_name[SG_NAME]) == frozenset({SSH_OPEN, HTTP})
    )
    return ok, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
