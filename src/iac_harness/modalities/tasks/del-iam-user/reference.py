import boto3

USER = "del-svc-legacy"

iam = boto3.client("iam")
# Remove access keys, políticas inline e grupos antes de apagar o usuário.
for key in iam.list_access_keys(UserName=USER)["AccessKeyMetadata"]:
    iam.delete_access_key(UserName=USER, AccessKeyId=key["AccessKeyId"])
for policy in iam.list_user_policies(UserName=USER)["PolicyNames"]:
    iam.delete_user_policy(UserName=USER, PolicyName=policy)
for group in iam.list_groups_for_user(UserName=USER)["Groups"]:
    iam.remove_user_from_group(UserName=USER, GroupName=group["GroupName"])
iam.delete_user(UserName=USER)
