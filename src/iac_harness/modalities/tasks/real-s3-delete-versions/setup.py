"""Planta as versões e os delete markers do bucket (o Terraform não expressa isso).

logs/ e tmp/: duas versões por chave e um delete marker (como se `aws s3 rm --recursive` já
tivesse rodado). data/: duas versões por chave, sem marker. Roda uma vez, depois do apply.
"""

import sys

import boto3

BUCKET = "e13-s3-delete-versions-931493484341"
DELETED = ["logs/app.log", "logs/error.log", "tmp/cache.bin", "tmp/session.dat"]
LIVE = ["data/report.csv", "data/users.csv"]


def main(region: str) -> None:
    s3 = boto3.client("s3", region_name=region)
    for key in DELETED + LIVE:
        for n in (1, 2):
            s3.put_object(Bucket=BUCKET, Key=key, Body=f"{key} v{n}".encode())
    for key in DELETED:
        s3.delete_object(Bucket=BUCKET, Key=key)


if __name__ == "__main__":
    main(sys.argv[1])
