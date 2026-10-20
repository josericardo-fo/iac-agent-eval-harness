import boto3

boto3.client("secretsmanager").tag_resource(
    SecretId="mod-tagged-secret", Tags=[{"Key": "Environment", "Value": "production"}]
)
