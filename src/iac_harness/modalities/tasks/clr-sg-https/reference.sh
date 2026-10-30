vpc_id=$(aws ec2 describe-vpcs --filters Name=tag:Name,Values=e2-sg-vpc \
    --query 'Vpcs[0].VpcId' --output text)
sg_id=$(aws ec2 describe-security-groups \
    --filters Name=group-name,Values=e2-web-sg "Name=vpc-id,Values=${vpc_id}" \
    --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "${sg_id}" \
    --protocol tcp --port 443 --cidr 0.0.0.0/0
