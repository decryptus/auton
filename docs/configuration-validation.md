# Configuration validation

The daemon validates the main document before DWho normalization and checks the
combined endpoint catalogue after imports. Endpoint plugin names are nonempty
strings; config, users, vars and discovery are mappings. Component import paths
are strings (or null for an unused optional import). Legacy Mako component files
must render to mappings, matching the existing catalogue component contract.
Imported catalogue limits, declaring-file paths, inline precedence, discovery
rules, authentication and job limits remain enforced by their existing owners.
Unknown plugin configuration fields are preserved. The separate client targets
and scenarios loader is unchanged.

XYS (Sonicprobe >= 0.3.57) validates parsed Python data; it does not replace the
YAML loader, resolve imports, initialize services or grant permissions. Schemas
are compiled once. These schemas use no modifiers and do not silently convert
values. The existing numeric normalization and semantic checks still apply.

Known application fields are validated explicitly. Extension settings remain
available where the existing contract permits them; this is not universal typo
detection for plugin configuration. Malformed section/component shapes now fail
with a configuration error instead of incidental attribute/update exceptions.
New validation errors do not include configuration values or credential contents.

`tests/test_configuration_schema.py` covers valid/invalid shapes and compatibility
at the loader boundary. Run the collection guard before the unittest suite:

```sh
python .github/scripts/check-test-collection.py --runner unittest tests
python -m unittest discover -s tests -v
```

These tests use synthetic data and mocked adapters or loopback services. They do
not establish provider availability or production acceptance.
