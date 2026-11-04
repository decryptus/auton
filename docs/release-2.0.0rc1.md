# Testing the Auton 2.0 release candidate

Auton 1.3.7 remains the stable release. To evaluate the candidate in a disposable Python environment, clone its branch and install the daemon and client separately.

```sh
git clone --branch preview/auton-2.0.0-rc1 https://github.com/decryptus/auton.git auton-rc1
cd auton-rc1
python3 -m venv .venv
. .venv/bin/activate
AUTON_PACKAGE=autond python -m pip install '.[auth]'
AUTON_PACKAGE=auton python -m pip install .
```

Copy the 1.x configuration and any supporting files to an isolated instance. Point the copy at disposable authentication and job databases, a separate loopback port, and harmless test commands. Run `autond --check-migration -c /path/to/copy.yml` to identify legacy settings. It reports configuration choices for review and does not run jobs.

## Authentication

An omitted `general.auth_mode` now selects `required`. Provide an existing Basic password file or configure the SQLite authentication backend and provision an account. A daemon with neither will refuse to start. Check that a protected API request without credentials returns HTTP 401 and that the same request succeeds for an authorized user. Explicit `legacy` retains route-specific authentication. Explicit `anonymous` is for isolated loopback development.

## Environment files

Fixed server-side files remain available to the subprocess on every run. If a client needs to select an additional file, list its exact absolute server path per endpoint.

```yaml
endpoints:
  deploy:
    plugin: subproc
    config:
      prog: /usr/local/bin/deploy
      envfiles:
        - /etc/auton/base.env
      allow-client-envfiles:
        - /etc/auton/staging.env
```

With this example, a request without envfiles loads base.env. A request selecting staging.env by its full path loads both. Any unlisted selection returns HTTP 400 before the job is queued. Keep the listed files and their parent directories under administrator control. An empty or omitted allowlist denies all client-selected paths, including when a 1.x configuration sets `disallow-envfiles: false`. A nonempty allowlist conflicts with `disallow-envfiles: true`.

After testing, stop the isolated daemon and discard its scratch data. Resume using the stable instance with its original configuration and databases.
