import json

import boto3

east = boto3.client("ec2")
west = boto3.client("ec2", region_name="us-west-2")


def tag(name):
    return [{"Name": "tag:Name", "Values": [name]}]


answer = {
    "elastic_ip": east.describe_addresses(Filters=tag("e13-charges-eip"))["Addresses"][0][
        "AllocationId"
    ],
    "volume": west.describe_volumes(Filters=tag("e13-charges-volume"))["Volumes"][0]["VolumeId"],
    "snapshot": west.describe_snapshots(OwnerIds=["self"], Filters=tag("e13-charges-snapshot"))[
        "Snapshots"
    ][0]["SnapshotId"],
}
with open("answer.json", "w") as fh:
    json.dump(answer, fh)
