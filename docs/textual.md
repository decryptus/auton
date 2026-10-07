# Textual terminal candidate

The optional Textual interface requires Python 3.9+ and the unreleased shared
DWho Textual candidate. Existing commands retain curses as their default.
This feature is available on a review branch, not in the published release.

From this candidate checkout:

```sh
python -m pip install 'git+https://github.com/decryptus/dwho.git@fdc3a12b1547c9f78ebec28e1e53770444f34caf'
AUTON_PACKAGE=auton python -m pip install '.[textual]'
auton --tui --tui-ui textual --uri https://daemon.example.org
```

Jobs, endpoints, stdout/stderr and daemon views share a searchable table and a scrollable detail panel. Enter opens a job output or filters jobs by endpoint. `[` / `]` switch daemons; `a` switches between one daemon and all daemons. `s` cycles job states, `c` clears filters, `r` requests refresh, `p` pauses refresh and `v` switches stdout/stderr.

This candidate is **read-only**. Execution preparation, scenario editing, result export and reconciliation still use the existing curses interface or CLI. It does not claim feature parity with that operator workflow. Authentication, TLS settings, explicitly selected targets and separate monitoring of failover origins use the existing client services. Displayed command output is bounded by the daemon response and the shared detail panel (65,536 characters).

`/` focuses search, Escape clears it, and `q` quits. Resize to at least 80 by
24; 120 columns or more gives the detail panel more room. States have explicit
text as well as color. An accepted or running request is not labelled successful.

## Offline demonstration

```sh
python -m auton_client.textual_demo
```

This opens the actual interface with labelled synthetic fixtures. It connects
to no service and performs no operation. Screenshots generated from it are
candidate demonstrations, not production observations or release evidence.

See the [contributor validation and media guide](textual-review.md) for tests
and capture generation. The published manual and existing screenshots still
represent the current release until migration is accepted.
