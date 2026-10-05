"""Check the installed client outside the checkout, without daemon modules."""
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

wheels = list(Path('dist/client').glob('auton-*.whl'))
assert len(wheels) == 1, wheels
with tempfile.TemporaryDirectory() as directory:
    venv.create(directory, with_pip=True)
    python = str(Path(directory) / 'bin/python')
    subprocess.run([python, '-m', 'pip', 'install', str(wheels[0].resolve())],
                   cwd=directory, check=True)
    subprocess.run([python, '-I', '-c', r'''
import importlib.abc
import sys
class BlockDaemon(importlib.abc.MetaPathFinder):
    presentation = False
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('auton', 'httpdis'):
            raise AssertionError(fullname)
        if fullname.split('.')[0] == 'dwho' and not (
                self.presentation and fullname in ('dwho', 'dwho.cli', 'dwho.tui')):
            raise AssertionError(fullname)
boundary = BlockDaemon()
sys.meta_path.insert(0, boundary)
from auton_client import RemoteClient
from auton_client.credentials import BearerCredentials
assert 'a' * 43 not in repr(BearerCredentials('a' * 43))
assert RemoteClient(['http://localhost'], 'example', 'one').output_offset == 0
from auton_client.config import load_targets, load_inventory
from auton_client.connections import select_connections
from pathlib import Path
Path("origins.yml").write_text("local: http://localhost")
Path("targets.yml").write_text("import_targets: origins.yml\ngroups: {web: [local]}")
assert load_targets("targets.yml") == {"local": "http://localhost"}
inventory = load_inventory("targets.yml")
assert select_connections(["loc*"], ["~web$"], **dict(configured=inventory["targets"], groups=inventory["groups"])) == inventory["targets"]
from auton_client.operations import OperationService
assert OperationService({"local": "http://localhost"}, "example").parallel == 4
from auton_client.scenarios import ScenarioService, select_scenarios
Path("scenarios.yml").write_text("check: {version: 1, steps: [{name: check, endpoint: example}]}")
Path("targets.yml").write_text("targets: {local: http://localhost}\nimport_scenarios: scenarios.yml\nscenario_groups: {checks: [check]}")
inventory = load_inventory("targets.yml")
scenarios = select_scenarios([], ["ch*"], inventory["scenarios"], inventory["scenario_groups"])
assert len(ScenarioService(inventory["targets"], scenarios).steps) == 1
from auton_client.connections import resolve_targets
targets = resolve_targets({"one": "http://one", "deployment": {"uris": [{"target": "one"}, "http://two"]}})
assert OperationService({"deployment": targets["deployment"]}, "example").origins["deployment"] == ["http://one", "http://two"]
from auton_client.visibility import DaemonClient
from auton_client.monitor import FleetMonitor
boundary.presentation = True
from dwho.cli import require_terminal, write_json
from dwho.tui import put
assert 'curses' not in sys.modules
from auton_client.tui import OperatorView, daemon_specs
from auton_client.preparation import PreparationView
from auton_client.session import ExecutionSession
session = ExecutionSession()
panel = PreparationView(inventory['targets'], {}, scenarios, {}, session, {})
assert not any(panel.selected)
session.close()
client = DaemonClient('http://localhost')
monitor = FleetMonitor({'local': client})
assert OperatorView(monitor).daemon == 'local'
assert daemon_specs([], ['http://localhost']) == {'default': 'http://localhost'}
monitor.close()
'''], cwd=directory, check=True)
    subprocess.run([python, '-I', str(Path(directory) / 'bin/auton'), '--help'],
                   cwd=directory, check=True, stdout=subprocess.DEVNULL)
