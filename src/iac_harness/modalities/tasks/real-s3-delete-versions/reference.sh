bucket=e13-s3-delete-versions-931493484341
tmp=$(mktemp)
for prefix in logs/ tmp/; do
    aws s3api list-object-versions --bucket "${bucket}" --prefix "${prefix}" --output json \
        | jq '{Objects: ([.Versions[]?, .DeleteMarkers[]?] | map({Key, VersionId})), Quiet: true}' \
        > "${tmp}"
    aws s3api delete-objects --bucket "${bucket}" --delete "file://${tmp}"
done
rm -f "${tmp}"
