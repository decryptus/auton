import subprocess
import sys
import unittest


class PreparationIsolationTests(unittest.TestCase):
    def test_preparation_service_does_not_load_terminal_interfaces(self):
        script = '''import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('curses', 'textual', 'auton_client.preparation', 'auton_client.tui'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from auton_client.preparation_model import PreparationModel
from unittest.mock import Mock
model = PreparationModel({'one': 'http://one'}, {}, {}, {}, Mock(), {})
model.selected[0] = ['one']; model.selected[4] = ['echo']
model.prepare()
assert model.mode == 'confirm'
model.session.start.assert_not_called()
'''
        subprocess.run([sys.executable, '-c', script], check=True)
