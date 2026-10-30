import boto3

ec2 = boto3.client("ec2")
vpc_id = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["e2-sg-vpc"]}])["Vpcs"][0][
    "VpcId"
]
sg_id = ec2.describe_security_groups(
    Filters=[
        {"Name": "group-name", "Values": ["e2-web-sg"]},
        {"Name": "vpc-id", "Values": [vpc_id]},
    ]
)["SecurityGroups"][0]["GroupId"]
ec2.authorize_security_group_ingress(
    GroupId=sg_id,
    IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
    ],
)
