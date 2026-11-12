import boto3

boto3.client("iam").attach_user_policy(
    UserName="mod-readonly-user", PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess"
)
