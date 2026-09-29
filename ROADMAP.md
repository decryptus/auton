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
- filter jobs by state, endpoint and owner;
- inspect detailed job status;
- list configured endpoints;
- expose daemon health;
- expose basic runtime statistics;
- preserve current authentication and ACL checks;
- keep existing `run` and `status` behavior backward compatible.

Suggested API capabilities:

```text
GET /jobs
GET /jobs/<id>
GET /endpoints
GET /health
GET /stats
```

Exact routes remain implementation details and must respect existing HTTP conventions.

## Milestone 2 — Auton TUI

Add a ncurses/TUI interface to the **auton client**, built on top of `RemoteClient`.

The TUI should provide:

- daemon selection;
- endpoint browsing;
- running jobs;
- queued jobs;
- completed jobs;
- stdout/stderr inspection;
- job details;
- live refresh;
- filtering/search;
- clear daemon health state.

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

Allow one `auton` client to query several `autond` instances and display an aggregated live view.

Important rule:

> Existing multiple-URI behavior is failover, not broadcast.

The aggregated view must therefore distinguish:

- connection/failover configuration;
- read-only aggregation of daemon state;
- explicit multi-target execution.

The first multi-daemon implementation should remain client-side and stateless.

## Milestone 4 — Explicit multi-target execution

Add a deliberate way to execute the same logical operation on multiple selected daemons.

Possible CLI direction:

```text
auton run --target autond-01 --target autond-02 ...
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

## Milestone 6 — Reliability and lifecycle

Continue product hardening:

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
