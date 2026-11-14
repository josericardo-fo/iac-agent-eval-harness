import boto3

ec2 = boto3.client("ec2")
vpc_id = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["e13-ssh-vpc"]}])["Vpcs"][0][
    "VpcId"
]
sg_id = ec2.describe_security_groups(
    Filters=[
        {"Name": "group-name", "Values": ["e13-ssh-sg"]},
        {"Name": "vpc-id", "Values": [vpc_id]},
    ]
)["SecurityGroups"][0]["GroupId"]
ssh = {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22}
ec2.revoke_security_group_ingress(
    GroupId=sg_id, IpPermissions=[{**ssh, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
)
ec2.authorize_security_group_ingress(
    GroupId=sg_id, IpPermissions=[{**ssh, "IpRanges": [{"CidrIp": "125.25.45.88/32"}]}]
)
