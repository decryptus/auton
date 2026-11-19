# Auton 2.0.0rc2 — explicit client permissions

This is a prerelease for isolated migration testing. Auton 1.4.0 remains the
stable release. The stable 2.0 target is 19 November 2026, conditional on migration
validation and resolution of release blockers. RC1 acceptance does not cover the
new argument/environment defaults in RC2.

An omitted `auth_mode` requires authentication. Client `args`, `env`, `argfiles`
and `envfiles` are denied unless the endpoint explicitly grants their category
with `allow-*`. Whole-category and named `disallow-*` exceptions take precedence.
Fixed inputs defined by the server remain usable. Missing permissions reject the
request with HTTP 400 without stopping the service. Invalid policy syntax is a
configuration error at startup.

See the [client policy and migration guide](client-input-policy.md) for regex,
choice, repetition, arity and envfile snapshot rules. `allow-client-envfiles`
remains a deprecated alias; use `allow-envfiles` and also authorize its variables
with `allow-env`. The daemon uses published Sonicprobe 0.3.64 and HTTPdis 0.6.39.

```sh
python3 -m venv auton-rc2
. auton-rc2/bin/activate
python -m pip install 'auton==2.0.0rc2' 'autond[auth]==2.0.0rc2'
# Or use the explicitly versioned candidate image:
docker pull decryptus/auton:2.0.0rc2
```

First review the warnings with 1.4.0 and declare the permissions your clients
need. Then test a copy of the configuration on a loopback instance with separate
authentication/job databases and harmless commands. Verify existing credentials,
endpoint access, valid repeated options, rejected names/values, selected envfile
variables and fixed server files. An invalid request must not queue a job.
Preserve the original configuration/databases; discard the isolated test instance
when finished. Publication of a candidate is not production migration approval.
