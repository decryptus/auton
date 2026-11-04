# Auton 1.3.6

This release adds daemon log warnings to help operators prepare for 2.0. Existing 1.x configurations retain their authentication and file access behavior.

## Migration warnings

`AUTON_MIGRATION_AUTH_DEFAULT` appears at startup when auth_mode is missing. Check your credentials and test required authentication before upgrading to 2.0. Explicit legacy mode retains its existing route-security warning.

`AUTON_MIGRATION_ENVFILES_ALLOWED` identifies a subprocess endpoint that permits clients to select environment files on the server. It appears at initialization even if no client has used this feature.

`AUTON_MIGRATION_ENVFILES_USED` identifies the first job on each endpoint that supplies client environment files, once per daemon start. The warning includes the endpoint name and omits file paths and contents. Monitor a representative operating period before changing clients.

Move fixed paths into the endpoint configuration and test the job. Once clients stop sending envfiles, enable disallow-envfiles on the endpoint. The daemon can still read its configured files. The shipped example now sets this flag for new subprocess endpoints.
