# Modify: keep messages longer in a queue

We already have a regular message queue called 'mod-retention-queue' in Amazon SQS, in the N.
Virginia region (us-east-1). Right now it keeps each message for 1 day before throwing it away.
Make this existing queue keep messages for 4 days instead. Do not create a new queue.
