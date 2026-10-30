aws s3 rm s3://del-logs-old --recursive
aws s3api delete-bucket --bucket del-logs-old
