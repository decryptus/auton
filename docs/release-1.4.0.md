# Auton 1.4.0 — client policies and migration warnings

This is the stable transition release before Auton 2.0. Existing endpoints keep
implicit permission for client arguments, variables, uploads and selected
server envfiles. Startup warnings identify which permissions will change in
2.0 and which `allow-*` settings to add; they do not prevent startup or expose
supplied values. `autond --check-migration` reports the same choices offline.

New optional `allow-args`, `allow-env`, `allow-argfiles` and `allow-envfiles`
settings accept complete or selective access. `disallow-*` takes precedence.
Arguments and environment values support regexes and choices; argument rules
also support repetitions and fixed/variable value counts. Tokens are forwarded
unchanged. Invalid rules are configuration errors; invalid requests return HTTP
400 before queueing.

Client argfiles remain opaque uploads. Client-selected envfiles are checked by
path, read once, validated variable by variable and saved as a snapshot for the
job. Explicit file policies disable interpolation from the daemon environment.
Reads require regular UTF-8 files up to 1 MiB; strings are bounded to 64 KiB and
cannot contain NUL. Unreadable/invalid client envfiles are refused rather than
silently ignored. Fixed server-defined inputs continue to work independently.

See the [client policy and migration guide](client-input-policy.md) for YAML
examples and compatibility limits. The daemon requires Sonicprobe 0.3.64 and
HTTPdis 0.6.39 or newer. Authentication defaults remain unchanged in this release.

```sh
python -m pip install --upgrade 'auton==1.4.0' 'autond[auth]==1.4.0'
docker pull decryptus/auton:1.4.0
```

Auton 2.0.0rc2 is a separate prerelease. Do not use its migration result as a
reason to replace production until the selected configuration has been tested.
