import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/select-release.sh'


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-b', 'master')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')
        self.commit('0.3.0')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self, version):
        for name in ('VERSION', 'RELEASE'):
            (self.root / name).write_text(version + '\n')
        self.git('add', '.')
        self.git('commit', '--allow-empty', '-m', 'test')

    def select(self, ref='refs/heads/master', event='push', tag='', success=True):
        output = self.root / 'output'
        output.unlink(missing_ok=True)
        result = subprocess.run(['bash', str(SCRIPT)], cwd=self.root, capture_output=True,
                                env=dict(os.environ, GITHUB_REF=ref, GITHUB_EVENT_NAME=event,
                                         RELEASE_TAG=tag, GITHUB_OUTPUT=str(output)))
        self.assertEqual(result.returncode == 0, success, result.stderr)
        return dict(line.split('=', 1) for line in output.read_text().splitlines()) if output.exists() else {}

    def test_new_version(self):
        self.assertEqual(self.select()['create'], 'true')

    def test_existing_tag_retry_and_later_commit(self):
        self.git('tag', 'v0.3.0')
        self.assertEqual(self.select()['publish'], 'true')
        self.git('commit', '--allow-empty', '-m', 'ordinary change')
        self.assertEqual(self.select()['publish'], 'false')

    def test_manual_uses_tagged_commit(self):
        sha = self.git('rev-parse', 'HEAD')
        self.git('tag', 'v0.3.0')
        self.commit('0.4.0')
        result = self.select(event='workflow_dispatch', tag='v0.3.0')
        self.assertEqual(result['sha'], sha)
        self.assertEqual(result['publish'], 'true')
        self.assertEqual(result['create'], 'false')

    def test_invalid_manual_and_mismatched_versions(self):
        self.select(event='workflow_dispatch', tag='--bad', success=False)
        self.select(event='workflow_dispatch', tag='v9.0.0', success=False)
        (self.root / 'RELEASE').write_text('0.4.0')
        self.select(success=False)

    def test_pull_request_and_wrong_tag(self):
        self.assertEqual(self.select(ref='refs/pull/2/merge', event='pull_request')['publish'], 'false')
        self.select(ref='refs/tags/v0.4.0', success=False)

    def test_unrelated_tag_rejected(self):
        self.git('checkout', '--orphan', 'other')
        self.git('commit', '-m', 'unrelated')
        self.git('tag', 'v0.3.0')
        self.git('checkout', 'master')
        self.select(success=False)
        self.select(event='workflow_dispatch', tag='v0.3.0', success=False)
