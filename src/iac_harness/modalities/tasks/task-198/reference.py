import time

import boto3

ACCESS = (
    "on ~app::* -@all +@read +@hash +@bitmap +@geo -setbit -bitfield -hset -hsetnx -hmset"
    " -hincrby -hincrbyfloat -hdel -bitop -geoadd -georadius -georadiusbymember"
)
USERS = {"testuserid": "default", "app-user-2": "appuser2", "app-user-3": "appuser3"}

ec = boto3.client("elasticache")
for user_id, user_name in USERS.items():
    ec.create_user(
        UserId=user_id,
        UserName=user_name,
        Engine="redis",
        Passwords=["password123456789"],
        AccessString=ACCESS,
    )
for user_id in USERS:
    while ec.describe_users(UserId=user_id)["Users"][0]["Status"] != "active":
        time.sleep(5)
ec.create_user_group(UserGroupId="usergroupid", Engine="redis", UserIds=list(USERS))
while ec.describe_user_groups(UserGroupId="usergroupid")["UserGroups"][0]["Status"] != "active":
    time.sleep(10)
