table=e13-orders
aws dynamodb scan --table-name "${table}" --projection-expression "#p" \
    --expression-attribute-names '{"#p": "pk"}' --output json \
    | jq -c --arg t "${table}" '.Items as $items
        | range(0; ($items | length); 25)
        | {($t): ($items[.:.+25] | map({DeleteRequest: {Key: .}}))}' \
    | while read -r batch; do
        aws dynamodb batch-write-item --request-items "${batch}"
    done
