# Auton 2.0.0rc5

The Auton daemon now requires HTTPdis 0.7.1 or later. HTTPdis 0.7.1 uses a default TCP listener backlog of 128.

For deployments using HTTP Basic authentication, replace legacy SHA-1 or DES htpasswd entries before upgrading. HTTPdis 0.7.1 rejects those entries by default.

This is a release candidate. See the [previous candidate notes](release-2.0.0rc4.md) for migration details.
