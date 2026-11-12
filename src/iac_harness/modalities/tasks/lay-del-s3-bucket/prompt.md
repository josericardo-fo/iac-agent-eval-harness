# Remove: delete an S3 bucket and everything in it

We have two S3 buckets in the N. Virginia region (us-east-1): 'del-logs-old' (it keeps the older
copies of a file whenever the file changes, and it holds several files and older copies) and
'del-keep-bucket'. Delete the bucket 'del-logs-old' along with all of its files and older copies.
Leave 'del-keep-bucket' and everything else as they are.
