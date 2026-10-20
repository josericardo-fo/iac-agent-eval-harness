import json

import boto3

ec2 = boto3.client("ec2")
vpc_id = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["query-sg-vpc"]}])["Vpcs"][0][
    "VpcId"
]
groups = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])[
    "SecurityGroups"
]


def ssh_open(perm):
    return (
        perm.get("IpProtocol") == "tcp"
        and perm.get("FromPort", -1) <= 22 <= perm.get("ToPort", -1)
        and any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", []))
    )


names = [g["GroupName"] for g in groups if any(ssh_open(p) for p in g["IpPermissions"])]
with open("answer.json", "w") as fh:
    json.dump({"security_groups": names}, fh)
