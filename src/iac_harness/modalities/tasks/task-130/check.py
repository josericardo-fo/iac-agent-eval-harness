"""Alguma VPC (não default) tem 1 subnet pública e 2 privadas, uma instância EC2 na pública,
outra numa privada e uma instância RDS cujo subnet group inclui a outra privada.

Pública = MapPublicIpOnLaunch ou rota 0.0.0.0/0 para internet gateway; privada = nenhuma.
"""

import json
import sys

import boto3

LIVE = ("pending", "running")


def _igw_subnets(ec2, vpc_id: str, subnet_ids: list[str]) -> set[str]:
    """Subnets cuja route table (explícita ou a main) tem 0.0.0.0/0 para um igw-."""
    tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    explicit, main_igw, out = {}, False, set()
    for rt in tables["RouteTables"]:
        igw = any(
            r.get("DestinationCidrBlock") == "0.0.0.0/0"
            and r.get("GatewayId", "").startswith("igw-")
            for r in rt.get("Routes", [])
        )
        for a in rt.get("Associations", []):
            if a.get("Main"):
                main_igw = main_igw or igw
            elif a.get("SubnetId"):
                explicit[a["SubnetId"]] = igw
    for sid in subnet_ids:
        if explicit.get(sid, main_igw):
            out.add(sid)
    return out


def main(region: str) -> tuple[bool, dict]:
    ec2 = boto3.client("ec2", region_name=region)
    rds = boto3.client("rds", region_name=region)
    instances = {}
    for res in ec2.describe_instances()["Reservations"]:
        for inst in res["Instances"]:
            if inst.get("State", {}).get("Name") in LIVE and inst.get("SubnetId"):
                instances.setdefault(inst["SubnetId"], []).append(inst["InstanceId"])
    dbs = {
        db["DBInstanceIdentifier"]: [
            s["SubnetIdentifier"] for s in db.get("DBSubnetGroup", {}).get("Subnets", [])
        ]
        for db in rds.describe_db_instances()["DBInstances"]
        if db.get("DBInstanceStatus") not in ("deleting", "failed")
    }
    details = {"instances_by_subnet": instances, "db_subnets": dbs, "vpcs": {}}
    for vpc in ec2.describe_vpcs()["Vpcs"]:
        if vpc.get("IsDefault"):
            continue
        vid = vpc["VpcId"]
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vid]}])["Subnets"]
        ids = [s["SubnetId"] for s in subnets]
        public = {s["SubnetId"] for s in subnets if s.get("MapPublicIpOnLaunch")}
        public |= _igw_subnets(ec2, vid, ids)
        private = [s for s in ids if s not in public]
        details["vpcs"][vid] = {"public": sorted(public), "private": private}
        web = any(s in instances for s in public)
        for app in (s for s in private if s in instances):
            others = {s for s in private if s != app}
            if web and any(others & set(sub) for sub in dbs.values()):
                return True, details
    return False, details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
