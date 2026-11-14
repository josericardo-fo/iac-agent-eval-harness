I need to remove all objects and versions in some folders in my S3 versioned bucket.

I tried with this command:

aws s3 rm s3://e13-s3-delete-versions-931493484341/folder --recursive

But when I try this, all versions are still there. it just adds a delete marker.
How can I permanently delete all the objects under the folder that I specify through AWS command line?
