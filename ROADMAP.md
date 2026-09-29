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

### Named scenarios and job sequences (next development milestone)

Keep one job as one command on one daemon. An operation may later describe
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

This is a planned capability: scenario files and a scenario CLI option are not
yet implemented. Multi-target execution currently runs one job per target.

Configuration layout:

- Client `targets.yml`: named daemon origins, selected explicitly at invocation.
- Client `scenarios/<name>.yml`: one versioned scenario and its ordered steps.
- Daemon `endpoints.yml`: optional separate endpoint definitions, command settings
  and endpoint ACLs, loaded by `autond`; adding this import is future work. Keep
  existing inline daemon endpoint configuration compatible.

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

### Target groups and scenario selection (planned)

- Add `--target-group NAME` to select an explicit named group from the client
  inventory. Combine explicit targets and groups as a stable union, with each
  target executed once; reject unknown members and keep failover separate.
- Add `--scenario NAME` for one scenario and `--scenario-group NAME` for an
  ordered group of scenarios, retaining existing endpoint execution commands.
- Expose the same selections in the TUI: targets/groups, endpoint or scenario,
  arguments, an exact target/action summary before launch, then per-target results.
- Support startup filtering before opening the TUI: `--target`,
  `--target-group`, `--scenario` and `--scenario-group`. Preserve existing
  `--daemon` usage. With `--tui`, these flags select what can be browsed and
  prepared; they never submit jobs automatically.
- Selection filters must support exact names, globs and regular expressions for
  targets, target groups, scenarios and scenario groups, shared by CLI and TUI.
  Proposed unambiguous syntax: `web-01` (exact), `glob:web-*` and
  `regex:^web-[0-9]+# Auton Roadmap

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

### Named scenarios and job sequences (next development milestone)

Keep one job as one command on one daemon. An operation may later describe
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

This is a planned capability: scenario files and a scenario CLI option are not
yet implemented. Multi-target execution currently runs one job per target.

Configuration layout:

- Client `targets.yml`: named daemon origins, selected explicitly at invocation.
- Client `scenarios/<name>.yml`: one versioned scenario and its ordered steps.
- Daemon `endpoints.yml`: optional separate endpoint definitions, command settings
  and endpoint ACLs, loaded by `autond`; adding this import is future work. Keep
  existing inline daemon endpoint configuration compatible.

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

### Target groups and scenario selection (planned)

- Add `--target-group NAME` to select an explicit named group from the client
  inventory. Combine explicit targets and groups as a stable union, with each
  target executed once; reject unknown members and keep failover separate.
- Add `--scenario NAME` for one scenario and `--scenario-group NAME` for an
  ordered group of scenarios, retaining existing endpoint execution commands.
- Expose the same selections in the TUI: targets/groups, endpoint or scenario,
  arguments, an exact target/action summary before launch, then per-target results.
- Support startup filtering before opening the TUI: `--target`,
  `--target-group`, `--scenario` and `--scenario-group`. Preserve existing
  `--daemon` usage. With `--tui`, these flags select what can be browsed and
  prepared; they never submit jobs automatically.
; quote shell arguments. Preserve inline `NAME=URI`.
  Prefix syntax is a proposal, not an implemented CLI contract.
- Match patterns against declared names, not URIs or arbitrary file paths.
  Match group names before expanding membership; deduplicate resolved targets.
  Use full-name regex matching, bound pattern length and matching work, and
  reject invalid or pathological expressions explicitly. A selector with no
  matches must fail clearly before execution, never silently fall back to all.
- Resolve and validate selections before curses initialization or network access.
  Combine target/group selections as a deduplicated union; scenario/group
  selections independently restrict the action catalogue. Contact only selected
  daemons. Reject unknown names; never broaden an empty/invalid selection to all.
- Display active filters and the resolved targets in the TUI. Changing the scope
  must be explicit, and execution still requires reviewing the selected action
  and exact destinations. These are operator filters, not authorization rules.
- Scenario filters initially apply to the scenario catalogue, not to historical
  jobs that lack scenario/operation metadata.
- Reuse application services across CLI and TUI; server-side authorization
  remains authoritative. These selection options are not yet implemented.

### Scenario Group (planned, after individual scenarios)

Add named, ordered groups of existing scenarios, distinct from target groups.
A target group selects where to execute; a scenario group selects what to run.

- Proposed CLI: `--scenario-group maintenance`, combined with explicit targets
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

This is a roadmap item, not an available command. Development remains focused
on Auton; shared terminology does not require a shared execution framework.

## Milestone 6 — Reliability and lifecycle

Continue product hardening:

Implemented: optional per-daemon JSONL lifecycle journal with size-based rotation,
bounded retention, execution identity and metadata-only records. Journal write
errors do not change command outcomes. This does not provide job restoration
or transactional durability. Real terminal captures are included in the README.

- improve cleanup of expired jobs;
- review persistence requirements for local daemon jobs;
- improve reproducible builds;
- review Python 3.13+ compatibility when dependencies allow it;
- strengthen HTTPS/auth deployment guidance;
- add integration tests for multi-daemon aggregation;
- add integration tests for explicit multi-target operations;
- preserve process-group cleanup and output limits;
- keep ACL checks both at admission and execution.

Persistent storage is not mandatory for the initial multi-daemon client work. It should only be introduced if product behavior requires it.

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
