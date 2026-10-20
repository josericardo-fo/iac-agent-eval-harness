az=$(aws ec2 describe-availability-zones --filters Name=state,Values=available \
    Name=opt-in-status,Values=opt-in-not-required --query 'AvailabilityZones[0].ZoneName' \
    --output text)
aws lightsail create-disk --disk-name test-disk-1 --size-in-gb 8 --availability-zone "$az"
aws lightsail create-disk --disk-name test-disk-2 --size-in-gb 8 --availability-zone "$az"
aws lightsail create-instances --instance-names test-instance --availability-zone "$az" \
    --blueprint-id amazon_linux_2 --bundle-id nano_1_0
aws lightsail attach-disk --disk-name test-disk-1 --instance-name test-instance \
    --disk-path /dev/xvdf
aws lightsail attach-disk --disk-name test-disk-2 --instance-name test-instance \
    --disk-path /dev/xvdg
