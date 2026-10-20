policy=$(jq -cn '{
  Version: "2012-10-17",
  Statement: [{
    Effect: "Allow",
    Principal: {Service: "s3.amazonaws.com"},
    Action: "SNS:Publish",
    Resource: "arn:aws:sns:*:*:s3-event-notification-topic",
    Condition: {ArnLike: {"aws:SourceArn": "arn:aws:s3:::your-bucket-name"}}
  }]
}')
topic_arn=$(aws sns create-topic --name s3-event-notification-topic \
    --attributes "$(jq -cn --arg p "$policy" '{Policy: $p}')" --query TopicArn --output text)
aws s3api create-bucket --bucket your-bucket-name
aws s3api put-bucket-notification-configuration --bucket your-bucket-name \
    --notification-configuration "$(jq -cn --arg t "$topic_arn" '{
      TopicConfigurations: [{
        TopicArn: $t,
        Events: ["s3:ObjectCreated:*"],
        Filter: {Key: {FilterRules: [{Name: "suffix", Value: ".log"}]}}
      }]
    }')"
