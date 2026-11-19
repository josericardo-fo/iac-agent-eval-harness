"""Remoção dos demais serviços: Lightsail, Lambda, CloudFront, CloudWatch, SageMaker,
Kinesis Video, SQS, SNS, Secrets Manager, SSM e KMS."""

from __future__ import annotations

import time
from typing import Any

from .common import KMS_PENDING_DAYS, WAIT_TIMEOUT_S, _pages, _poll_gone


def _delete_cloudfront_distribution(cloudfront: Any, dist_id: str) -> None:
    """Desabilita (se preciso), espera o Deployed e apaga com o ETag atual."""
    current = cloudfront.get_distribution_config(Id=dist_id)
    config = current["DistributionConfig"]
    if config["Enabled"]:
        config["Enabled"] = False
        cloudfront.update_distribution(
            Id=dist_id, IfMatch=current["ETag"], DistributionConfig=config
        )
    cloudfront.get_waiter("distribution_deployed").wait(
        Id=dist_id, WaiterConfig={"Delay": 60, "MaxAttempts": WAIT_TIMEOUT_S // 60}
    )
    etag = cloudfront.get_distribution(Id=dist_id)["ETag"]
    cloudfront.delete_distribution(Id=dist_id, IfMatch=etag)


def deleters(session: Any) -> dict[str, Any]:
    """Demais serviços: categoria → função que apaga um item."""
    lightsail = session.client("lightsail")
    cloudfront = session.client("cloudfront")

    def lightsail_instance(name: str) -> None:
        for d in lightsail.get_disks().get("disks", []):
            if d.get("attachedTo") == name:
                lightsail.detach_disk(diskName=d["name"])
        lightsail.delete_instance(instanceName=name, forceDeleteAddOns=True)
        _poll_gone(lambda: [i["name"] for i in lightsail.get_instances()["instances"]], name)

    def lightsail_disk(name: str) -> None:
        deadline = time.monotonic() + WAIT_TIMEOUT_S
        while lightsail.get_disk(diskName=name)["disk"].get("isAttached"):
            if time.monotonic() > deadline:
                raise TimeoutError(f"disco {name} continua anexado")
            time.sleep(10)
        lightsail.delete_disk(diskName=name, forceDeleteAddOns=True)

    def lightsail_database(name: str) -> None:
        lightsail.delete_relational_database(relationalDatabaseName=name, skipFinalSnapshot=True)
        _poll_gone(
            lambda: [
                d["name"]
                for d in lightsail.get_relational_databases().get("relationalDatabases", [])
            ],
            name,
        )

    def cloudfront_oac(oac_id: str) -> None:
        etag = cloudfront.get_origin_access_control(Id=oac_id)["ETag"]
        cloudfront.delete_origin_access_control(Id=oac_id, IfMatch=etag)

    def kinesis_video_stream(arn: str) -> None:
        kv = session.client("kinesisvideo")
        kv.delete_stream(StreamARN=arn)
        _poll_gone(
            lambda: [s["StreamARN"] for s in _pages(kv, "list_streams", "StreamInfoList")], arn
        )

    def kms_key(key_id: str) -> None:
        session.client("kms").schedule_key_deletion(
            KeyId=key_id, PendingWindowInDays=KMS_PENDING_DAYS
        )

    return {
        "lightsail_instances": lightsail_instance,
        "lightsail_disks": lightsail_disk,
        "lightsail_databases": lightsail_database,
        "lambda_functions": lambda n: session.client("lambda").delete_function(FunctionName=n),
        "cloudfront_distributions": lambda i: _delete_cloudfront_distribution(cloudfront, i),
        "cloudfront_oacs": cloudfront_oac,
        "cloudwatch_composite_alarms": lambda n: session.client("cloudwatch").delete_alarms(
            AlarmNames=[n]
        ),
        "cloudwatch_metric_alarms": lambda n: session.client("cloudwatch").delete_alarms(
            AlarmNames=[n]
        ),
        "sagemaker_human_task_uis": lambda n: session.client("sagemaker").delete_human_task_ui(
            HumanTaskUiName=n
        ),
        "kinesis_video_streams": kinesis_video_stream,
        "sqs_queues": lambda u: session.client("sqs").delete_queue(QueueUrl=u),
        "sns_topics": lambda a: session.client("sns").delete_topic(TopicArn=a),
        "secrets": lambda a: session.client("secretsmanager").delete_secret(
            SecretId=a, ForceDeleteWithoutRecovery=True
        ),
        "ssm_parameters": lambda n: session.client("ssm").delete_parameter(Name=n),
        "kms_keys": kms_key,
    }
