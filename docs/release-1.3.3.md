# Auton 1.3.3

This maintenance release shares terminal drawing, interactive-terminal checks and
CLI JSON output with DWho. Remote-text sanitization, navigation, credentials,
confirmation and execution semantics remain owned by Auton.

Both terminal renderer choices reject redirected input/output before loading the
interactive implementation or credentials. The curses view preserves selection
styling and tolerates terminal resizing. The optional Textual interface remains
available; ordinary CLI commands do not import curses or Textual.

The base client now declares `dwho>=0.3.63`, including DWho's normal transitive
dependencies. The optional Textual extra still requires `dwho[textual]>=0.3.66`.
Only terminal helpers are imported by presentation; client application services
remain independent of daemon and presentation modules. CLI/daemon executable
version identities now match the package version instead of retaining 1.2.0.

Both `auton` and `autond` use version 1.3.3. Upgrade the client with:

```sh
python -m pip install --upgrade 'auton==1.3.3'
# Optional Textual interface:
python -m pip install --upgrade 'auton[textual]==1.3.3'
```

The daemon image is `decryptus/auton:1.3.3`. Preserve configuration, credentials
and durable volumes and use the existing controlled restart procedure when
upgrading a daemon. No configuration or storage migration is required from 1.3.2.
The release does not deploy to existing servers automatically.

Python 3.10–3.13 remain supported. The existing OIDC deadline, authorization,
unknown-outcome handling and refusal to replay ambiguous submissions are unchanged.
