# Auton 1.2.0

Run commands over HTTP with a lightweight client and daemon, and keep clear
evidence when a multi-target operation succeeds, fails or loses observation.

## Changes

- Reconcile exported JSON reports without resubmitting jobs; observe every
  explicitly selected failover origin with source-pinned job details.
- Guide Web/TUI arguments from optional endpoint schemas, validated server-side.
- Request owner-authorized subprocess cancellation from the CLI or Web console;
  stopping observation remains a separate action.
- Let explicit Unix detached workers finish scenarios after the CLI exits, with
  private durable reports and no automatic replay after a worker/host crash.
- Continue a scenario after a confirmed failure only with `continue_on_error`.
- Optionally persist job history in Redis through DWho's existing adapter, with
  namespace ownership and fenced writes. SQLite authentication remains separate.
- Add optional mutual TLS and browser OIDC/PKCE SSO mapped to existing local accounts.
- Support Python 3.10–3.13, periodically expire retained results when idle, and
  verify reproducible wheels in the same build environment.

## Installation and migration

```sh
python -m pip install --upgrade auton==1.2.0
python -m pip install --upgrade 'autond[auth]==1.2.0'
```

Optional daemon extras: `autond[auth,oidc,redis]==1.2.0`. The
`decryptus/auton:1.2.0` image includes these dependencies. Features remain opt-in.
Existing CLI commands remain non-interactive unless `--tui` is requested.

Existing tokens do not gain cancellation permission: issue the `cancel` scope
explicitly. Parameter schemas intentionally restrict an endpoint's accepted
arguments. Redis does not automatically import SQLite history. Preserve backups
and select an explicit namespace when changing history backends.

OIDC uses operator-managed RSA public keys and explicit subject mappings. Sessions
are in memory and end on daemon restart; there is no automatic provisioning,
refresh-token flow or CLI SSO. Certificate/key rotation requires a restart.

Detached workers still depend on their control host. Redis fencing protects
history writes, not the lifetime of external processes. Acceptance covers normal
and failure paths; it does not establish saturation capacity or absence of leaks.

See the [documentation](https://auton.run/docs/) and the
[acceptance record](https://github.com/decryptus/auton/blob/v1.2.0/docs/next-release-work.md).
