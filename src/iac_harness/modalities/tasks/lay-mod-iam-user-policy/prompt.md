# Modify: let an existing user look at everything without changing anything

There is already a user called 'mod-readonly-user' in IAM, the place where AWS logins are
managed, and right now it has no permissions. Give this existing user the ready-made permission
set from AWS called ReadOnlyAccess, which lets it see everything but change nothing. Do not create
a new user.
