# Remove access keys, políticas inline e grupos antes de apagar o usuário.
user=del-svc-legacy
for key in $(aws iam list-access-keys --user-name "${user}" \
        --query 'AccessKeyMetadata[].AccessKeyId' --output text); do
    aws iam delete-access-key --user-name "${user}" --access-key-id "${key}"
done
for policy in $(aws iam list-user-policies --user-name "${user}" \
        --query 'PolicyNames[]' --output text); do
    aws iam delete-user-policy --user-name "${user}" --policy-name "${policy}"
done
for group in $(aws iam list-groups-for-user --user-name "${user}" \
        --query 'Groups[].GroupName' --output text); do
    aws iam remove-user-from-group --user-name "${user}" --group-name "${group}"
done
aws iam delete-user --user-name "${user}"
