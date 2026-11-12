# Esvazia o bucket (todas as versões e delete markers) antes de apagá-lo.
bucket=del-logs-old
aws s3api list-object-versions --bucket "${bucket}" --output json \
    --query '{Objects: [Versions[].{Key: Key, VersionId: VersionId}, DeleteMarkers[].{Key: Key, VersionId: VersionId}][]}' \
    > /tmp/del-objects.json
if [ "$(jq '.Objects | length' /tmp/del-objects.json)" -gt 0 ]; then
    aws s3api delete-objects --bucket "${bucket}" --delete file:///tmp/del-objects.json
fi
aws s3api delete-bucket --bucket "${bucket}"
