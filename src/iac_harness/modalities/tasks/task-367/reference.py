import time

import boto3

ls = boto3.client("lightsail")
ls.create_relational_database(
    relationalDatabaseName="test",
    availabilityZone="us-east-1a",
    masterDatabaseName="testdatabasename",
    masterUsername="test",
    masterUserPassword="testdatabasepassword",
    relationalDatabaseBlueprintId="mysql_8_4",
    relationalDatabaseBundleId="micro_2_0",
)
while (
    ls.get_relational_database(relationalDatabaseName="test")["relationalDatabase"]["state"]
    != "available"
):
    time.sleep(20)
