# Auton 1.3.4

This maintenance release hardens job admission, daemon startup and HTTP connections.

Targets that forbid client arguments, argument files, environment variables or environment files now reject nonempty forbidden fields with HTTP 400 before a job enters the queue. Clients should omit those fields. The Docker quickstart also disables client-supplied environment variables on its greeting endpoint.

When started as root, the daemon initializes the service account's supplementary groups before dropping privileges. Startup failures return a nonzero status. Anonymous mode checks the effective listening address, including command-line overrides.

Clients have ten seconds per request to send the request line and all HTTP headers. Sending bytes slowly does not extend the deadline. With native mTLS the deadline begins after the handshake. Request bodies and job execution have separate behavior.

## Upgrade from 1.3.3

Upgrade both PyPI packages to 1.3.4 or deploy the versioned daemon image. Preserve your authentication configuration, credentials and persistent volumes. Existing legacy authentication policies are supported. Review clients that previously sent fields their target forbids.

```sh
python -m pip install --upgrade 'auton==1.3.4'
# On hosts running the Python daemon with SQLite authentication:
python -m pip install --upgrade 'autond[auth]==1.3.4'
# For container deployments:
docker pull decryptus/auton:1.3.4
```
