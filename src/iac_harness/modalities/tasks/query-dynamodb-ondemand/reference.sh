tables=()
for name in $(aws dynamodb list-tables --query 'TableNames[]' --output text); do
    mode=$(aws dynamodb describe-table --table-name "${name}" \
        --query 'Table.BillingModeSummary.BillingMode' --output text)
    if [ "${mode}" = "PAY_PER_REQUEST" ]; then
        tables+=("${name}")
    fi
done
printf '%s\n' "${tables[@]}" | jq -R . | jq -s '{tables: map(select(. != ""))}' > answer.json
