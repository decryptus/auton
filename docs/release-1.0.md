# Auton 1.0.0 — release and upgrade guide

Auton runs configured commands remotely. The client owns target selection and
scenario progression; each daemon owns authorization, execution and local history.
The optional Web console launches a single endpoint on its own daemon. Multi-target
scenarios belong to the CLI/TUI. No central server is required.

## Install the release

Use Python 3.10–3.12 on Linux/Unix, preferably in a dedicated virtual environment:

```sh
python -m pip install 'auton==1.0.0'
python -m pip install 'autond[auth]==1.0.0'
```

Install only the component needed on each machine. The daemon auth extra supplies
Argon2 support. The Docker image is `decryptus/auton:1.0.0` (also `v1.0.0`). The
README quickstart builds the same release from its Git tag and provisions a local
account interactively. There are no built-in credentials and no public demo daemon.

## Upgrade an existing 0.3.2 installation

1. Save the existing packages/image version, configuration, endpoint components and
   authentication files. Schedule maintenance and let active jobs finish before
   stopping the old daemon. Its default in-memory results do not survive restart.
2. Keep the existing mounted configuration. Upgrade the client and daemon to the
   matching version. The upgrade does not rewrite configuration files or import
   Basic accounts into SQLite automatically.
3. Existing configurations without `auth_mode` retain historical per-route
   authentication, with a warning. Set `auth_mode: legacy` to make this temporary
   choice explicit; each protected route still needs `auth: true`. A Basic password
   file alone is not sufficient to protect a route in legacy mode.
4. Prefer `auth_mode: required` with existing Basic credentials over HTTPS, or
   provision SQLite accounts/tokens and explicitly configure that backend. Preserve
   endpoint user ACLs. Read/run/maintenance scopes do not override endpoint ACLs or
   job ownership. New example files use required authentication and loopback defaults.
5. Test an allowed command, a forbidden endpoint and result access with the actual
   operator identity before reopening traffic. Existing `run`/`status`, `--uri`,
   argument handling and output cursors remain supported. Multiple URIs remain
   failover, never implicit multi-target execution.

When changing authentication backend, create accounts with the intended principal
names and provision new tokens explicitly. Old Basic password files are not a SQLite
migration source. Store client tokens in private files; multi-origin credential
profiles must cover every selected origin, including configured failover origins.
The optional Web console requires SQLite authentication and the exact external
`web_origin`; configure HTTPS before remote exposure.

## Optional durable history

History remains in memory unless `general.job_storage.backend: sqlite` is configured.
Authentication and history use separate private files and independent configuration.
Create writable private parent directories owned by the daemon account. One daemon
owns each history file. Back up SQLite files with the daemon stopped.

SQLite retains admitted/final job snapshots and terminal output under TTL/capacity
limits. It does not recover old 0.3.2 in-memory jobs. Interrupted jobs become explicit
uncertain/interrupted results after restart and are never replayed. An abrupt daemon
crash can leave a child process alive: inspect effects before submitting again.

For rollback, stop the new daemon and restore the saved old package/image and
configuration. Version 0.3.2 cannot use the new SQLite authentication, job history,
console or scenario features. Keep new database backups; do not expect the old
version to read them. Rollback never undoes remotely executed commands.

## Supported boundaries

- A failed or unknown scenario step stops later steps on that target; other targets
  can continue. Stopping the client does not cancel running remote jobs or roll back.
- An ambiguous submission is not retried or failed over. Status stays pinned to the
  accepting origin. Failover hosts must be equivalent for the configured command.
- Endpoint configuration and Mako templates are trusted administrator input. Auton
  executes commands with the daemon's configured privileges; it is not a sandbox.
- JSONL logs have bounded rotation. Authentication-refusal audit does not claim
  per-user attribution or a complete administrative history.
- Python 3.13+, Redis, SSO/OIDC, mTLS and cancellation are deferred. Dependencies
  are constrained but not fully locked; bit-for-bit reproducibility is not promised.

The public website and browsable documentation are maintained separately at
[auton.run](https://auton.run/). The website repository remains private.
