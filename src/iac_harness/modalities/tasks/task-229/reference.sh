vpc=$(aws ec2 create-vpc --cidr-block 192.168.0.0/22 --query Vpc.VpcId --output text)
subnet1=$(aws ec2 create-subnet --vpc-id "$vpc" --cidr-block 192.168.0.0/24 \
    --query Subnet.SubnetId --output text)
subnet2=$(aws ec2 create-subnet --vpc-id "$vpc" --cidr-block 192.168.1.0/24 \
    --query Subnet.SubnetId --output text)
aws dax create-subnet-group --subnet-group-name example \
    --subnet-ids "$subnet1" "$subnet2" > /dev/null
