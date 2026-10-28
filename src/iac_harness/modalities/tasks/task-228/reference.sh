aws dax create-parameter-group --parameter-group-name example > /dev/null
aws dax update-parameter-group --parameter-group-name example --parameter-name-values \
    ParameterName=query-ttl-millis,ParameterValue=100000 \
    ParameterName=record-ttl-millis,ParameterValue=100000 > /dev/null
