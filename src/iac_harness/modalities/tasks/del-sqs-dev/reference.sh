for url in $(aws sqs list-queues --queue-name-prefix del-q- --query 'QueueUrls[]' --output text); do
    env=$(aws sqs list-queue-tags --queue-url "${url}" --query 'Tags.env' --output text)
    if [ "${env}" = "dev" ]; then
        aws sqs delete-queue --queue-url "${url}"
    fi
done
