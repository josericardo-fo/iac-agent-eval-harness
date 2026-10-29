"""Retrato do efeito colateral: a consulta não pode criar, mudar nem remover nada.

Guarda todas as VPCs da região e todos os SGs (nome + VPC, inclusive os default), para pegar
VPC/SG novo em qualquer lugar; as VPCs com a tag (id) e, em cada SG da VPC, GroupId (recriado =
id novo), descrição, tags e as regras de entrada e saída normalizadas (protocolo "6" = "tcp", só
CIDRs e grupos) e ordenadas. A resposta (answer.json) não entra.
"""

import json
import sys

import boto3

VPC_TAG = "query-sg-vpc"


def _rules(perms: list[dict]) -> list:
    out = []
    for p in perms:
        proto = "tcp" if p.get("IpProtocol") == "6" else p.get("IpProtocol")
        cidrs = sorted(r.get("CidrIp") for r in p.get("IpRanges", []))
        cidrs6 = sorted(r.get("CidrIpv6") for r in p.get("Ipv6Ranges", []))
        groups = sorted(g.get("GroupId") for g in p.get("UserIdGroupPairs", []))
        out.append([proto, p.get("FromPort"), p.get("ToPort"), cidrs, cidrs6, groups])
    return sorted(out, key=json.dumps)


def main(region: str) -> dict:
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [VPC_TAG]}])["Vpcs"]
    vpc_ids = sorted(v["VpcId"] for v in vpcs)
    snap: dict = {"vpcs": vpc_ids}
    # Região inteira: VPC ou SG criado fora da VPC da tarefa também é efeito colateral.
    snap["all_vpcs"] = sorted(v["VpcId"] for v in ec2.describe_vpcs()["Vpcs"])
    snap["all_groups"] = sorted(
        [g["GroupName"], g.get("VpcId")] for g in ec2.describe_security_groups()["SecurityGroups"]
    )
    if not vpc_ids:
        return snap
    groups = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": vpc_ids}])[
        "SecurityGroups"
    ]
    snap["groups"] = {
        g["GroupName"]: {
            "id": g["GroupId"],
            "description": g.get("Description"),
            "tags": sorted((t["Key"], t["Value"]) for t in g.get("Tags", [])),
            "ingress": _rules(g.get("IpPermissions", [])),
            "egress": _rules(g.get("IpPermissionsEgress", [])),
        }
        for g in sorted(groups, key=lambda g: g["GroupId"])
    }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
