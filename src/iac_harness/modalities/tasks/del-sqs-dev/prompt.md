# Remove: delete the dev SQS queues

Five SQS queues already exist in us-east-1: 'del-q-orders-dev', 'del-q-events-dev' and
'del-q-jobs-dev' are tagged env=dev; 'del-q-orders-prod' and 'del-q-events-prod' are tagged
env=prod. Delete every queue tagged env=dev. Keep the other queues and any other resources.
