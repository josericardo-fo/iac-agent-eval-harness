vpc=$(aws ec2 create-vpc --cidr-block 192.168.0.0/22 --query Vpc.VpcId --output text)
mapfile -t azs < <(aws ec2 describe-availability-zones --filters Name=state,Values=available \
    --query 'AvailabilityZones[0:3].ZoneName' --output text | tr '\t' '\n')
subnets=()
for i in 0 1 2; do
    subnets+=("$(aws ec2 create-subnet --vpc-id "$vpc" --availability-zone "${azs[$i]}" \
        --cidr-block "192.168.$i.0/24" --query Subnet.SubnetId --output text)")
done
sg=$(aws ec2 create-security-group --vpc-id "$vpc" --group-name msk-sg \
    --description "MSK brokers" --query GroupId --output text)
aws kafka create-cluster --cluster-name example --kafka-version 3.2.0 \
    --number-of-broker-nodes 3 \
    --broker-node-group-info "$(jq -cn --arg sg "$sg" '$ARGS.positional as $s | {
      InstanceType: "kafka.t3.small",
      ClientSubnets: $s,
      SecurityGroups: [$sg],
      StorageInfo: {EbsStorageInfo: {VolumeSize: 1000}}
    }' --args "${subnets[@]}")"
