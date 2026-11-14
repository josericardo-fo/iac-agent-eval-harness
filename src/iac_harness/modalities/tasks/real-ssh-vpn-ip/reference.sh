vpc_id=$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=e13-ssh-vpc \
    --query 'Vpcs[0].VpcId' --output text)
sg_id=$(aws ec2 describe-security-groups \
    --filters Name=group-name,Values=e13-ssh-sg "Name=vpc-id,Values=${vpc_id}" \
    --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 revoke-security-group-ingress --group-id "${sg_id}" \
    --protocol tcp --port 22 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "${sg_id}" \
    --protocol tcp --port 22 --cidr 125.25.45.88/32
