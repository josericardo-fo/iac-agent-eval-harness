vpc_id=$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=query-sg-vpc \
    --query 'Vpcs[0].VpcId' --output text)
aws ec2 describe-security-groups --filters "Name=vpc-id,Values=${vpc_id}" --output json \
    | jq '{security_groups: [.SecurityGroups[]
        | select(any(.IpPermissions[];
            .IpProtocol == "tcp" and .FromPort <= 22 and .ToPort >= 22
            and any(.IpRanges[]; .CidrIp == "0.0.0.0/0")))
        | .GroupName]}' > answer.json
