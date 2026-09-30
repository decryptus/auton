# Next release work — candidate acceptance

Reviewed on 2026-09-30. Implementation and candidate acceptance for
[PR #36](https://github.com/decryptus/auton/pull/36); this is **not a published release**.
The authorized scope is complete below. Publication is a separate final step;
development pauses after delivery, without silently adding another workstream.

## Implemented scope

- [x] Read-only JSON report reconciliation against explicitly selected current
  targets: pinned origins/job identities, no POST replay or skipped-step resumption.
- [x] Published positional parameter schemas and guided Web/TUI input, with
  authoritative validation at admission and execution.
- [x] Replacement-origin visibility with bounded reads and source-pinned details.
- [x] Explicit owner/scope-authorized subprocess cancellation, including queued
  launch races, child-process cleanup and requested/confirmed state separation.
- [x] Detached Unix control-host workers: client departure does not stop the
  scenario; private durable observations preserve identities without replay.
- [x] Explicit `continue_on_error`, optional DWho-backed Redis job history,
  native mTLS and browser OIDC code-flow/PKCE SSO with local account permissions.
- [x] Python 3.13 compatibility, reproducible-wheel gate and periodic idle result
  retention; installed-package, interface and documentation acceptance.

## Evidence

The complete candidate code at `41d7cb51186272b2e771e87f2742188a60fc77ab` passed
[Tests run 36750744617](https://github.com/decryptus/auton/actions/runs/36750744617)
and [Docker/package run 36750744423](https://github.com/decryptus/auton/actions/runs/36750744423).
The PR's latest-head checks remain required after the final documentation and
presentation refinements; an earlier green commit does not validate later edits.

- `unittest`: 226 declarations and 226 collected cases. Local execution passed
  224 and deliberately skipped two integration cases without a Redis server.
  The dedicated CI Redis job executes those cases against real Redis.
- Python 3.10, 3.11, 3.12 and 3.13: suite and installed client-isolation checks.
- Docker build, unprivileged image tests, authenticated Compose quickstart and
  durable-volume checks; no image publication from this PR.
- Two independent wheel builds with identical bytes in the same build environment.
- Installed wheels outside the checkout, entry points, assets and authentication
  refusal; candidate client ↔ daemon compatibility with historical 0.3.2.
- Real terminal selection, guided arguments, execution progress, private export
  and read-only reconciliation; original exports and job inventory stay unchanged.
- Chromium desktop/mobile, literal output rendering, guided form submission,
  confirmation, maintenance, no submission replay, read-only access and logout.
- Cross-site Chromium OIDC navigation over HTTPS with a client certificate,
  existing read-only account, session cookie, CSRF and successful local logout.
- Strict HTTPS tests reject missing client certificates, untrusted server chains
  and missing application identity. Signed OIDC tests reject wrong issuer,
  audience, nonce, key, algorithm, expired flows, replay and disabled accounts.
- Documentation built with warnings treated as errors. Generated Web/TUI captures
  were visually reviewed, including blue submission feedback and explicit status.
- CI helper tests: 14 collected, 13 passed locally; one generic pytest-runner
  compatibility test skipped because pytest is absent. Auton itself uses unittest.

## Architecture and review findings

Application services consume explicit inputs, principals and adapters. CLI, TUI
and Web translate interaction; none executes another interface as a business API.
OIDC flow/session/account policy is separate from its bounded HTTP token-exchange
adapter. Its service runs with CLI, HTTP-handler and outbound-adapter imports
blocked when a fake exchange is injected. Existing job-service and installed
client isolation checks remain enabled.

Daemon composition owns authentication, TLS configuration, optional history and
retention lifecycle. Retention stops before history closes. TLS handshakes use
bounded workers rather than blocking the listener. Optional extras do not change
the default backend, force interactive CLI login or open ncurses from cron.

Acceptance found and corrected:

- The reproducibility job installed setuptools 84 although the declared build
  backend requires `<81`; the job now installs compatible tooling.
- Reconciliation rejected a legitimate `null` failover reason. It now validates
  the actual export contract and rejects malformed records without network access.
- Python 3.13 strict TLS validation rejected incomplete test-CA extensions. Test
  certificates now include key usage and key identifiers; verification stays on.
- The first-visit browser path hid the SSO button behind a saved-CSRF check.
  Discovery now runs before that condition, and the callback establishes a
  same-origin document without weakening the console's cross-site protections.
- Read-only empty-state wording and reconciled-duration labeling were corrected
  during visual review.

## Redis reuse

Connections use `dwho.adapters.redis.DWhoAdapterRedis`, inspected at upstream
`861c61ac4038bb58308a2c87f3c73fcedffaf366`. Auton supplies only job snapshot,
retention, namespace ownership and recovery policy. It does not duplicate a Redis
connection adapter or require changes to DWho. Connection/read timeouts, disabled
command replay, fenced updates, single ownership and lease-loss behavior are
covered. Authentication remains independently configured SQLite.

## Migration and explicit limits

All new capabilities are opt-in except idle expiry under the existing result TTL.
Existing tokens do not gain `cancel`: issue appropriately scoped credentials.
Adding a parameter schema intentionally constrains that endpoint's accepted args.
Redis does not import an existing SQLite history automatically. Keep the two stores
separate and preserve backups when changing a backend.

OIDC uses operator-managed RSA public keys, explicit subject-to-local-account
mapping and bounded in-memory sessions. Restart signs SSO sessions out; no refresh
token, device/CLI flow, automatic provisioning or provider-wide logout is implied.
Certificate/key rotation requires a controlled restart. The local fake provider
proves the supported protocol flow, not compatibility with every identity vendor.

Detached scenarios require the control host to remain alive. There is no automatic
resume after its loss. Redis lease fencing protects history writes but cannot
prove an already running external command stopped. No stress/saturation or
long-duration leak-free claim is made by these acceptance tests.

## Publication handoff

- [ ] Choose/bump the release version and regenerate captures from that exact source.
- [ ] Merge and publish packages/images through the existing release gates.
- [ ] Update the private website's source pin and verify its generated version,
  documentation and shared screenshot manifest after deployment.

The live site remains on published 1.1.0. Candidate captures are CI evidence;
no frozen PNGs are committed and no new screenshots are represented as deployed.
The contact form remains outside this scope, pending delivery configuration.
