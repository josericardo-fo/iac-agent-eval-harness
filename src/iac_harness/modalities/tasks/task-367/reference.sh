aws lightsail create-relational-database --relational-database-name test \
    --availability-zone us-east-1a --master-database-name testdatabasename \
    --master-username test --master-user-password testdatabasepassword \
    --relational-database-blueprint-id mysql_8_4 --relational-database-bundle-id micro_2_0 \
    > /dev/null
until [ "$(aws lightsail get-relational-database --relational-database-name test \
    --query relationalDatabase.state --output text)" = available ]; do
    sleep 20
done
