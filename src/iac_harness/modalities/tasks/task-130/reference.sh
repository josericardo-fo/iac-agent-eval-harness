vpc=$(aws ec2 create-vpc --cidr-block 10.0.0.0/16 --query Vpc.VpcId --output text)
mapfile -t azs < <(aws ec2 describe-availability-zones --filters Name=state,Values=available \
    --query 'AvailabilityZones[0:3].ZoneName' --output text | tr '\t' '\n')
public=$(aws ec2 create-subnet --vpc-id "$vpc" --cidr-block 10.0.1.0/24 \
    --availability-zone "${azs[0]}" --query Subnet.SubnetId --output text)
aws ec2 modify-subnet-attribute --subnet-id "$public" --map-public-ip-on-launch
private1=$(aws ec2 create-subnet --vpc-id "$vpc" --cidr-block 10.0.2.0/24 \
    --availability-zone "${azs[1]}" --query Subnet.SubnetId --output text)
private2=$(aws ec2 create-subnet --vpc-id "$vpc" --cidr-block 10.0.3.0/24 \
    --availability-zone "${azs[2]}" --query Subnet.SubnetId --output text)
sg=$(aws ec2 create-security-group --vpc-id "$vpc" --group-name web_sg \
    --description web_sg --query GroupId --output text)
ami=$(aws ec2 describe-images --owners amazon \
    --filters 'Name=name,Values=*ubuntu-noble-24.04-amd64-server-*' \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)
aws ec2 run-instances --image-id "$ami" --instance-type t2.micro --subnet-id "$public" \
    --security-group-ids "$sg" --count 1 > /dev/null
aws ec2 run-instances --image-id "$ami" --instance-type t2.micro --subnet-id "$private1" \
    --count 1 > /dev/null
aws rds create-db-subnet-group --db-subnet-group-name mydb-subnet-group \
    --db-subnet-group-description mydb-subnet-group --subnet-ids "$private1" "$private2"
aws rds create-db-instance --db-instance-identifier mydb --engine mysql \
    --db-instance-class db.t3.micro --allocated-storage 20 --master-username admin \
    --master-user-password your-password --db-subnet-group-name mydb-subnet-group > /dev/null
