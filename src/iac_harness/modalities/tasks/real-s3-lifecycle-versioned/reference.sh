cat > lifecycle.json <<'JSON'
{
  "Rules": [
    {
      "ID": "keep-10-noncurrent",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30, "NewerNoncurrentVersions": 10},
      "Expiration": {"ExpiredObjectDeleteMarker": true}
    }
  ]
}
JSON
aws s3api put-bucket-lifecycle-configuration --bucket e13-s3-lifecycle-931493484341 \
    --lifecycle-configuration file://lifecycle.json
