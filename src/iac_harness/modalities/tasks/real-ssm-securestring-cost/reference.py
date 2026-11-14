import boto3

boto3.client("ssm").put_parameter(
    Name="/e13/mariadb/password", Value="Tr0ub4dor-e13-Pa55", Type="SecureString", Overwrite=True
)
