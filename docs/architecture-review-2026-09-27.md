# Architecture review — 2026-09-27

Reviewed commit: `502a0dc6ac9e8b8d550685c2ab9b66ec1d893130` on `master`.
Initial status: **review and engineering requirements only**. The follow-up
implementation described below addresses the runtime findings.

Scope: separation of application logic, interfaces and adapters; callback and
initialization ownership; fixed validation contracts. Source files were fetched
at the pinned commit. This PR does not change runtime code or claim CI enforcement
of the new requirements. [Pinned source](https://github.com/decryptus/auton/tree/502a0dc6ac9e8b8d550685c2ab9b66ec1d893130).

All 11 package Python files and both executables were inspected for dependencies
and the job/request/execution paths traced. This is a source review; runtime and
subprocess integration suites were not rerun for this documentation change.

## Confirmed findings

### A1 — High: jobs and worker plugins depend on live HTTP request objects

`auton/modules/job.py` owns job admission, capacity eviction, expiry, ownership,
queue insertion and result construction inside DWho HTTP handlers. These policies
raise `HttpReqErrJson` directly. `_push_epts_sync` stores a copied request in
`AutonEPTObject`; its constructor reads `HTTP_AUTH_USER` from request server vars.
`AutonPlugBase.run` reads that request again for authorization and
`AutonSubProcPlugin.do_run` calls `payload_params()` to obtain execution inputs.

This is transport/application coupling even though the server does not import
`bin/auton`. Extract a JobService with explicit principal and validated JobInput,
neutral errors and injected queue/clock/store. The HTTP module decodes and maps
results; plugins consume job values. Preserve authorization at admission and
execution, ownership checks, locking, capacity, expiry and output bounds.

Acceptance: admit, execute through a fake plugin, poll and expire a job with HTTP
and CLI imports blocked; unauthorized users cannot submit or inspect another
owner's job. No request object survives beyond transport decoding.

### A2 — Medium: the reusable remote client is implemented in the executable

`bin/auton.AutonClient` combines URL construction, POST/GET, failover, response
validation, output-offset tracking, environment/file preparation and terminal
rendering. `do_autorun` mixes polling with `_show_results` and CLI return-code policy.
Extract a client module with explicit options/session and a stream of results;
keep stdin/stdout, argument parsing and process exit mapping in `bin/auton`.
Preserve the existing rule against replaying an ambiguous accepted POST.

### A3 — Low: validation/style contracts are scattered

The environment-name registration and output-offset regex in `modules/job.py`
use inline patterns and `match` with `$`. The offset pattern can match before a
final newline in a direct call. Centralize fixed patterns and use full matching
for identifiers; do not conflate this observation with acceptance by the HTTP
parser. Existing job schemas already use uppercase class constants.

## Positive evidence and limits

The package has no CLI/TUI imports in the AST scan. `bin/autond` is a legitimate
composition/lifecycle entry point. The subproc plugin executes configured targets,
which is the product's purpose; this is not an API launching its own CLI.
The existing execution/output limits, safe retry rules and authorization must
remain intact during extraction. No live job was submitted during this review.

## Follow-up implementation

The application extraction following this review addresses A1–A3:

- `classes/job.py`, `classes/jobs.py` and `classes/job_schema.py` contain job values,
  validation and service policy. HTTP status translation stays in `modules/job.py`.
- Plugins consume detached payload/principal values and recheck execution ACLs.
  The legacy object constructor is a documented snapshot facade.
- `auton_client.RemoteClient` owns remote transport and result iteration; terminal
  input/output and exit policy remain in the CLI executable.
- Fixed validation patterns use full matching. Initialized HTTP modules own their
  locks, while existing daemon endpoint/queue registries remain composition inputs.

Behavioral tests block HTTP, DWho and CLI imports while admitting, executing a fake
job, checking ownership, polling and expiring it. Other tests exercise detached
legacy input, real subprocess execution and ACL rechecks, concurrency/capacity,
queue failure, client offsets and the existing real daemon/CLI integration suite.
See README for the precise compatibility surface and remaining lifecycle limits.
