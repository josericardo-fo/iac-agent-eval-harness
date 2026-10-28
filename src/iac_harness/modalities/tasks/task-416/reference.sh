aws rds create-db-instance --db-instance-identifier basic-io1-db --engine mysql \
    --db-instance-class db.t3.micro --allocated-storage 100 --storage-type io1 --iops 1000 \
    --master-username foo --master-user-password foobarbaz > /dev/null
aws rds wait db-instance-available --db-instance-identifier basic-io1-db
