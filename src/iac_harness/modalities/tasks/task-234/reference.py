import boto3

ec2 = boto3.client("ec2")
ec2.create_vpc(CidrBlock="10.0.0.0/16", AmazonProvidedIpv6CidrBlock=True)
