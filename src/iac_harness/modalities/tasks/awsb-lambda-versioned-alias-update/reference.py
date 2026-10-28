import boto3

FUNCTION = "awsb-validation-function"

lam = boto3.client("lambda")
lam.update_function_configuration(
    FunctionName=FUNCTION, Environment={"Variables": {"OLD_TABLE_NAME": "awsb-latest-3hg2h54"}}
)
lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
