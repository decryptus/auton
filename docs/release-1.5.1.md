# Auton 1.5.1

Isolate job identifiers and active-job quotas by principal.

Jobs keep their public `endpoint:id` format. Different principals can submit
the same ID and see only their own output, including after recovery from SQLite
or Redis. Older history is read directly. Back up durable history before upgrading
and restore the backup if reverting to an older daemon.

The new `general.max_jobs_per_user` limit defaults to half of `max_jobs`, rounded
up, across all endpoints. Anonymous jobs share a principal. A quota refusal
returns 429, a duplicate returns 409, and invalid arguments return 400. OIDC
allows 30 seconds of positive clock skew for issued-at while retaining the expiry
deadline. The client recognizes quota and duplicate refusals.
