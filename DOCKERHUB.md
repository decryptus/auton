# Auton

2.0.0rc5 uses HTTPdis 0.7.1 or later for the daemon.


Auton runs explicitly configured jobs on remote servers through an authenticated
HTTP API. `auton` is the client; `autond` is the daemon in this image.

## Image and documentation

The versioned image is `decryptus/auton:{{version}}` (also tagged
`decryptus/auton:v{{version}}`). Use an explicit version; this release process
does not maintain a `latest` tag.

```sh
docker pull decryptus/auton:{{version}}
docker run --rm --entrypoint autond decryptus/auton:{{version}} -d
```

The second command checks the installed daemon and exits; it does not start a
configured service. For a service, follow the
[authenticated Compose quickstart](https://auton.run/docs/index.html#quickstart)
and the [configuration guide](https://auton.run/docs/index.html).
The repository's Compose example builds the checked-out release locally; the
commands above obtain the published image.

- [English manual](https://auton.run/docs/index.html)
- [Documentation française](https://auton.run/docs/fr/index.html)
- [Source and versioned examples](https://github.com/decryptus/auton/tree/v{{version}})
- [Release notes](https://github.com/decryptus/auton/releases/tag/v{{version}})

## Migrating toward 2.0

Version 1.5.0 adds reusable YAML imports for arguments, environment and client policies.
An imported configuration may load all six blocks; blocks cannot import further files.
Explicit client policies and startup migration warnings remain available.
Existing implicit 1.x permissions remain enabled. Declare the required
`allow-args`, `allow-env`, `allow-argfiles` and `allow-envfiles` before moving to
2.0, which denies those additions by default. See the
[migration guide](https://auton.run/docs/client-input-policy.html).
The 2.0.0rc5 image is a separate prerelease for isolated tests.

## Capabilities

- Authenticated job execution with endpoint permissions, deadlines and bounded output.
- CLI, curses and optional Textual clients; authenticated web console.
- Multi-daemon observation, explicit multi-target operations and job output browsing.
- SQLite authentication and persistent job history; optional Redis history.
- Scoped cancellation and durable observation without blindly replaying uncertain jobs.

The daemon runs as a non-root user. Preserve its configuration, credentials and
persistent volumes when upgrading. Configure endpoint permissions explicitly;
use HTTPS for remote access and keep the daemon on a trusted private interface
behind the reverse proxy. The image includes the SQLite authentication extra.
New installations should follow the current authentication guide rather than
create historical SHA-1 password entries.

## Client installation

```sh
python -m pip install 'auton=={{version}}'
# Optional Textual interface
python -m pip install 'auton[textual]=={{version}}'
```

Python 3.10–3.13 are supported. Job acceptance is distinct from completion;
losing the connection does not prove that an external command stopped.

Auton is free software under the GNU GPL. See the
[repository license](https://github.com/decryptus/auton/blob/master/LICENSE).
