# Auton 2.0.0rc6

Restrict caller input in the shipped subprocess examples.

The curl, ansible-playbook and terraform examples run a fixed version check. They reject client arguments, environment variables and uploaded or server-side files. To enable the selective report example, install your own /opt/scripts/report.sh and uncomment the report endpoint. Existing operator configurations retain their settings. See the [migration guide](release-2.0.0rc3.md) for isolated testing.
