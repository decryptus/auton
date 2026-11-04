import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from auton.classes.migration import migration_findings

ROOT = Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def test_shipped_examples(self):
        for name in ('auton.yml.example', 'docker.yml.example'):
            self.assertEqual(migration_findings(ROOT / 'etc/auton' / name), [])

    def test_cli_reports_findings_and_never_executes_component_templates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'daemon.yml'
            marker = root / 'executed'
            (root / 'component').write_text('<% open(' + repr(str(marker)) + ', "w").close() %>')
            entries = {'work': {'plugin': 'subproc', 'import_config': 'component'}}
            catalogue = root / 'endpoints.yml'
            catalogue.write_text(yaml.safe_dump(entries))
            config = {'general': {}, 'import_endpoints': 'endpoints.yml'}

            def run():
                path.write_text(yaml.safe_dump(config))
                return subprocess.run([sys.executable, str(ROOT / 'bin/autond'), '--check-migration',
                                       '-c', str(path)], cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                      capture_output=True, text=True, timeout=10)

            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('AUTON_MIGRATION_AUTH_DEFAULT', result.stdout)
            self.assertIn("REVIEW 'work'", result.stdout)
            config['general']['auth_mode'] = 'required'
            entries['work']['config'] = {'disallow-envfiles': True, 'envfiles': ['private-value']}
            catalogue.write_text(yaml.safe_dump(entries))
            self.assertEqual(run().returncode, 0)
            entries['work']['config']['disallow-envfiles'] = False
            catalogue.write_text(yaml.safe_dump(entries))
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('AUTON_MIGRATION_ENVFILES_ALLOWED', result.stdout)
            self.assertNotIn('private-value', result.stdout + result.stderr)
            self.assertFalse(marker.exists())
            config['import_endpoints'] = 'missing-private-path'
            result = run()
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('missing-private-path', result.stdout + result.stderr)
