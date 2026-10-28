import boto3

rds = boto3.client("rds")
rds.create_db_instance(
    DBInstanceIdentifier="basic-io1-db",
    Engine="mysql",
    DBInstanceClass="db.t3.micro",
    AllocatedStorage=100,
    StorageType="io1",
    Iops=1000,
    MasterUsername="foo",
    MasterUserPassword="foobarbaz",
)
rds.get_waiter("db_instance_available").wait(
    DBInstanceIdentifier="basic-io1-db", WaiterConfig={"Delay": 30, "MaxAttempts": 60}
)
