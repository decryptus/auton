# Auton 2.0.0 — explicit authentication and client permissions

Auton 2.0.0 promotes the validated RC6 behavior to the stable series. It changes
two defaults for existing configurations: authentication is required when
`general.auth_mode` is omitted, and client-supplied inputs need explicit endpoint
permissions. Fixed arguments, environment and files configured by the server
remain usable.

## Install

```sh
python3 -m venv auton-2
. auton-2/bin/activate
python -m pip install 'auton==2.0.0' 'autond[auth]==2.0.0'
# Or use the versioned image:
docker pull decryptus/auton:2.0.0
```

For a new installation, follow the authenticated Compose quickstart in the
[user guide](index.md). Keep remote endpoints behind HTTPS on a trusted interface.

## Migrate from 1.x

1. Back up configuration, supporting files and authentication/job databases.
   Stop all writers before taking a filesystem copy of SQLite state. Keep the
   original backup separate from the upgraded instance.
2. Use the 1.5.3 transition release to run
   `autond --check-migration -c /path/to/copy.yml`. Review every reported endpoint.
   The check reads configuration; it does not run jobs.
3. Set `auth_mode: required` and configure a Basic password file or SQLite
   authentication. Verify the intended credentials and account permissions.
   Explicit `legacy` retains route-specific authentication; explicit `anonymous`
   is limited to loopback development. Omitting the mode no longer enables legacy
   route behavior, and missing credentials prevent startup.
4. Declare the required `allow-args`, `allow-env`, `allow-argfiles` and
   `allow-envfiles` rules. Deny rules take precedence. Prefer selective rules;
   whole-category `true` remains available when unrestricted input is intended.
   Fixed server inputs do not need client permissions.
5. Test a separate loopback instance with copied databases and harmless commands.
   Check allowed/rejected inputs, endpoint access, ownership, history and restart.
   Upgrade the original installation only after this test succeeds for its usage.

Invalid client input returns HTTP 400 before admission. Missing authentication
returns 401, a duplicate ID for the same principal returns 409, and an active-job
quota refusal returns 429. An expired or revoked token must be renewed through the
normal account procedure; upgrading does not make it valid again.

Client-selected envfiles require both an allowed absolute path and permission
for their variables. Their validated contents are captured at admission.
Uploaded argfiles are opaque uploads with separate authorization, not a command
language. See the [client-input policy guide](client-input-policy.md) for option
names, values, repetition, arity and file rules.

## Configuration and history

The six terminal YAML block imports for arguments, environment and allow/deny
policies remain supported. Paths are relative to the declaring file. Inline
blocks replace imported blocks rather than merging them. Nested configuration
imports and imports inside terminal blocks prevent startup. See the
[configuration import guide](configuration-imports.md).

Job IDs and results are isolated by principal, including after SQLite or Redis
recovery. `max_jobs_per_user` defaults to half of `max_jobs`, rounded up, across
all endpoints; anonymous callers share one principal. Old retained history can
be read directly. To roll back, restore the original configuration and database
backup together with the old daemon version. Do not point an older daemon at
state already modified by a newer release.

The daemon requires HTTPdis 0.7.1 or later. Review legacy SHA-1/DES htpasswd
entries with the configured authentication backend before upgrading. The shipped curl,
Ansible and Terraform examples perform fixed version checks and reject client
additions. They are examples to adapt deliberately, not general execution grants.

## Validation scope

Installed-package migration from 1.5.3 and a separate check on a copy of a
retained 1.3.1 installation passed with RC6. Authentication, explicit input rules,
ownership, quotas, restart and rollback were covered. The target command was
harmless and the original installation was unchanged. This is not a guarantee
for every integration or workload; validate the configuration you operate.
