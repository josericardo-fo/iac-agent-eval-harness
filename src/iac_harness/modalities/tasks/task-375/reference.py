import boto3

ec2 = boto3.client("ec2")
az = ec2.describe_availability_zones(
    Filters=[
        {"Name": "state", "Values": ["available"]},
        {"Name": "opt-in-status", "Values": ["opt-in-not-required"]},
    ]
)["AvailabilityZones"][0]["ZoneName"]
ls = boto3.client("lightsail")
for name in ("test-disk-1", "test-disk-2"):
    ls.create_disk(diskName=name, sizeInGb=8, availabilityZone=az)
ls.create_instances(
    instanceNames=["test-instance"],
    availabilityZone=az,
    blueprintId="amazon_linux_2",
    bundleId="nano_1_0",
)
for name, path in (("test-disk-1", "/dev/xvdf"), ("test-disk-2", "/dev/xvdg")):
    ls.attach_disk(diskName=name, instanceName="test-instance", diskPath=path)
