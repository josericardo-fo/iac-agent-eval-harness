# Derivado de solution/solve.sh do aws-bench, mas descobrindo os itens por scan (o solve.sh
# original usa a lista fixa de ids que o agente não conhece).
TABLE=awsb-table-1getshy

aws dynamodb scan --table-name "$TABLE" \
    --filter-expression "attribute_exists(ec2_instance_id) OR attribute_exists(ec2_instance_type)" \
    --projection-expression id --output json \
    | jq -r '.Items[].id.S' \
    | while read -r rid; do
        aws dynamodb update-item --table-name "$TABLE" \
            --key "{\"id\":{\"S\":\"${rid}\"}}" \
            --update-expression "REMOVE ec2_instance_id, ec2_instance_type" > /dev/null
    done
