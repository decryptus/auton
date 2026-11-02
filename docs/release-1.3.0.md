# auton 1.3.0

## Optional Textual interface

This version adds an optional terminal dashboard using the shared
DWho 0.3.65 presentation components. Auton supports Python 3.10–3.13.
The existing curses interface remains the default.

View jobs, endpoints, daemon status and stdout/stderr with search, filtering
and selection-preserving refresh. Textual also supports execution preparation, guided arguments, scenario/group
selection, result export and read-only reconciliation through existing services.
Only explicit confirmation submits an operation; stopping observation does not
cancel remote jobs.

Install with `python -m pip install "auton[textual]==1.3.0"`
and select it with `auton --tui --tui-ui textual --uri https://daemon.example.org`.

No configuration or stored-data migration is required. The offline demo uses
explicitly labelled synthetic fixtures and connects to no service. See the
[Textual guide](textual.md) for controls and limitations.
