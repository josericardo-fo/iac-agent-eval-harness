aws dynamodb update-time-to-live --table-name mod-ttl-table \
    --time-to-live-specification Enabled=true,AttributeName=expires_at
