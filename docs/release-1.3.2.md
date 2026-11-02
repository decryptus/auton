# Auton 1.3.2

OIDC token exchange now enforces the existing five-second budget across the
complete request, including response headers and body. A server that trickles
bytes can no longer extend authentication indefinitely by avoiding individual
socket timeouts. The bounded helper is terminated and reaped on expiry.

Certificate verification, response-size limits, denied/unavailable distinctions,
and the refusal to follow redirects or retry token exchanges are preserved.
Credentials are passed through the helper's private input, not command arguments.
Existing HTTP Basic authentication and job execution behavior are unchanged.

Both `auton` and `autond` are released at 1.3.2. Upgrade the client with
`python -m pip install --upgrade 'auton[textual]==1.3.2'` and use
`decryptus/auton:1.3.2` for the daemon image. Review your configuration and use
the existing explicit daemon restart procedure after an upgrade.
