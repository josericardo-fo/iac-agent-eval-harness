import boto3

ec2 = boto3.client("ec2")
az = ec2.describe_availability_zones(
    Filters=[
        {"Name": "state", "Values": ["available"]},
        {"Name": "opt-in-status", "Values": ["opt-in-not-required"]},
    ]
)["AvailabilityZones"][0]["ZoneName"]
ls = boto3.client("lightsail")
ls.create_disk(diskName="test-disk", sizeInGb=8, availabilityZone=az)
ls.create_instances(
    instanceNames=["test-instance"],
    availabilityZone=az,
    blueprintId="amazon_linux_2",
    bundleId="nano_1_0",
)
ls.attach_disk(diskName="test-disk", instanceName="test-instance", diskPath="/dev/xvdf")
