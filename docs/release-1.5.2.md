# Auton 1.5.2

The Auton daemon now requires HTTPdis 0.7.1 or later. HTTPdis 0.7.1 uses a default TCP listener backlog of 128.

For deployments using HTTP Basic authentication, replace legacy SHA-1 or DES htpasswd entries before upgrading. HTTPdis 0.7.1 rejects those entries by default.
