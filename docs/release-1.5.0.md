# Auton 1.5.0 — reusable configuration blocks

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

Authentication and client-permission defaults remain unchanged on the 1.x line.
Use the startup warnings and offline migration report to prepare for Auton 2.0.

```sh
python -m pip install --upgrade 'auton==1.5.0' 'autond[auth]==1.5.0'
docker pull decryptus/auton:1.5.0
```

Auton 2.0.0rc3 is a separate prerelease for isolated migration tests. Its publication
does not approve a production upgrade; stable 2.0 still requires operator acceptance.
