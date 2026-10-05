# Contributor documentation

This guide is for people changing or maintaining auton. For installation, configuration and everyday use, start with the [user documentation](https://github.com/decryptus/auton/blob/master/README.md).

## Roadmap

See [ROADMAP.md](https://github.com/decryptus/auton/blob/master/ROADMAP.md) for the current Auton product roadmap, including the TUI, multi-autond support, multi-target execution and dedicated website.


### Development

```sh
AUTON_PACKAGE=autond python -m pip install '.[auth,oidc,redis]' -r requirements-auton.txt
python .github/scripts/check-test-collection.py --runner unittest tests
python -m unittest discover -s tests -v
AUTON_PACKAGE=autond python -m pip wheel --no-deps . -w dist/server
AUTON_PACKAGE=auton python -m pip wheel --no-deps . -w dist/client
```

The test suite includes real subprocesses and an HTTP client/server integration
with authentication. CI runs Python 3.10, 3.11, 3.12 and 3.13 and builds the Docker image.
Docker builds track the checkout's code; dependencies and the base image are not
fully locked, so builds are not yet bit-for-bit reproducible.


### Application services and remote client

`auton.classes.jobs.JobService` owns admission, endpoint ACLs, job ownership,
capacity, completed-result expiry and output cursors. It accepts explicit job
values and an authenticated principal, plus injected endpoint/queue mappings,
clock, lock and object factory. It does not import the HTTP module, CLI or DWho.
The principal must come from a trusted authentication adapter; it is not a field
accepted from a remote job payload. HTTP continues to use the existing routes,
schemas, response fields and status codes.

Workers read `JobObject.owner` and `get_payload()`, recheck the endpoint ACL before
execution, and release input data on completion. Submitted payloads are copied so
later mutation of the request cannot change a queued command. Command timeouts,
process-group cleanup, bounded output, retention defaults and legacy/explicit
output cursors are unchanged.

The historical `auton.classes.plugins.AutonEPTObject` import, constructor and
callback remain available. For older plugins, `get_request()` returns a detached
compatibility snapshot exposing `payload_params()`, `get_server_vars()` (the
`HTTP_AUTH_USER` value), `get_headers()` and `query_params()`. It is **not** a live
HTTP request: socket access, request identity and arbitrary server variables are
not retained. Headers/query values are copied when the old constructor receives
a request; service-created jobs supply only execution payload and principal.
New plugins should use `owner` and `get_payload()`. Input views are cleared when
a worker completes a job, as the old request reference was.

`JobModule` retains its helper names, result builder and mutable limit/object
attributes as compatibility delegates. Its HTTP handlers now call `JobService`
directly; overriding a private helper no longer intercepts admission. Customize
the service or its adapters instead. Direct calls to the old submission helper
now also enforce admission policy. Each initialized module owns its lock.
Environment names and output offsets must match the complete value, including
rejecting a trailing newline.



## Build verification

### Shared terminal primitives

The client uses DWho 0.3.63 or newer: `dwho.cli.require_terminal` checks
interactive input/output, `dwho.cli.write_json` writes CLI JSON responses, and
`dwho.tui.put` handles bounded drawing and terminal resize races. Auton sanitizes
remote control characters before drawing and owns navigation, refresh, job
preparation, confirmation and the curses lifecycle. Its asynchronous views must
continue refreshing while the operator navigates or edits input.

Only explicit `--tui` enters curses. The CLI checks for a terminal before
importing the TUI or reading its credentials. Ordinary commands remain usable
from cron and pipes without curses. Authentication, transport and execution
services remain independent of presentation. DWho is now a declared client
dependency (including its transitive dependencies); importing its terminal
helpers does not initialize daemon modules or HTTP services.

The installed-client check permits only `dwho.cli`/`dwho.tui` in presentation
and continues blocking DWho in the application services. It also blocks Auton's
daemon package and HTTPdis throughout. Validate changes with the unittest
collection guard and suite, the client-wheel check and `scripts/capture_tui.py`
against disposable local daemons.

### Reproducible builds

Auton 1.2.0 supports Python 3.10–3.13. CI compares wheel bytes from two separate
build directories with the same source, tool versions and `SOURCE_DATE_EPOCH`.
This checks reproducibility within that environment; it does not claim identical
artifacts across arbitrary operating systems or dependency versions.

## Documentation rules

Keep user instructions and contributor material separate. The repository [engineering requirements](https://github.com/decryptus/auton/blob/master/AGENTS.md) define the review and validation rules. Preserve user-facing compatibility, security and recovery guidance when moving internal explanations.
