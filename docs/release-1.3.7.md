# Auton 1.3.7

Inspect a local daemon configuration before migrating to 2.0. The command runs offline without starting the daemon, executing jobs or reading credentials.

```sh
autond --check-migration -c /etc/auton/auton.yml
```

Exit code 0 means neither implicit legacy authentication nor permission for client-selected environment files was found. Code 1 means an item needs review. Code 2 means the file or its endpoint catalogues could not be inspected.

A setting that depends on an imported component is reported for manual review. The diagnostic does not execute templates. Compare its findings with the daemon's migration warnings over a representative period, including scheduled jobs. Server-configured environment files remain supported.

Upgrade the daemon package to 1.3.7 to use the command. The check is optional and does not change the behavior of existing 1.x installations.
