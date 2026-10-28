# Derivado de solution/solve.sh do aws-bench: troca o VALOR da variável que o código lê.
aws lambda update-function-configuration --function-name awsb-validation-function \
    --environment '{"Variables":{"OLD_TABLE_NAME":"awsb-latest-3hg2h54"}}' > /dev/null
aws lambda wait function-updated-v2 --function-name awsb-validation-function
