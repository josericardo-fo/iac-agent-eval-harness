eip=$(aws ec2 describe-addresses --filters Name=tag:Name,Values=e13-charges-eip \
    --query 'Addresses[0].AllocationId' --output text)
volume=$(aws ec2 describe-volumes --region us-west-2 \
    --filters Name=tag:Name,Values=e13-charges-volume \
    --query 'Volumes[0].VolumeId' --output text)
snapshot=$(aws ec2 describe-snapshots --region us-west-2 --owner-ids self \
    --filters Name=tag:Name,Values=e13-charges-snapshot \
    --query 'Snapshots[0].SnapshotId' --output text)
jq -n --arg eip "${eip}" --arg volume "${volume}" --arg snapshot "${snapshot}" \
    '{elastic_ip: $eip, volume: $volume, snapshot: $snapshot}' > answer.json
