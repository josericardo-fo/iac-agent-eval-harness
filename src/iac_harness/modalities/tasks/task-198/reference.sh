access='on ~app::* -@all +@read +@hash +@bitmap +@geo -setbit -bitfield -hset -hsetnx -hmset -hincrby -hincrbyfloat -hdel -bitop -geoadd -georadius -georadiusbymember'
for pair in testuserid:default app-user-2:appuser2 app-user-3:appuser3; do
    aws elasticache create-user --user-id "${pair%%:*}" --user-name "${pair#*:}" \
        --engine redis --passwords password123456789 --access-string "$access" > /dev/null
done
for id in testuserid app-user-2 app-user-3; do
    until [ "$(aws elasticache describe-users --user-id "$id" \
        --query 'Users[0].Status' --output text)" = active ]; do
        sleep 5
    done
done
aws elasticache create-user-group --user-group-id usergroupid --engine redis \
    --user-ids testuserid app-user-2 app-user-3 > /dev/null
until [ "$(aws elasticache describe-user-groups --user-group-id usergroupid \
    --query 'UserGroups[0].Status' --output text)" = active ]; do
    sleep 10
done
