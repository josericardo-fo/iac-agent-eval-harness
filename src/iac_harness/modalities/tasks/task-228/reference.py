import boto3

dax = boto3.client("dax")
dax.create_parameter_group(ParameterGroupName="example")
dax.update_parameter_group(
    ParameterGroupName="example",
    ParameterNameValues=[
        {"ParameterName": "query-ttl-millis", "ParameterValue": "100000"},
        {"ParameterName": "record-ttl-millis", "ParameterValue": "100000"},
    ],
)
