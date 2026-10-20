import boto3

ec2 = boto3.client("ec2")
vpc = ec2.create_vpc(CidrBlock="192.168.0.0/22")["Vpc"]["VpcId"]
azs = ec2.describe_availability_zones(Filters=[{"Name": "state", "Values": ["available"]}])
subnets = [
    ec2.create_subnet(VpcId=vpc, AvailabilityZone=az["ZoneName"], CidrBlock=f"192.168.{i}.0/24")[
        "Subnet"
    ]["SubnetId"]
    for i, az in enumerate(azs["AvailabilityZones"][:3])
]
sg = ec2.create_security_group(VpcId=vpc, GroupName="msk-sg", Description="MSK brokers")["GroupId"]
boto3.client("kafka").create_cluster(
    ClusterName="example",
    KafkaVersion="3.2.0",
    NumberOfBrokerNodes=3,
    BrokerNodeGroupInfo={
        "InstanceType": "kafka.t3.small",
        "ClientSubnets": subnets,
        "SecurityGroups": [sg],
        "StorageInfo": {"EbsStorageInfo": {"VolumeSize": 1000}},
    },
)
