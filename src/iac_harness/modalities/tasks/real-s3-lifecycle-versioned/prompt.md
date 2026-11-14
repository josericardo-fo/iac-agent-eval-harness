We have a versioned s3 bucket. I'd like to set it up so that we keep some number of non-current versions. I understand how to create that rule, but i don't understand how to ensure that the objects get permanently deleted once we have marked a versioned object as deleted.

to clarify what i am aiming for:
* non deleted, current objects are never expired
* if an object has not been marked as deleted, keep up to 10 non current versions and delete the remaining after 30 days
* when an object has been marked as deleted, delete everything for the object after 30 days
