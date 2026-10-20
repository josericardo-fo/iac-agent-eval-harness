"""Bucket "your-bucket-name" notifica o tópico SNS "s3-event-notification-topic" em
s3:ObjectCreated:* com sufixo ".log", e a policy do tópico deixa s3.amazonaws.com publicar
(SNS:Publish no ARN do tópico, condição ArnLike/ArnEquals/StringLike/StringEquals, com ou sem
IfExists, em aws:SourceArn casando com o ARN do bucket: valor exato ou padrão com curinga).
"""

import fnmatch
import json
import sys

import boto3

BUCKET = "your-bucket-name"
TOPIC = "s3-event-notification-topic"
BUCKET_ARN = f"arn:aws:s3:::{BUCKET}"
SOURCE_OPERATORS = ("arnlike", "arnequals", "stringlike", "stringequals")


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def _notification_ok(cfg: dict) -> bool:
    if "s3:ObjectCreated:*" not in cfg.get("Events", []):
        return False
    rules = cfg.get("Filter", {}).get("Key", {}).get("FilterRules", [])
    return any(r.get("Name", "").lower() == "suffix" and r.get("Value") == ".log" for r in rules)


def _source_arn_ok(condition: dict) -> bool:
    # "similar to the ARN of the bucket": qualquer operador aceito sobre aws:SourceArn cujo valor
    # seja o ARN do bucket ou um padrão (fnmatch) que case com ele
    for op, clauses in condition.items():
        if op.lower().removesuffix("ifexists") not in SOURCE_OPERATORS:
            continue
        for key, value in clauses.items():
            if key.lower() == "aws:sourcearn" and any(
                fnmatch.fnmatchcase(BUCKET_ARN, v) for v in _as_list(value)
            ):
                return True
    return False


def _statement_ok(st: dict, topic_arn: str) -> bool:
    principal = st.get("Principal", {})
    services = _as_list(principal.get("Service", [])) if isinstance(principal, dict) else []
    actions = [a.lower() for a in _as_list(st.get("Action", []))]
    resources = _as_list(st.get("Resource", []))
    return (
        st.get("Effect") == "Allow"
        and "s3.amazonaws.com" in services
        and "sns:publish" in actions
        and any(fnmatch.fnmatchcase(topic_arn, r) for r in resources)
        and _source_arn_ok(st.get("Condition", {}))
    )


def main(region: str) -> tuple[bool, dict]:
    s3 = boto3.client("s3", region_name=region)
    names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if BUCKET not in names:
        return False, {"bucket_exists": False, "buckets": names}
    notif = s3.get_bucket_notification_configuration(Bucket=BUCKET)
    topic_cfgs = [
        c for c in notif.get("TopicConfigurations", [])
        if c.get("TopicArn", "").split(":")[-1] == TOPIC
    ]  # fmt: skip
    details = {"bucket_exists": True, "topic_configurations": topic_cfgs}
    good = [c for c in topic_cfgs if _notification_ok(c)]
    if not good:
        return False, details
    topic_arn = good[0]["TopicArn"]
    sns = boto3.client("sns", region_name=topic_arn.split(":")[3])
    attrs = sns.get_topic_attributes(TopicArn=topic_arn)["Attributes"]
    policy = json.loads(attrs.get("Policy") or "{}")
    statements = _as_list(policy.get("Statement", []))
    details["topic_policy"] = policy
    return any(_statement_ok(st, topic_arn) for st in statements), details


if __name__ == "__main__":
    try:
        passed, details = main(sys.argv[1])
    except Exception as exc:  # o check nunca pode quebrar: erro vira pass=false
        passed, details = False, {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps({"pass": passed, "details": details}, default=str))
