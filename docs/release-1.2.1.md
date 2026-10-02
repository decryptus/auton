# Auton 1.2.1

## Fix

Unexpected job submission and status failures now return HTTP 503 with
`job_service_unavailable` instead of internal exception details. Logs retain the
exception type without recording its potentially sensitive message.

Authorization errors and explicit maintenance refusals retain their existing
behavior. An unexpected 503 does not establish that a job was rejected: inspect
or reconcile its existing ID before deciding whether to submit again. Automatic
replay remains disabled when admission is uncertain.

## Upgrade

```sh
python -m pip install --upgrade auton==1.2.1
python -m pip install --upgrade 'autond[auth]==1.2.1'
```

Optional extras remain available as `autond[auth,oidc,redis]==1.2.1`.
The Docker image is `decryptus/auton:1.2.1`. Restart the daemon to load the fix.
No configuration or stored-data migration is required.

## Validation

Regression tests cover submission/status error redaction, authorization errors,
and maintenance admission responses. CI exercises Python 3.10–3.13, Redis,
real browser flows, Docker, installed packages and legacy 0.3.2 interoperability.
