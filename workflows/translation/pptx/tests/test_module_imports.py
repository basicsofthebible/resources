"""Cold module entry points must not depend on notebook import order or clients."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

IMPORT_CHECK = r'''
import importlib
import importlib.abc
import json
import os
from pathlib import Path
import sys

class BlockProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'openai', 'anthropic', 'google', 'dotenv'}:
            raise AssertionError('Import-time provider/credential loader: ' + fullname)

def audit(event, args):
    if event == 'open':
        mode, flags = args[1:3]
        if set(mode or '') & set('wax+') or (flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('Import-time file write: ' + str(args[0]))
    elif event in {'os.mkdir', 'os.remove', 'os.rmdir', 'os.rename', 'os.link', 'os.symlink', 'os.chmod', 'os.utime',
                   'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Import-time mutation/network: ' + event)

sys.meta_path.insert(0, BlockProviders())
sys.addaudithook(audit)
names = json.loads(sys.argv[1])
root = Path(sys.argv[2]).resolve()
package = importlib.import_module('pptx_translation')
assert not any(name.startswith('pptx_translation.') for name in sys.modules), 'Eager package initialization'
modules = [importlib.import_module(name) for name in names]
for name, module in zip(names, modules):
    relative = ('tests/support/offline_mock_translation.py' if name == 'support.offline_mock_translation'
                else name.replace('.', '/') + '.py')
    assert Path(module.__file__).resolve() == root / relative, name
    assert sys.modules[name] is module
    assert name.rsplit('.', 1)[-1] not in sys.modules, 'Duplicate bare production module: ' + name
assert len({id(module) for module in modules}) == len(names)
assert not list(Path.cwd().iterdir()), 'Import produced artifacts'
print(json.dumps(names))
'''


class ModuleImportTests(unittest.TestCase):
    def test_every_production_module_can_be_imported_first_without_writes_or_providers(self):
        names = sorted('pptx_translation.' + path.stem for path in (ROOT / 'pptx_translation').glob('*.py')
                       if path.name != '__init__.py') + ['support.offline_mock_translation']
        self.assertEqual(len(names), 21)  # Preserve all 21 pre-migration entry orders.
        self.assertFalse(list(ROOT.glob('*.py')))
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / 'tests'))), PYTHONDONTWRITEBYTECODE='1')
        with tempfile.TemporaryDirectory() as folder:
            for first in names:
                with self.subTest(first=first):
                    order = [first] + [name for name in reversed(names) if name != first]
                    completed = subprocess.run(
                        [sys.executable, '-B', '-c', IMPORT_CHECK, json.dumps(order), str(ROOT)],
                        cwd=folder, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(json.loads(completed.stdout), order)
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
