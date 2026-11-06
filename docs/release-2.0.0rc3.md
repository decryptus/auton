# Auton 2.0.0rc3 — reusable blocks and migration testing

This is a prerelease for isolated migration testing. Auton 1.5.0 is the stable line.
The stable 2.0 target remains 19 November 2026, conditional on operator migration
acceptance and resolution of release blockers. Earlier RC acceptance does not
qualify the new import-resolution behavior.

The release adds six YAML imports: `import_args`, `import_env`, `import_allow_args`,
`import_disallow_args`, `import_allow_env` and `import_disallow_env`. They may coexist
on an endpoint, in inline `config`, or in its `import_config` file.

Only downward imports are supported. An imported configuration cannot import another
configuration. Argument, environment and policy blocks are terminal plain YAML and
cannot contain imports or execute Mako templates. Paths are relative to the declaring
file. Inline values replace complete imported blocks; lists and allowlists are not
silently combined. Missing, malformed or forbidden imports fail startup explicitly.

See the [configuration import guide](configuration-imports.md) and
[client policy guide](client-input-policy.md). The complete example contains all six
blocks. Existing administrator-controlled `import_config` Mako templates remain
supported, with the documented restrictions on nested imports and duplicate keys.

Authentication is required by default. Client arguments, environment variables,
uploads and selected server envfiles need explicit permissions. Fixed server
inputs remain usable; disallow rules take precedence.

```sh
python3 -m venv auton-rc3
. auton-rc3/bin/activate
python -m pip install 'auton==2.0.0rc3' 'autond[auth]==2.0.0rc3'
docker pull decryptus/auton:2.0.0rc3
```

Review 1.5.0 migration warnings first. Test a copy of the configuration on a separate
loopback instance with copied or disposable authentication/job databases and harmless
commands. Verify existing credentials, endpoint permissions, valid and rejected client
inputs, envfile snapshots and the new import paths and precedence. Forbidden imports
must fail startup; invalid requests must not create jobs. Preserve the original
installation and report outcomes without credentials, values or raw configuration.
Publishing RC3 does not approve a production upgrade.
