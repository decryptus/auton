# auton 1.3.0

## Optional Textual interface

This version adds an optional read-only terminal dashboard using the shared
DWho 0.3.65 presentation components. Python 3.9+ is required for this extra.
The existing curses interface remains the default.

View jobs, endpoints, daemon status and stdout/stderr with search, filtering
and selection-preserving refresh. Execution preparation, scenario editing,
export and reconciliation remain available through curses and CLI.

After publication, install with `python -m pip install "auton[textual]==1.3.0"`
and select it with `auton --tui --tui-ui textual --uri https://daemon.example.org`.

No configuration or stored-data migration is required. The offline demo uses
explicitly labelled synthetic fixtures and connects to no service. See the
[Textual guide](textual.md) for controls and limitations.
