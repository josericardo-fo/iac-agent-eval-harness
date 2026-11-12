"""Retrato do efeito colateral: o que deve ficar igual depois de abrir TCP 443 no SG "mod-web-sg".

Guarda todas as VPCs da região e todos os SGs (nome + VPC, inclusive os default), para pegar
VPC/SG novo em qualquer lugar; as VPCs com a tag (id), os SGs da VPC (nome → GroupId; recriado =
id novo), e, no SG alvo, descrição, tags, regras de entrada menos a tcp/443 (alvo) e regras de
saída. As regras são normalizadas (protocolo "6" = "tcp", só CIDRs e grupos) e ordenadas.
"""

import json
import sys

import boto3

VPC_TAG = "mod-sg-vpc"
GROUP = "mod-web-sg"
TARGET_PORT = 443


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
    snap["groups"] = sorted([g["GroupName"], g["GroupId"]] for g in groups)
    for g in groups:
        if g["GroupName"] != GROUP:
            continue
        ingress = [
            p
            for p in g.get("IpPermissions", [])
            if not (
                p.get("IpProtocol") in ("tcp", "6")
                and p.get("FromPort") == TARGET_PORT
                and p.get("ToPort") == TARGET_PORT
            )
        ]
        snap[g["GroupId"]] = {
            "description": g.get("Description"),
            "tags": sorted((t["Key"], t["Value"]) for t in g.get("Tags", [])),
            "ingress": _rules(ingress),
            "egress": _rules(g.get("IpPermissionsEgress", [])),
        }
    return snap


if __name__ == "__main__":
    try:
        out = {"snapshot": main(sys.argv[1])}
    except Exception as exc:  # o retrato nunca pode quebrar: erro vira saída sem "snapshot"
        out = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(out, default=str, sort_keys=True))
