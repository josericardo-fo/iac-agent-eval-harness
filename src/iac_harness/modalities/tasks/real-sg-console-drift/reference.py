import boto3

ec2 = boto3.client("ec2")
vpc_id = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["e13-drift-vpc"]}])["Vpcs"][0][
    "VpcId"
]
sg_id = ec2.describe_security_groups(
    Filters=[
        {"Name": "group-name", "Values": ["e13-drift-sg"]},
        {"Name": "vpc-id", "Values": [vpc_id]},
    ]
)["SecurityGroups"][0]["GroupId"]
ec2.revoke_security_group_ingress(
    GroupId=sg_id,
    IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ],
)
