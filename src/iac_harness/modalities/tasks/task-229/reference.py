import boto3

ec2 = boto3.client("ec2")
vpc = ec2.create_vpc(CidrBlock="192.168.0.0/22")["Vpc"]["VpcId"]
subnet1 = ec2.create_subnet(VpcId=vpc, CidrBlock="192.168.0.0/24")["Subnet"]["SubnetId"]
subnet2 = ec2.create_subnet(VpcId=vpc, CidrBlock="192.168.1.0/24")["Subnet"]["SubnetId"]
boto3.client("dax").create_subnet_group(SubnetGroupName="example", SubnetIds=[subnet1, subnet2])
