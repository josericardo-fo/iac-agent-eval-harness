import boto3

ec2 = boto3.client("ec2")
vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
azs = ec2.describe_availability_zones(Filters=[{"Name": "state", "Values": ["available"]}])
names = [az["ZoneName"] for az in azs["AvailabilityZones"]]


def subnet(cidr: str, az: str) -> str:
    return ec2.create_subnet(VpcId=vpc, CidrBlock=cidr, AvailabilityZone=az)["Subnet"]["SubnetId"]


public = subnet("10.0.1.0/24", names[0])
ec2.modify_subnet_attribute(SubnetId=public, MapPublicIpOnLaunch={"Value": True})
private1 = subnet("10.0.2.0/24", names[1])
private2 = subnet("10.0.3.0/24", names[2])
sg = ec2.create_security_group(VpcId=vpc, GroupName="web_sg", Description="web_sg")["GroupId"]
images = ec2.describe_images(
    Owners=["amazon"],
    Filters=[{"Name": "name", "Values": ["*ubuntu-noble-24.04-amd64-server-*"]}],
)["Images"]
ami = max(images, key=lambda i: i.get("CreationDate", ""))["ImageId"]
ec2.run_instances(
    ImageId=ami, InstanceType="t2.micro", SubnetId=public, SecurityGroupIds=[sg],
    MinCount=1, MaxCount=1,
)  # fmt: skip
ec2.run_instances(ImageId=ami, InstanceType="t2.micro", SubnetId=private1, MinCount=1, MaxCount=1)
rds = boto3.client("rds")
rds.create_db_subnet_group(
    DBSubnetGroupName="mydb-subnet-group",
    DBSubnetGroupDescription="mydb-subnet-group",
    SubnetIds=[private1, private2],
)
rds.create_db_instance(
    DBInstanceIdentifier="mydb",
    Engine="mysql",
    DBInstanceClass="db.t3.micro",
    AllocatedStorage=20,
    MasterUsername="admin",
    MasterUserPassword="your-password",
    DBSubnetGroupName="mydb-subnet-group",
)
