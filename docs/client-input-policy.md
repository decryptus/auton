# Client input policy and migration

These policies are introduced in the Auton 1.4.0 transition release. The default
changes in Auton 2.0, available for isolated testing as 2.0.0rc3.

In the transition release, omitted `allow-*` settings retain the 1.x permissions
and generate startup warnings for each affected endpoint/category. Warnings do
not prevent startup and never contain argument or environment values. The
read-only `autond --check-migration -c config.yml` also reports missing policies.
In 2.0, omitted permissions deny client additions. A denial is an explicit HTTP
400 before admission; the daemon continues serving other requests. Invalid
policy syntax is a configuration error at startup.

## Arguments and environment

```yaml
endpoints:
  report:
    plugin: subproc
    config:
      prog: /opt/scripts/report.sh
      args: ["--format", "pdf"]
      allow-args:
        month:
          regex: '20[0-9]{2}-(0[1-9]|1[0-2])'
          repeatable: true
          nargs: '+'
        verbose:
          nargs: 0
      allow-env:
        TZ:
          choices: [UTC, Europe/Paris]
      disallow-env: [LD_PRELOAD, PYTHONPATH]
```

`allow-args: [month]` accepts `--month VALUE` or `--month=VALUE`, with one value
of any content and one occurrence. Bare names mean long options; use `-m` to
allow precisely that short option. `month: 'REGEX'` is shorthand for a regex
rule. A mapping may use `regex`, `choices`, `repeatable` and `nargs`. Regexes
match the entire value with a bounded evaluation time. Choice values must be
YAML strings; use quotes for numeric-looking values. Both constraints apply
if both are configured. Environment names are literal and case-sensitive;
`nargs` and `repeatable` apply only to arguments.

`nargs` accepts 0 (a flag), a fixed integer, `+` (one or more) or `*` (zero or
more). Every value is checked separately. Repetition is refused unless
`repeatable: true`. A restricted policy refuses unknown options, positional
arguments, abbreviations and compact short forms such as `-abc` or `-mVALUE`.
Values beginning with an option-like dash must use `--month=VALUE`; negative
numbers such as `-1` can also be separate values. Auton keeps the original
argument tokens, repeats and ordering. It does not build a shell command,
transform values or decide how the target program interprets repeated options.

## Opening a category and exceptions

```yaml
allow-args: true
allow-env: true
allow-argfiles: true
allow-envfiles: true
disallow-args: [dangerous-option, '-x']
disallow-env: [LD_PRELOAD]
disallow-envfiles: [/etc/auton/private.env]
```

This explicitly permits all four categories, except named denials. `disallow-*:
true` forbids the whole category, even if allowed. `false` adds no denial; in
2.0 it does **not** grant permission by itself. Empty allowlists grant nothing.
A deny list matches exact names, not program-specific aliases; list every alias
or prefer an allowlist. Compact short options are refused when using unrestricted
arguments with exceptions. A literal `--` ends option processing only with
unrestricted arguments. The called program is still responsible for its own
semantics. Explicit unlimited access should be restricted to trusted callers.

## Files

Files declared under the server's `args`, `env`, `argfiles` or `envfiles` remain
usable independently of client permissions.

Client **argfiles are uploads**, not paths selected on the server. Auton decodes
the uploaded bytes into a private temporary directory and passes that file's
path to the program. `allow-argfiles: ['-f']` permits that upload option; a
mapping can validate its original filename and allow repeated upload options.
The `-f@` syntax is checked as `-f` and still passes an `@path` value to programs
that support it. This permission is separate from `allow-args`; neither grants
the other. The contents are opaque program data: JSON, certificates, binary
files, etc. They are **not** parsed as CLI arguments. Do not authorize file
options whose target-program behavior gives callers more authority than intended.

Client **envfiles select existing server files**. Set `allow-envfiles` to `true`
or a list of exact absolute paths. Exceptions use `disallow-envfiles`. Resolved
paths are checked, so a symlink alias cannot bypass a denied resolved path.
Administrators must control allowed files and their parent directories.
The old candidate name `allow-client-envfiles` remains an alias with a warning;
configure only one of the two names.

```yaml
allow-envfiles: [/etc/auton/report.env]
allow-env:
  TZ:
    choices: [UTC, Europe/Paris]
```

Every variable read from a client-selected file must also pass `allow-env` /
`disallow-env`, including variables later overridden by another source. Auton
reads each file once before queueing, validates it and executes with that saved
snapshot; later file changes do not change admitted tasks. Reads are limited to
regular UTF-8 files of at most 1 MiB. Empty values use `NAME=`; a key without `=`
has no string value and is rejected. Explicit policies keep `${...}` literal;
they never expand the daemon's environment. The transition keeps legacy dotenv
interpolation only for wholly implicit envfile/environment policies. Move fixed
files into server configuration or declare their paths and variable rules.

## Migration checklist

1. Review startup warnings or the offline migration report.
2. Add selective policies for every client input used by the endpoint, or
   explicitly set its `allow-*` to `true` to retain unrestricted access.
3. Test valid and invalid calls against the transition release.
4. Test a copy of the configuration on 2.0 before upgrading production.

All four permissions must be explicit to retain unrestricted 1.x client input.
Existing `disallow-*` booleans remain supported. Rules reject invalid input as a
whole; Auton never silently drops a forbidden option or variable. Policy errors
identify the option/category without echoing its value. A policy validates
strings up to 64 KiB without NUL bytes; the normal request size/count limits
also remain in force.

## Reusing policy blocks

Auton 1.5.0 and 2.0.0rc3 support [six terminal YAML block imports](configuration-imports.md),
including imports inside `import_config`. Imported rules use the same policy syntax.
