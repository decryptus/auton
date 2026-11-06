# Reusable argument and environment blocks

Available in the next release; not in 1.4.0 or 2.0.0rc2.

An endpoint can declare `import_config` and any combination of these six imports:

| Import | Loaded setting | File contents |
| --- | --- | --- |
| `import_args` | `args` | List of strings, preserving order and duplicates |
| `import_env` | `env` | Mapping of environment names to string values |
| `import_allow_args` | `allow-args` | Boolean, option-name list or rule mapping |
| `import_disallow_args` | `disallow-args` | Boolean or option-name list |
| `import_allow_env` | `allow-env` | Boolean, variable-name list or rule mapping |
| `import_disallow_env` | `disallow-env` | Boolean or variable-name list |

Use the same policy syntax as [inline client rules](client-input-policy.md).
The imported file contains the value directly, without an `args:` or
`allow-args:` wrapper. For example, an `allow-args.yml` can contain:

```yaml
- month
```

This permits one `--month VALUE` or `--month=VALUE`. A more selective file is:

```yaml
month:
  regex: '20[0-9]{2}-(0[1-9]|1[0-2])'
  repeatable: true
```

## Allowed import levels

A main configuration or endpoint catalogue declares endpoints. Each endpoint can
import one configuration. That configuration may import several different blocks,
including all six above, but cannot import another configuration, vars, users,
modules or an endpoint catalogue. The blocks are all at the same level. None may
contain another import, including one nested inside a list or mapping. Reserved
import keys are consequently not available as environment or policy names in blocks.

Block imports can also be written directly beside `plugin` or in inline `config`.
Each directive accepts one nonempty local filename, not a URL or list of files.
Relative paths start at the declaring file's directory, resolving symlinks to the
real file. They do not depend on the daemon's working directory.

## Precedence

The imported configuration supplies the initial settings. Direct endpoint block
imports replace those settings. Inline `config` replaces both. Within a configuration,
an explicit value such as `allow-args` replaces its `import_allow_args` result.
Replacement affects the entire block, including mappings and lists: use `[]`, `{}`
or `false` when appropriate to clear a setting. No list concatenation or recursive
mapping merge occurs. This prevents an inline restriction from accidentally retaining
permissions from a shared allowlist. Every declared import is still loaded and
validated, even when its value will be overridden.

## Loading and errors

Blocks are plain YAML data, with no Mako rendering or executable include tags.
`import_config` retains its administrator-controlled Mako template support and
previously loaded `vars`; such configuration templates are trusted code. Fixed
argument/environment strings retain their existing execution-time formatting.

Missing files, invalid types or policies, duplicate YAML keys, forbidden nested
imports, recursive YAML aliases and excessive nesting fail startup with a configuration
error. A block source is limited to 1 MiB and 32 container levels. Diagnostics identify
the block or path without echoing argument/environment values. Resolution finishes
before endpoint plugins are initialized. Restart the daemon after changing these files;
already loaded settings are snapshots, not per-request reads.

The offline migration checker never executes component templates. Where effective
permissions depend on an import, it reports manual review and the conservative
missing-policy warnings. Confirm the effective startup policies before migration.

Imports do not grant client access by themselves. Fixed `args` and `env` remain
server settings, while `allow-*` and `disallow-*` regulate client additions.
