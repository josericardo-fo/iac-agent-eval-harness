"""Cria a regra 22/0.0.0.0/0 no SG "e13-drift-sg" fora do Terraform (o "console" do post).

Roda uma vez, depois do apply inicial, e nunca vai para o /work: o tfstate do agente não
conhece esta regra.
"""

import sys

import boto3


def main(region: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    vpc = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["e13-drift-vpc"]}])["Vpcs"][0]
    sg_id = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": ["e13-drift-sg"]},
            {"Name": "vpc-id", "Values": [vpc["VpcId"]]},
        ]
    )["SecurityGroups"][0]["GroupId"]
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": 22,
                "ToPort": 22,
                "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            }
        ],
    )


if __name__ == "__main__":
    main(sys.argv[1])
