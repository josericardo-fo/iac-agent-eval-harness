"""Remoção de armazenamento de objetos: S3 e Glacier."""

from __future__ import annotations

from typing import Any


def _empty_bucket(session: Any, name: str) -> Any:
    """Esvazia versões e delete markers; devolve o client da região do bucket."""
    region = session.client("s3").get_bucket_location(Bucket=name).get("LocationConstraint")
    s3 = session.client("s3", region_name=region or "us-east-1")
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=name):
        objs = [
            {"Key": o["Key"], "VersionId": o["VersionId"]}
            for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if objs:
            s3.delete_objects(Bucket=name, Delete={"Objects": objs, "Quiet": True})
    return s3


def deleters(session: Any) -> dict[str, Any]:
    """S3 e Glacier: categoria → função que apaga um item."""

    def glacier_vault(name: str) -> None:
        glacier = session.client("glacier")
        glacier.delete_vault_notifications(vaultName=name)
        glacier.delete_vault(vaultName=name)

    def s3_bucket(name: str) -> None:
        _empty_bucket(session, name).delete_bucket(Bucket=name)

    return {
        "glacier_vaults": glacier_vault,
        "s3_buckets": s3_bucket,
    }
