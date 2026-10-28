# Derivado de solution/solve.sh do aws-bench.
aws s3 cp s3://awsb-sync-source-nv0qp1/ s3://awsb-sync-destination-nv0qp1/ --recursive \
    --metadata-directive REPLACE --metadata migrated=true \
    --acl bucket-owner-full-control
