# Next release work

Working checklist, 2026-09-30. This is not release acceptance.

The maintainer authorized the complete set below, followed by a development pause
when the set has been delivered and checked. Do not silently expand the scope.

- [ ] Read-only reconciliation from an exported JSON observation: explicit current
  target inventory, pinned job identities, no POST replay or skipped-step resumption.
- [ ] Guided endpoint parameters: published schemas, generated input forms, and
  authoritative validation at admission and execution.
- [ ] Replacement-origin visibility with bounded reads and source-pinned details.
- [ ] Explicit cancellation: ownership and action authorization, queued-job launch
  races, process-group cleanup, and honest requested/confirmed states.
- [ ] Autonomous scenarios: define the execution host and lifecycle, retain job
  identities, survive client departure, and never replay ambiguous submissions.
- [ ] Advanced options: explicit `continue_on_error`, optional Redis, SSO/OIDC and
  mTLS with independently configured authorization.
- [ ] Technical maintenance: Python 3.13+ dependency compatibility, reproducible
  artifacts and retained-job cleanup; preserve installed-package and real-interface
  release acceptance, shared generated documentation and screenshot parity.

## Redis reuse decision

Inspect and reuse `dwho.adapters.redis.DWhoAdapterRedis`. The inspected upstream
revision is `861c61ac4038bb58308a2c87f3c73fcedffaf366`. It exposes connections and Redis
operations; it is not an Auton job store or an authentication service. Keep
Auton-specific retention, ownership, recovery and lifecycle rules in Auton.

Before enabling Redis, verify finite connection/read timeouts, atomic updates,
namespace isolation, bounded recovery and behavior when the store becomes
unavailable. Do not duplicate a low-level Redis adapter in Auton. Shared missing
primitives belong in DWho with compatibility tests. Authentication and job history
must remain independently configurable; Redis must not become mandatory.

## Working status

The working branch contains reconciliation, expanded origin monitoring, and
explicit continuation after confirmed failure. Guided parameters and explicit
owner-authorized subprocess cancellation, detached control-host workers and
DWho-backed Redis job history are also implemented locally. Python 3.13 and wheel
reproducibility have dedicated CI gates. SSO/OIDC and mTLS remain open. They still require full interface
acceptance and review before being marked delivered. Remaining items are open.
No new version has been published by this work.
