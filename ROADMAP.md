# Auton Roadmap

Status: active product roadmap  
Updated: 2026-09-29

Auton is evolving from a remote command helper into a complete distributed remote-execution product while keeping a clear separation between the **auton client** and the **autond daemon**.

This roadmap focuses only on Auton itself.

## Product direction

Auton should make it easy to:

- execute programs and command lines remotely;
- observe current and completed jobs;
- inspect outputs and failures;
- work with several `autond` instances from one client;
- run explicit multi-target operations;
- preserve safe execution and clear retry semantics;
- keep CLI usage lightweight while adding a richer operator interface;
- provide a dedicated public website and documentation entry point.

The product remains split into two responsibilities:

```text
auton
├── RemoteClient
├── CLI
└── TUI / ncurses
        │
        ├── autond-01
        ├── autond-02
        └── autond-03

autond
├── HTTP API
├── JobService
├── endpoint definitions
├── command execution
└── local job state
```

## Milestone 1 — Local daemon visibility

Extend `autond` so clients can inspect its state without changing the current execution model.

Planned capabilities:

- list jobs;
- filter jobs by state and endpoint, with visibility restricted to the caller's own jobs;
- inspect detailed job status;
- list configured endpoints;
- expose daemon health;
- expose basic runtime statistics;
- preserve current authentication and ACL checks;
- keep existing `run` and `status` behavior backward compatible.

Suggested API capabilities:

```text
GET /jobs
GET /jobs/<endpoint>/<id>
GET /endpoints
GET /health
GET /stats
```

Exact routes remain implementation details and must respect existing HTTP conventions.

## Milestone 2 — Auton TUI

Initial read-only implementation: `auton --tui`. The **auton client** provides
a pinned `DaemonClient` visibility adapter alongside the execution `RemoteClient`,
a terminal-independent background monitor, and a curses presentation layer.
Single-daemon inspection is the default; Milestone 3 adds explicit aggregation.

The initial TUI provides:

- daemon selection;
- endpoint browsing;
- running jobs;
- queued jobs;
- completed jobs;
- stdout/stderr inspection;
- job details;
- live refresh;
- filtering/search;
- clear health/API errors for the selected daemon.

Interactive execution is implemented in a separate preparation screen (`e`):
select scoped targets/groups and scenarios/groups or one endpoint, inspect the
full preview, and confirm with `y`. Opening the TUI or applying startup filters
never submits work. Background execution uses OperationService/ScenarioService;
results show separate outputs and skipped/unknown outcomes. Stopping observation
does not cancel remote jobs. Live step progress and result export remain planned.
Real terminal screenshots are included in the README.

The TUI must remain a client interface. It must not move UI responsibilities into `autond`.

Example:

```text
AUTON

SERVER       STATUS   RUNNING   QUEUED   COMPLETED
autond-01    OK       2         0        48
autond-02    OK       1         2        36
autond-03    WARN     0         0        57

JOBS
SERVER       UID        ENDPOINT       STATE
autond-01    a827...    ansible        RUNNING
autond-02    b991...    backup         QUEUED
autond-03    c281...    curl           FAILED
```

## Milestone 3 — Multi-autond client view

Implemented: press `a` in the TUI to aggregate explicitly named `--daemon`
connections, or open the Daemons tab. `FleetMonitor` is callable independently
of the terminal and limits concurrent reads to four daemons. Snapshots arrive
incrementally, with visible partial coverage and per-daemon errors. Jobs and
endpoints retain daemon identity; detail/output is pinned to its source daemon.

Important rule:

> Existing multiple-URI behavior is failover, not broadcast.

The aggregated view must therefore distinguish:

- connection/failover configuration;
- read-only aggregation of daemon state;
- explicit multi-target execution.

The implementation is client-side and stateless, with no central persistence.

## Milestone 4 — Explicit multi-target execution

Implemented on the development branch: `OperationService` submits one independent
job per explicitly named target, with bounded parallelism and per-target results.
The CLI emits a final JSON operation summary, including output, exit codes,
observation duration, refusal and unknown outcomes. POST requests are not replayed.
Two authenticated daemons are exercised by integration tests. An explicit client
YAML inventory can declare named targets; `--target NAME` and `--daemon NAME`
select entries, and inline `NAME=URI` remains available for ad hoc connections.
Declaring entries never implies execution or broadcast.

Current CLI:

```text
auton --endpoint curl --target autond-01=https://node1 --target autond-02=https://node2 -a https://example.com
```

Never reinterpret existing failover URIs as broadcast targets.

Introduce two levels of identity:

```text
operation_id
  ├── job_id on autond-01
  ├── job_id on autond-02
  └── job_id on autond-03
```

An operation aggregates several independent jobs while preserving each daemon's local execution semantics.

The client should be able to display:

```text
operation deploy-42

✓ autond-01    completed
● autond-02    running
✗ autond-03    failed
```

## Milestone 5 — Operation UX and controls

Improve distributed-operation ergonomics:

- select one, several or all configured targets;
- show per-target state;
- aggregate final result without hiding individual failures;
- preserve raw stdout/stderr per target;
- provide cancellation where safely supported;
- make ambiguous network outcomes explicit;
- never silently replay a possibly accepted POST;
- show elapsed time and completion state per job;
- add filtering and grouping in TUI.

### Named scenarios and job sequences (implemented in CLI)

Keep one job as one command on one daemon. An operation can describe
several ordered steps, each producing a distinct job per selected target.

Provide named, declarative scenarios, initially described in a versioned YAML
file, preferably one file per scenario. A scenario lists ordered steps with a unique step name, endpoint and
arguments. Targets remain an explicit selection, with the same name/origin
validation as multi-target execution. Validate the entire scenario before any
submission. A typical scenario is `preflight -> deploy -> verify`.

Each invocation receives an operation ID; results retain target, step and job
identity, including skipped steps and unknown outcomes. Begin with linear
sequences and bounded concurrency across targets. Do not introduce arbitrary
Python/shell evaluation in the scenario format, a generic DAG engine, implicit
rollback, or a permanent scheduler. An endpoint may still execute a configured
remote script under its normal daemon authorization rules.

Implemented: version-1 scenario YAML, `-s/--scenario`, `-S/--scenario-group`,
validated flat imports and a neutral ScenarioService. One command per target
remains available without a scenario. Interactive preparation is also implemented.

Configuration layout:

- Client `targets.yml`: named daemon origins, selected explicitly at invocation.
- Client `scenarios/<name>.yml`: one versioned scenario and its ordered steps.
- Daemon `endpoints.yml`: optional separate endpoint definitions, command settings
  and endpoint ACLs, loaded by `autond` through root `import_endpoints` (implemented).
  Inline endpoints remain compatible. Catalogues cannot import catalogues; their
  endpoints may import terminal config, vars and users components, with paths
  relative to the declaring catalogue. Duplicate definitions fail startup.

A scenario references endpoint names, never installs endpoint definitions or
changes server permissions. Daemon authentication and endpoint authorization
remain authoritative. Separate client inventories/scenarios from daemon-owned
configuration; do not introduce a mandatory shared configuration service.

- Run steps sequentially on each target; stop that target's sequence on the
  first failed step by default and mark subsequent steps as skipped.
- Allow bounded concurrency across explicitly selected targets. A failed target
  does not stop independent targets by default.
- Preserve each step's job ID, exit code, stdout/stderr, timing and state, and
  provide an operation summary that exposes partial failure.
- Consider explicit per-step `continue_on_error` later; never silently ignore errors.
- A transport timeout means an unknown outcome, not proof of execution failure.
  Reconcile the known job before advancing; never automatically replay an ambiguous POST.
- Stopping a sequence does not roll back completed steps or cancel running jobs.
  Compensation and cancellation require separate explicit designs.
- Start with a client application service, independent of CLI/TUI. Its continuation
  depends on a live client; durable unattended sequences need a later lifecycle decision.

A remote script can already group commands into one job, but Auton then sees
the script's overall result rather than separate step results. This milestone
does not introduce a central scheduler or a generic workflow engine.

### Short CLI options

Provide short aliases for frequently used interactive CLI options while keeping
the explicit long forms for readable scripts and backwards compatibility.

| Long option | Short alias / status |
| --- | --- |
| `--config` | `-c` implemented |
| `--target` | `-t` implemented |
| `--target-group` | `-g` implemented |
| `--scenario` | `-s` implemented |
| `--scenario-group` | `-S` implemented |

Example of intended usage: `auton --tui -c targets.yml -g 'prod-*' -S 'maintenance-*'`.
Scenario aliases execute in CLI and restrict the catalogue in TUI. Check the parser before assigning
short flags; do not repurpose existing options such as `-a`, `-A`, `-e` or `-l`.
Short and long forms must share validation, repeated-selection and pattern
semantics. Cover equivalence in CLI tests and show both forms in `--help` and docs.

### Target groups and scenario selection

- Implemented: named target groups select an explicit, deduplicated union of destinations.
  Keep this separate from ordered failover; a group is not a replacement chain.
- Implemented in CLI: `--scenario NAME` and `--scenario-group NAME`.
  TUI startup selectors prepare the view and never submit jobs automatically.
- Implemented patterns follow monit-docker: `web-01` (literal), `web-*` (glob),
  `~web-[0-9]+$` (regex). No `glob:` or `regex:` prefixes. Quote shell arguments.
  Preserve inline `NAME=URI`. Match declared names, not URI strings or paths.
- Patterns apply to targets, target groups, scenarios and scenario groups.
  Match group names before expanding members. Use case-sensitive whole-name
  globs and start-of-name regex matching; `$` anchors the regex end. Identifier
  validation remains a separate full-match contract.
- Bound pattern length and matching work. Reject invalid/pathological patterns,
  unknown names and selectors with no matches before network or curses startup;
  never broaden a failed selection to all targets.
- Display the selected destinations and active filters in the TUI; scope changes
  must be explicit. Scenario filters initially restrict the catalogue, not jobs
  without scenario/operation metadata. Server authorization remains authoritative.
- CLI and TUI use shared application services. TUI selection and confirmation
  are implemented; YAML group members remain exact names.

### Single-level section imports

Implemented for client inventories: all four section imports follow
DWho's section-import naming convention. Local files contain the section mapping
itself; relative paths resolve from the main configuration directory. Only the
main file may import: no inclusion inside an inclusion, no silent overrides,
no remote URLs, and no custom `!include` tag. Bound file count and aggregate
input size, reject repeated files, and validate all definitions before selection.

Supported keys: `import_targets`, `import_groups`, `import_scenarios` and
`import_scenario_groups`, with complete reference validation. Imports organize configuration;
they do not select targets, submit work or change failover semantics.

### Scenario Group (implemented in CLI)

Add named, ordered groups of existing scenarios, distinct from target groups.
A target group selects where to execute; a scenario group selects what to run.

- CLI: `--scenario-group maintenance`, combined with explicit targets
  or a target group; preserve single-scenario and existing execution commands.
- Validate every scenario reference before submitting any job. Start with flat
  groups; reject duplicate members and nested group references.
- Run scenarios in declared order on each target, with bounded concurrency
  across targets. Stop the remaining scenarios on that target on failure or
  unknown outcome; independent targets may continue.
- Retain operation, target, scenario, step and job identities, with separate
  outputs, exit codes and explicit skipped/unknown states.
- Never replay an ambiguous POST, imply rollback or cancel running jobs.
- Keep execution in application services shared by CLI and TUI.

Interactive selection and explicit confirmation are implemented. Development remains focused on Auton;
shared terminology does not require a shared execution framework.

### Local daemon maintenance and pre-execution checks (implemented)

- Add an explicit local daemon maintenance state, separate from daemon health.
  Expose availability and an optional operator reason through the visibility API.
- Clients check maintenance before submission and show affected targets in CLI,
  TUI and scenario results. Never infer maintenance from a network failure.
- Enforce maintenance atomically at server-side admission, not only in the
  client: a direct POST or a race after the client check must still be refused
  with a stable, machine-readable maintenance reason.
- Running jobs continue by default; visibility and output reads remain available.
  Agreed policy: already admitted queued jobs wait and resume when maintenance
  ends, even if the client has left. Recheck availability and authorization
  before process launch; waiting jobs remain queued with no start timestamp.
- If no eligible configured failover origin remains, a maintenance refusal
  prevents scenario progression on that target, with an explicit blocked/skipped
  reason; independent targets may continue. Only the safe pre-admission failover
  rules below permit another submission; never replay an ambiguous POST.
- Implemented: authenticated `POST /maintenance`, explicit `maintenance_operators`,
  health availability metadata, client precheck and typed HTTP 503 refusal.
  State is in memory and initializes from YAML on restart.
- Restrict maintenance changes to explicitly authorized operators. Keep state
  local to each daemon, without Centrex or mandatory central coordination.
- Test admission races, queued/running behavior, direct HTTP enforcement,
  authorization and partial multi-target outcomes before delivering the feature.

### Ordered failover origins per target (implemented)

Extend the client inventory so one logical target can declare ordered replacement
daemons. Keep the existing `name: URI` form, inline `NAME=URI` and legacy repeated
`--uri` behavior compatible; no additional CLI flag is needed for this extension.

Supported YAML:

```yaml
targets:
  deploy:
    uris:
      - https://autond-01.example.com
      - https://autond-02.example.com
```

- `--target deploy` selects one logical destination with ordered failover origins.
  Multiple targets still mean explicit execution on each target; replacement
  origins are never implicitly broadcast destinations or a target group.
- Allow explicit references to individual declared targets in the ordered
  `uris` list, alongside literal URI strings, to reuse connection definitions.
  Example: `uris: [{target: node-01}, {target: node-02}]`, with both nodes declared
  using the existing `name: URI` form.
- Exclude target-group references from failover lists in the first version.
  Target groups select multiple execution destinations; they do not declare
  interchangeable daemons. Changing group membership must not silently alter
  a failover chain.
- Resolve references before network access; reject missing references and
  self/indirect cycles, bound expansion, and preserve declared order. Deduplicate
  normalized origins within a resolved chain, retaining their first occurrence.
- Validate the complete nonempty, bounded resolved URI list before execution,
  using the same origin checks as individual targets. Retain duplicate-origin
  protections across separately selected execution targets.
- An unavailable health precheck may move to the next origin before any POST.
  Try the next origin after POST only when connection failure proves no submission could
  have been accepted, or a trusted maintenance precheck/explicit daemon refusal
  establishes that no job was admitted. A generic HTTP 503 is not such proof.
- Do not fail over after an ambiguous POST, read timeout, lost response or known
  admission. Once admitted, pin status and output reads to the accepting daemon;
  expose unknown outcomes and the contacted origin for manual reconciliation.
- Record attempted origins, safe refusal/failure reasons and the accepting origin
  under the logical target result. Bound all attempts by the operation budget.
- Replacement daemons must be configured by the operator to perform equivalent
  work with appropriate endpoint permissions and shared dependencies. Do not
  assume another host can replace a command acting on a specific machine.
- Scenario affinity is implemented: the first successful step pins the accepting
  daemon for every remaining step/scenario in that invocation. Later failure or
  maintenance stops that target; no cross-host continuation is inferred.
- TUI execution shows chains and attempted origins. Monitoring deliberately stays
  on primary origins; select physical target names to inspect replacement daemons.
  A richer origin-aware monitoring view remains future work.
- Cover maintenance/admission races, unreachable origins, exhausted lists,
  ambiguous responses, pinned observation and legacy compatibility in tests.

## Milestone 6 — Reliability and lifecycle

Continue product hardening:

Implemented: optional per-daemon JSONL lifecycle journal with size-based rotation,
bounded retention, execution identity and metadata-only records. Journal write
errors do not change command outcomes. This does not provide job restoration
or transactional durability. Real terminal captures are included in the README.

- improve cleanup of expired jobs;
- Implemented: optional local SQLite job snapshots, terminal output restoration,
  explicit interrupted/uncertain recovery without replay, independent of authentication;
- improve reproducible builds;
- review Python 3.13+ compatibility when dependencies allow it;
- strengthen HTTPS/auth deployment guidance;
- add integration tests for multi-daemon aggregation;
- add integration tests for explicit multi-target operations;
- preserve process-group cleanup and output limits;
- keep ACL checks both at admission and execution.

Persistent job storage is optional; the default remains in memory. Redis is a later
adapter, with independent auth/history configuration and mixed backends when supported.

## 1.0 gate — Authentication and local daemon web console

Before the public website, strengthen authentication and deliver an optional web
console served by autond. It uses the same application services and authorization
as the HTTP API. No central service is required.

- Safe local defaults and an explicit migration from historical route-based auth.
- Protect all configured routes in required mode; retain endpoint ACLs and ownership.
- Verify modern password hashing across supported packages and images.
- HTTPS guidance, client credential profiles and safe secret input.
- Bound failed authentication attempts and audit refusals without credentials.
- Browser authentication, CSRF protection, safe output rendering and security headers.
- Console: daemon state, maintenance, allowed endpoints, jobs, output, search and
  confirmed single-endpoint execution. Targets and scenario orchestration remain
  client-side for this first console.
- Test permissions and browser flows before screenshots, recording and publication.
- Implemented: HTTPdis-backed persistent SQLite authentication, Argon2 accounts,
  expiring/revocable scoped tokens, local `autond-auth` administration and client
  `-k` token files for CLI/TUI. Required mode protects all routes; endpoint ACLs,
  ownership and maintenance operator checks remain in force. Basic stays available
  as an explicit compatibility mode.
- Authentication state and optional local job history survive restart in separate
  SQLite files. Interrupted jobs are never replayed. Redis is deferred; configure
  the two stores independently and allow mixed backends once both are supported.
- SSO/OIDC and mTLS remain separately scoped extensions.

## Milestone 7 — Website

Domain purchased: **auton.run**. DNS and website deployment remain to be configured.

Create a dedicated **Auton website**, following the same product/documentation approach used for monit-docker.

The site should become the main public entry point for the project and explain the product before exposing implementation details.

Initial sections:

- product overview;
- auton vs autond architecture;
- installation;
- quickstart;
- CLI examples;
- TUI screenshots and usage once available;
- multi-autond usage;
- failover semantics;
- multi-target operations;
- security and deployment guidance;
- integrations and CI/CD examples;
- documentation;
- releases / changelog;
- GitHub and PyPI links.

The site should:

- have its own visual identity based on the existing Auton branding;
- be lightweight and mobile-friendly;
- keep documentation synchronized with releases;
- host screenshots locally rather than relying on GitHub image links;
- use galleries/lightboxes where useful;
- present real terminal recordings and local screenshots using disposable demo
  daemons; do not expose a public remote-execution daemon;
- provide a contact form with spam protection (recipient auton@doowan.net; delivery configuration
  to be supplied), alongside GitHub issue links;
- expose the current project version clearly;
- be deployable automatically from GitHub;
- keep the main documentation browsable directly on the site.

A public demo may be added only if remote execution can be demonstrated safely without exposing arbitrary command execution or insecure credentials.

## Milestone 8 — Documentation and release quality

Before a future major stable release:

- document client/daemon responsibilities;
- document failover vs multi-target execution;
- document operation IDs and job IDs;
- add TUI screenshots/examples;
- add multi-daemon examples;
- add CI/CD usage examples;
- document failure modes;
- document authentication and HTTPS deployment;
- maintain migration notes and backward compatibility expectations;
- ensure the website and repository documentation are generated or updated together.

## Compatibility principles

- preserve existing CLI behavior unless a change is explicitly versioned;
- preserve current failover safety semantics;
- keep `autond` usable without the TUI;
- keep `auton` usable as a simple command-line client;
- do not require a database for basic operation;
- do not make the richer UI mandatory for automation or CI/CD use;
- keep transport, application logic and presentation separated.

## Current priorities

1. Local `autond` visibility API.
2. Auton ncurses/TUI.
3. Multi-`autond` aggregated view.
4. Explicit multi-target operations.
5. Reliability and integration tests.
6. Dedicated Auton website.
7. Documentation and release polish.
