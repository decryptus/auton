<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/svg/logo-horizontal-dark.svg">
    <img src="assets/brand/svg/logo-horizontal.svg" alt="Auton" width="520">
  </picture>
</p>

## auton project

[![PyPI pyversions](https://img.shields.io/pypi/pyversions/auton.svg)](https://pypi.org/project/auton/)
[![PyPI version shields.io](https://img.shields.io/pypi/v/auton.svg)](https://pypi.org/project/auton/)
[![Docker Hub](https://github.com/decryptus/auton/actions/workflows/dockerhub.yml/badge.svg)](https://github.com/decryptus/auton/actions/workflows/dockerhub.yml)
[![Documentation Status](https://readthedocs.org/projects/auton/badge/?version=latest)](https://auton.readthedocs.io/)

auton is a free and open-source, we develop it to run programs and command-lines on remote servers through HTTP protocol.
There are two programs, auton for client side and autond for server side.
auton is just a helper to transform command-lines into HTTP protocol, it is able to transform basic arguments, file arguments and environment variables.
For example, you can use auton from CI/CD to run on remote servers, you just need to configure your endpoints:
  - [ansible](https://github.com/ansible/ansible)
  - [curl](https://github.com/curl/curl)
  - [terraform](https://github.com/hashicorp/terraform)

You can also use auton if you need to execute a new version of a software but you can't install it on a legacy server
or tests programs execution.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the current Auton product roadmap, including the TUI, multi-autond support, multi-target execution and dedicated website.

## Quickstart

Using autond in Docker

`docker compose up --build -d`

See [docker-compose.yml](docker-compose.yml)

## Runtime and reliability notes

This branch targets Linux/Unix with Python **3.10–3.12**. Python 2 support is
removed. Python 3.13+ is not supported yet because the HTTP dependency uses
`crypt`. Python 3.12 installs the `pyasyncore` compatibility dependency.

The Docker image now installs **this checkout**, runs as the `auton` user, and
Compose publishes port 8666 on **127.0.0.1 only**. The demo still has no
authentication; enable Basic authentication on every route, configure endpoint
users and use HTTPS before exposing it remotely. Basic authentication supports
the documented `{SHA}` htpasswd format and formats available through system
`crypt`. The handler keeps each authenticated identity local to its request.

### Execution and failover

* The first successful server accepts the job; the client stops trying other
  servers. Automatic failover is limited to failures to establish a connection.
  A read timeout or disconnect after sending a request is ambiguous and **does
  not cause another POST**. Use `--mode status --uid <original-id>` to inspect it.
  There is no shared state or cross-server exactly-once guarantee.
* `--http-timeout 30` bounds each HTTP connection/read wait. It is distinct from
  the endpoint's execution `timeout` and is not a total job deadline.
* On execution timeout, the process group is terminated and reaped; the client
  receives exit code 124. Shutdown interrupts the active command with code 130.
  Jobs must remain in their process group; this is not a container sandbox.
* stdout/stderr are decoded as UTF-8, replacing invalid sequences. Partial lines
  are available without waiting for a newline. The initial response is displayed.
* Client arguments are literal, including braces and JSON. Only administrator
  configured arguments support variable interpolation. Uploaded names must be
  plain basenames; absolute paths and traversal are rejected.

### Results and resource limits

New clients send `X-Auton-Output-Offset` on status requests and consume the
`next_offset` from responses. Offsets count output chunks, not bytes. Repeating
an offset returns the same prefix plus any output subsequently produced, without
consuming another reader's output. Legacy clients retain their shared cursor.
A completed job is no longer deleted on the first status request.

Limits in `general`:

| Setting | Default | Behavior |
| --- | --- | --- |
| `result_ttl` | 3600 seconds | Completed results expire; cleanup occurs on subsequent run/status requests. |
| `max_jobs` | 128 | Caps queued, running and retained jobs together. Oldest completed results are evicted first; if all jobs are active, new submissions receive 503. |
| `max_output_bytes` | 1048576 | Combined UTF-8 stdout/stderr budget per job. Exceeding it stops the command with exit code 1. A short diagnostic is retained in addition. |

Results are in memory and are lost on restart. IDs are reserved only while their
job is retained; use a fresh UUID for each operation. Once evicted or expired,
results cannot be replayed and IDs no longer protect against repeat execution.
Status access is restricted to the submitting authenticated user and the current
endpoint permissions. Unauthenticated demo jobs have no individual user identity.

### Local daemon visibility

The daemon also exposes these read-only routes:

| Request | Result |
| --- | --- |
| `GET /jobs` | `jobs`: metadata for the caller's retained jobs, without output. |
| `GET /jobs?endpoint=test&status=complete` | Exact endpoint/state filters. States are `new`, `processing`, `complete`; completion does not imply exit code zero. Invalid filters return 400. |
| `GET /jobs/<endpoint>/<id>` | Metadata and retained output; `X-Auton-Output-Offset` selects stdout chunks, defaulting to zero. |
| `GET /endpoints` | `endpoints`: names allowed by the current endpoint ACLs. |
| `GET /health` | `status: ok` when the local job service can be accessed; not a check of remote dependencies or worker progress. |
| `GET /stats` | `stats`: visible job count, counts by state, and accessible endpoint count. |

Lists, details and statistics enforce current endpoint permissions and job
ownership. Counts do not include other users' jobs; shared queue depths are not
exposed. Detail returns HTTP 200 even when the inspected command failed: inspect
`status`, `return_code` and `errors`. Missing/expired jobs return 404, access denial
returns 403, and a service lock timeout returns 503. Listing, statistics and detail
requests also clean expired results. There is no persistent history.

These reads never advance the legacy `/status` output cursor. Detail uses the
existing output format (`stream` for stdout, `errors` for stderr/diagnostics).
The existing `/run` and `/status` contracts are unchanged.

The example configuration is an unauthenticated local demo: anonymous clients
share one identity. Before remote use, set `auth: true` on **all seven routes**
in `etc/auton/modules/job.yml`, configure Basic authentication and endpoint users,
and serve behind HTTPS. Apply the same protection when adding these routes to an
existing installation; keep the existing `run` route's `safe_init: true` setting.

### Operator TUI (client)

The Unix client includes a read-only ncurses interface. It requires an interactive
terminal with Python curses support and a daemon with the visibility API above.

```sh
auton --tui --uri https://autond.example.net --http-timeout 5

# Named daemons are selected explicitly; only the selected daemon is refreshed.
auton --tui --daemon prod=https://autond-01.example.net \
            --daemon staging=https://autond-02.example.net --refresh 2
```

Authentication uses the existing `--auth-user` / `--auth-passwd` options or
`AUTON_AUTH_USER` / `AUTON_AUTH_PASSWD`. These credentials apply to all explicitly
listed daemons; select only trusted servers sharing that identity. URIs must be
HTTP(S) origins without embedded credentials, paths, queries or fragments.
Use HTTPS for remote connections. Redirects are rejected and failures are not
silently retried against another daemon.

`--daemon` is TUI-only and overrides `AUTON_URI`; it cannot be combined with
explicit `--uri`. One `--uri` (or one `AUTON_URI` value) is accepted as a shortcut.
Multiple execution `--uri` values remain **failover**, not daemon selection or
broadcast. The TUI never submits commands, cancels jobs or creates operations.

| Key | Action |
| --- | --- |
| `[` / `]` | Select previous / next named daemon. |
| `Tab` | Switch Jobs, Endpoints and Output views. |
| Up / Down or `k` / `j` | Select a row; scroll in Output. |
| Enter | Open a job's output; select an endpoint as a job filter. |
| `/` | Edit literal search; Enter or Escape finishes editing. |
| `s` | Cycle all / queued / running / completed job states. |
| `c` | Clear search, state and endpoint filters. |
| `v` | Switch stdout and stderr/diagnostics. |
| Page Up / Page Down | Scroll by ten rows. |
| `r` / `p` | Refresh now / pause automatic refresh. |
| Escape / `q` | Return to jobs / quit. |

Counts and lists retain server-side ownership and endpoint ACL restrictions.
Completion is separate from success: inspect the exit code and stderr. Output
reads replay retained data without consuming the legacy status cursor.
API errors (including older daemons returning 404) are shown explicitly.
The display handles terminal resizing (minimum 64 columns by 12 rows), filters
control characters from remote output, and keeps navigation responsive while a
single background refresh is pending. A daemon switch may wait for the current
bounded refresh; `q` exits immediately. `--refresh` defaults to 2 seconds after a
refresh finishes, with a minimum of 0.2 seconds; `--http-timeout` bounds each GET.
Only the active daemon is polled; this is not yet an aggregated multi-daemon view.

### Development

```sh
python -m pip install -r requirements-autond.txt
python -m unittest discover -s tests -v
AUTON_PACKAGE=autond python -m pip wheel --no-deps . -w dist/server
AUTON_PACKAGE=auton python -m pip wheel --no-deps . -w dist/client
```

The test suite includes real subprocesses and an HTTP client/server integration
with authentication. CI runs Python 3.10, 3.11 and 3.12 and builds the Docker image.
Docker builds track the checkout's code; dependencies and the base image are not
fully locked, so builds are not yet bit-for-bit reproducible.

## Installation

### autond for server side

`pip install autond`

### auton for client side

`pip install auton`

## Environment variables

### autond

| Variable         | Description                 | Default |
|:-----------------|:----------------------------|:--------|
| `AUTOND_CONFIG`  | Configuration file contents<br />(e.g. `export AUTOND_CONFIG="$(cat auton.yml)"`) |  |
| `AUTOND_LOGFILE` | Log file path               | /var/log/autond/daemon.log |
| `AUTOND_PIDFILE` | autond pid file path        | /run/auton/autond.pid |
| `AUTON_GROUP`    | auton group                 | auton or root |
| `AUTON_USER`     | auton user                  | auton or root |

### auton

| Variable               | Description                 | Default |
|:-----------------------|:----------------------------|:--------|
| `AUTON_AUTH_USER`      | user for authentication     | <span/> |
| `AUTON_AUTH_PASSWD`    | password for authentication | <span/> |
| `AUTON_ENDPOINT`       | name of endpoint            | <span/> |
| `AUTON_LOGFILE`        | Log file path               | /var/log/auton/auton.log |
| `AUTON_NO_RETURN_CODE` | Do not exit with return code if present | False |
| `AUTON_UID`            | auton job uid               | random uuid |
| `AUTON_URI`            | autond URI(s)<br />(e.g. http://auton-01.example.org:8666,http://auton-02.example.org:8666) | <span/> |

## Autond configuration

See configuration example [etc/auton/auton.yml.example](etc/auton/auton.yml.example)

### Endpoints

In this example, we declared three endpoints: ansible-playbook-ssh, ansible-playbook-http, curl.
They used subproc plugin.

```yaml
endpoints:
  ansible-playbook-ssh:
    plugin: subproc
    config:
      prog: ansible-playbook
      timeout: 3600
      args:
        - '/etc/ansible/playbooks/ssh-install.yml'
        - '--tags'
        - 'sshd'
      become:
        enabled: true
      env:
        DISPLAY_SKIPPED_HOSTS: 'false'
  ansible-playbook-http:
    plugin: subproc
    config:
      prog: ansible-playbook
      timeout: 3600
      args:
        - '/etc/ansible/playbooks/http-install.yml'
        - '--tags'
        - 'httpd'
      become:
        enabled: true
      env:
        DISPLAY_SKIPPED_HOSTS: 'false'
  curl:
    plugin: subproc
    config:
      prog: curl
      timeout: 3600
```

### Authentication

To enable authentication, you must add `auth_basic` and `auth_basic_file` lines in section `general`:

```yaml
  auth_basic:      'Restricted'
  auth_basic_file: '/etc/auton/auton.passwd'
```

Use `htpasswd` to generate `auth_basic_file`:

`htpasswd -c -s /etc/auton/auton.passwd foo`

And you have to add for each modules route `auth: true`:

```yaml
modules:
  job:
    routes:
      run:
        handler:   'job_run'
        regexp:    '^run/(?P<endpoint>[^\/]+)/(?P<id>[a-z0-9][a-z0-9\-]{7,63})$'
        safe_init: true
        auth:      true
        op:        'POST'
      status:
        handler:   'job_status'
        regexp:    '^status/(?P<endpoint>[^\/]+)/(?P<id>[a-z0-9][a-z0-9\-]{7,63})$'
        auth:      true
        op:        'GET'
```

Use section `users` to specify users allowed by endpoint:
```yaml
  ansible-playbook-ssh:
    plugin: subproc
    users:
      maintainer: true
      bob: true
    config:
      prog: ansible-playbook
      timeout: 3600
      args:
        - '/etc/ansible/playbooks/ssh-install.yml'
        - '--tags'
        - 'sshd'
      become:
        enabled: true
      env:
        DISPLAY_SKIPPED_HOSTS: 'false'
```

### Plugin subproc

subproc plugin executes programs with python `subprocess`.

Predefined AUTON environment variables during execution:

| Variable           | Description                                   |
|:-------------------|:----------------------------------------------|
| `AUTON`            | Mark the job is executed in AUTON environment |
| `AUTON_JOB_TIME`   | Current time in local time zone               |
| `AUTON_JOB_GMTIME` | Current time in GMT                           |
| `AUTON_JOB_UID`    | Current job uid passed from client            |
| `AUTON_JOB_UUID`   | Unique ID of the current job                  |

Use keyword `prog` to specify program path:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
```

Use keyword `workdir` to change the working directory:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      workdir: somedir/
```

Use keyword `search_paths` to specify paths to search `prog`:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      search_paths:
        - /usr/local/bin
        - /usr/bin
        - /bin
```

Use section `become` to execute with an other user:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      become:
        enabled: true
        user: foo
```

Use keyword `timeout` to raise an exception after n seconds (default: 60 seconds):
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      timeout: 3600
```

Use section `args` to define arguments always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      args:
        - '-s'
        - '-4'
```

Use keyword `disallow-args` to disable arguments from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      args:
        - '-vvv'
        - 'https://example.com'
      disallow-args: true
```

Use section `argfiles` to define arguments files always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      argfiles:
        - arg: '--key'
          filepath: /tmp/private_key
        - arg: '-d@'
          filepath: /tmp/data
```

Use keyword `disallow-argfiles` to disable arguments files from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      argfiles:
        - arg: '--key'
          filepath: /tmp/private_key
        - arg: '-d@'
          filepath: /tmp/data
      disallow-argfiles: true
```

Use section `env` to define environment variables always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      env:
        HTTP_PROXY: http://proxy.example.com:3128/
        HTTPS_PROXY: http://proxy.example.com:3128/
```

Use keyword `disallow-env` to disable environment variables from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      env:
        HTTP_PROXY: http://proxy.example.com:3128/
        HTTPS_PROXY: http://proxy.example.com:3128/
      disallow-env: true
```

Use section `envfiles` to define environment variables files always present:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      envfiles:
        - somedir/foo.env
        - somedir/bar.env
```

Use keyword `disallow-envfiles` to disable environment files from client:
```yaml
endpoints:
  curl:
    plugin: subproc
    config:
      prog: curl
      envfiles:
        - somedir/foo.env
        - somedir/bar.env
      disallow-envfiles: true
```

## Auton command-lines

### endpoint curl examples:

Get URL https://example.com:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com'`

Get URL https://example.com with auton authentication:

`auton --endpoint curl --uri http://localhost:8666 --auth-user foo --auth-passwd bar -a 'https://example.com'`

Add environment variable HTTP\_PROXY:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' -e 'HTTP_PROXY=http://proxy.example.com:3128/'`

Import already declared environment variable with argument --imp-env:

`HTTPS_PROXY=http://proxy.example.com:3128/ auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' --imp-env HTTPS_PROXY`

Load environment variables from local files:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' --load-envfile foo.env`

Tell to autond to load environment variables files from its local fs:

`auton --endpoint curl --uri http://localhost:8666 -a 'https://example.com' --envfile /etc/auton/auton.env`

Add multiple autond URIs for high availability:

`auton --endpoint curl --uri http://localhost:8666 --uri http://localhost:8667 -a 'https://example.com'`

Add arguments files to send local files:

`auton --endpoint curl --uri http://localhost:8666 -A '--cacert=cacert.pem' -a 'https://example.com'`

Add multiple arguments:

`auton --endpoint curl --uri http://localhost:8666 --multi-args '-vvv -u foo:bar https://example.com' --multi-argsfiles '-d@=somedir/foo.txt -d@=bar.txt --cacert=cacert.pem'`

Get file contents from stdin with `-`:

`cat foo.txt | auton --endpoint curl --uri http://localhost:8666 --multi-args '-vvv -u foo:bar sftp://example.com' --multi-argsfiles '--key=private_key.pem --pubkey=public_key.pem -T=-'`

### Automatic releases and Docker Hub

Set `DOCKERHUB_TOKEN` in repository Actions secrets to a Docker Hub token with
write access to `decryptus/auton`. The login is `decryptus`.

To release, update `VERSION` and `RELEASE` together (`X.Y.Z`), along with
`setup.yml`, the versions in `bin/auton` and `bin/autond`, and `CHANGELOG.md`.
Merge into `master`: the Docker Hub workflow builds and tests the image, creates
`vX.Y.Z` if absent, and publishes that same image as `decryptus/auton:X.Y.Z`
and `decryptus/auton:vX.Y.Z`. Tag creation and publication happen in the same
workflow, so they do not depend on another workflow being triggered by a bot tag.

Ordinary commits with an already tagged version do not replace that release.
Pull requests build and test only. Existing tags are never moved.
To retry a failed upload or publish an existing release such as `v0.3.0`, select
**Actions → Docker Hub → Run workflow**, use branch `master`, and enter the tag.
The workflow builds the tagged commit, verifies it belongs to master's history,
and tests it before uploading. Published tags are versioned; `latest` is not changed.

## Publishing to PyPI

See [PyPI publishing](docs/pypi.md) for Trusted Publisher setup and automated releases.


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

The `auton` client distribution now provides `auton_client.RemoteClient`:

```python
from auton_client import RemoteClient

client = RemoteClient(
    uris=['https://autond.example'],
    endpoint='backup',
    uid='backup-123',
    payload={'args': ['--verify']},
    auth=('alice', 'password'),
    http_timeout=30,
)
for result in client.iter_results(delay=0.5):
    consume(result)  # Your application decides how to present/store the result.
```

The client accepts an optional requests-compatible `session` and `sleep` callable.
It never reads stdin, prints output or selects a process exit code. Each yielded
batch advances the output cursor; resuming a status request uses that cursor.
`do_run()` and `do_status()` remain available for one-shot operations. An ambiguous
POST failure is never replayed on another daemon. The command-line options,
file/environment preparation, terminal output and exit-code behavior remain in
`bin/auton`. Install the `auton` distribution to use the remote client; `autond`
continues to package the daemon separately.

Job expiry remains lazy on service operations; this change does not introduce a
background expiry thread, durable job storage or a total client polling deadline.
