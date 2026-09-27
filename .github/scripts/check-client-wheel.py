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
    subprocess.run([python, '-I', '-c', '''
import importlib.abc
import sys
class BlockDaemon(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('auton', 'dwho', 'httpdis'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockDaemon())
from auton_client import RemoteClient
assert RemoteClient(['http://localhost'], 'example', 'one').output_offset == 0
'''], cwd=directory, check=True)
    subprocess.run([python, '-I', str(Path(directory) / 'bin/auton'), '--help'],
                   cwd=directory, check=True, stdout=subprocess.DEVNULL)
