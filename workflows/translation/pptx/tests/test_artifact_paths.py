"""Recorded-path precedence, exact hashes, legacy hooks, and virtual layouts."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from pptx_translation import artifact_paths as ap
from pptx_translation import checkpoint_io as cio
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import primary_translation as pt
from pptx_translation import primary_recovery as pr
from pptx_translation import fallback_recovery as recovery
from pptx_translation import fallback_evaluation as fe
from pptx_translation import run_reporting as reporting
from pptx_translation import workflow_config as config


class ArtifactPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, data=b'exact artifact'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    @contextmanager
    def cwd(self, path):
        old = Path.cwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    def test_roots_are_module_anchored_flat_or_one_package_level(self):
        with self.cwd(self.root), patch.dict(os.environ, {
            'PPTX_V3_ROOT': str(ROOT.parent / 'pptx_workflow_v2'),
            'PPTX_V2_ROOT': str(ROOT.parent / 'pptx_workflow_v2'),
        }):
            self.assertEqual(ap.project_root(), ROOT)
            self.assertEqual(ap.project_root(ROOT / 'workflow_config.py'), ROOT)
            self.assertEqual(ap.project_root(ROOT / 'pptx_translation/workflow_config.py'), ROOT)
            self.assertEqual(ap.project_root(ROOT / 'pptx_translation/artifact_paths.py'), ROOT)
            # No broad upward search for an arbitrary enclosing folder.
            self.assertEqual(ap.project_root(self.root / 'other/module.py'), self.root / 'other')
            self.assertEqual(config.PROJECT_ROOT, ROOT)
        self.assertEqual(Path(ap.__file__).resolve(), ROOT / 'pptx_translation/artifact_paths.py')
        self.assertFalse((ROOT / 'artifact_paths.py').exists())

    def test_workflow_config_defaults_in_virtual_flat_and_package_layouts(self):
        source = (ROOT / 'pptx_translation/workflow_config.py').read_text(encoding='utf-8')
        for index, anchor in enumerate((ROOT / 'workflow_config.py', ROOT / 'pptx_translation/workflow_config.py')):
            module = ModuleType('stage6_config_probe_' + str(index))
            module.__file__ = str(anchor)
            module.__package__ = 'pptx_translation'
            with patch.dict(sys.modules, {module.__name__: module}):
                exec(compile(source, '<stage6-config-layout-only>', 'exec'), module.__dict__)
                value = module.WorkflowConfig(input_path='pptx_files/example.pptx')
                self.assertEqual(module.PROJECT_ROOT, ROOT)
                self.assertEqual(value.input_path, ROOT / 'pptx_files/example.pptx')
                self.assertEqual(value.output_root, ROOT / 'outputs')
                self.assertEqual(module.WorkflowConfig().input_path, ROOT / 'examples/stage1_sample.pptx')
                self.assertFalse(value.enable_paid_api)

    def test_absolute_relative_and_project_relative_inputs(self):
        path = self.write('data/artifact.json')
        record = dict(path=str(path), sha256=cio.file_hash(path))
        self.assertEqual(ap.resolve_input(record), path.resolve())
        self.assertEqual(fr._resolve_input(record), path.resolve())
        with self.cwd(self.root):
            relative = dict(record, path='data/artifact.json')
            self.assertEqual(ap.resolve_input(relative), path.resolve())
        working = self.root / 'working'
        working.mkdir()
        with self.cwd(working):
            self.assertEqual(ap.resolve_input(dict(record, path='data/artifact.json'),
                                             module_file=self.root / 'artifact_paths.py'), path.resolve())

    def test_windows_wsl_and_forward_slash_anchors_flat_and_package(self):
        for anchor in ('outputs', 'pptx_files', 'examples'):
            path = self.write(anchor + '/artifact.dat')
            forms = [f'D:\\__stage6_missing_{self.root.name}\\{anchor}\\artifact.dat',
                     f'/mnt/d/__stage6_missing_{self.root.name}/{anchor}/artifact.dat',
                     f'D:/__stage6_missing_{self.root.name}/{anchor}/artifact.dat']
            for filename in (self.root / 'finalized_reconstruction.py',
                             self.root / 'pptx_translation/finalized_reconstruction.py'):
                for recorded in forms:
                    with self.subTest(anchor=anchor, filename=str(filename), recorded=recorded):
                        record = dict(path=recorded, sha256=cio.file_hash(path))
                        self.assertEqual(ap.resolve_input(record, module_file=filename), path.resolve())
                        with patch.object(fr, '__file__', str(filename)):
                            self.assertEqual(fr._resolve_input(record), path.resolve())

    def test_candidate_order_retains_all_anchors_and_duplicate_checks(self):
        recorded = 'outputs/pptx_files/examples/artifact.json'
        project = self.root / 'project'
        seen = []
        def absent(path):
            seen.append(path)
            return False
        with patch.object(Path, 'is_file', absent):
            with self.assertRaisesRegex(ValueError, 'Missing or changed finalization input:'):
                ap.resolve_input(dict(path=recorded), module_file=project / 'artifact_paths.py')
        self.assertEqual(seen, [Path(recorded), project / recorded,
                                project / 'pptx_files/examples/artifact.json',
                                project / 'examples/artifact.json', project / recorded])

    def test_cwd_candidate_precedes_anchor_and_wrong_hash_falls_through(self):
        working = self.write('working/outputs/artifact.json')
        anchored = self.write('project/outputs/artifact.json')
        record = dict(path='outputs/artifact.json', sha256=cio.file_hash(anchored))
        with self.cwd(self.root / 'working'):
            self.assertEqual(ap.resolve_input(record, module_file=self.root / 'project/artifact_paths.py'), working.resolve())
            working.write_bytes(b'wrong candidate')
            self.assertEqual(ap.resolve_input(record, module_file=self.root / 'project/artifact_paths.py'), anchored.resolve())

    def test_override_is_the_only_candidate_and_does_not_discover_roots(self):
        path = self.write('override.json')
        record = dict(path='outputs/recorded.json', sha256=cio.file_hash(path))
        with patch.object(ap, 'project_root', side_effect=AssertionError('Unexpected fallback')):
            self.assertEqual(ap.resolve_input(record, path), path.resolve())
            with self.assertRaisesRegex(ValueError, '^Missing or changed finalization input: outputs/recorded.json$'):
                ap.resolve_input(dict(record, sha256='wrong'), path)

    def test_errors_hash_requirements_and_no_newest_file_discovery(self):
        path = self.write('examples/newest.json')
        missing = str(self.root / 'examples/missing.json')
        with self.assertRaisesRegex(ValueError, 'Missing or changed finalization input:'):
            ap.resolve_input(dict(path=missing, sha256=cio.file_hash(path)), module_file=self.root / 'artifact_paths.py')
        with self.assertRaises(KeyError):
            ap.resolve_input({})
        with self.assertRaises(KeyError):
            ap.resolve_input(dict(path=str(path)))
        with self.assertRaises(TypeError):
            ap.resolve_input(dict(path=path, sha256=cio.file_hash(path)))
        with patch.object(cio, 'file_hash', side_effect=OSError('unreadable artifact')):
            with self.assertRaisesRegex(OSError, 'unreadable artifact'):
                ap.resolve_input(dict(path=str(path), sha256='irrelevant'))

    def test_reconstruction_wrapper_preserves_path_and_hash_patch_hooks(self):
        path = self.write('artifact.json')
        record = dict(path=str(path), sha256=cio.file_hash(path))
        with patch.object(fr, 'Path', side_effect=Path) as factory, \
             patch.object(pt, '_file_hash', wraps=cio.file_hash) as reader:
            self.assertEqual(fr._resolve_input(record), path.resolve())
            factory.assert_called()
            reader.assert_called_once_with(path)
        with patch.object(pt, '_file_hash', side_effect=OSError('legacy hash hook')):
            with self.assertRaisesRegex(OSError, 'legacy hash hook'):
                fr._resolve_input(record)


    def test_recovery_resolution_no_longer_imports_reconstruction(self):
        path = self.write('ancestor.json')
        record = dict(path=str(path), sha256=cio.file_hash(path))
        with patch.dict(sys.modules, {'pptx_translation.finalized_reconstruction': None}):
            self.assertEqual(pr._base(record), path.resolve())
            self.assertEqual(recovery.resolve(record), path.resolve())


    def test_evaluator_roots_and_relative_parent_precedence(self):
        anchored = self.write('outputs/evaluation.json')
        c = dict(evaluation_sha256=cio.file_hash(anchored), primary_sha256='primary', source_sha256='source')
        evaluator = dict(compatibility=dict(primary_sha256='primary', source_sha256='source', model='verified-model'))
        for module_file in (self.root / 'fallback_evaluation.py', self.root / 'pptx_translation/fallback_evaluation.py'):
            state = dict(provenance=dict(evaluation_checkpoint=f'D:\\__stage6_missing_{self.root.name}\\outputs\\evaluation.json'), compatibility=c)
            with patch.object(fe, '__file__', str(module_file)), patch.object(fe.ft, '_read', return_value=state), \
                 patch.object(fe.ev, '_read', return_value=evaluator) as read:
                self.assertEqual(fe.resolve_evaluation_model(self.root / 'fallback.json'), 'verified-model')
                read.assert_called_once_with(anchored)
        near = self.write('branch/evaluation.json')
        self.write('evaluation.json')
        state = dict(provenance=dict(evaluation_checkpoint='evaluation.json'), compatibility=c)
        with self.cwd(self.root / 'outputs'), patch.object(fe, '__file__', str(self.root / 'pptx_translation/fallback_evaluation.py')), \
             patch.object(fe.ft, '_read', return_value=state), patch.object(fe.ev, '_read', return_value=evaluator) as read:
            # There is a CWD candidate here: it must still precede fallback.parent.
            self.assertEqual(fe.resolve_evaluation_model(self.root / 'branch/fallback.json'), 'verified-model')
            read.assert_called_once_with(Path('evaluation.json'))
        with self.cwd(self.root / 'branch'), patch.object(fe, '__file__', str(self.root / 'pptx_translation/fallback_evaluation.py')), \
             patch.object(fe.ft, '_read', return_value=state), patch.object(fe.ev, '_read', return_value=evaluator):
            self.assertEqual(fe.resolve_evaluation_model(self.root / 'branch/fallback.json'), 'verified-model')
        with self.cwd(self.root), patch.object(fe, '__file__', str(self.root / 'pptx_translation/fallback_evaluation.py')), \
             patch.object(fe.ft, '_read', return_value=state), patch.object(fe.ev, '_read', return_value=evaluator) as read:
            # Make the direct CWD candidate ineligible; fallback.parent comes next.
            (self.root / 'evaluation.json').write_bytes(b'wrong hash')
            self.assertEqual(fe.resolve_evaluation_model(self.root / 'branch/fallback.json'), 'verified-model')
            read.assert_called_once_with(near)


    def test_neutral_import_has_no_stage_provider_or_write_side_effects(self):
        blocked = sorted({'pptx_translation.' + file.stem for file in (ROOT / 'pptx_translation').glob('*.py')
                          if file.stem not in ('__init__', 'artifact_paths', 'checkpoint_io')})
        code = """import sys, importlib.abc, os
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname in BLOCKED or fullname.split('.')[0] in BLOCKED:
   raise AssertionError(fullname)
def no_writes(event,args):
 if event=='open' and (set(args[1] or '') & set('wax+') or (args[2] or 0) & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
  raise AssertionError('Import attempted a write')
 if event in ('os.mkdir','os.remove','os.rename','os.rmdir','socket.connect','socket.getaddrinfo'):
  raise AssertionError(event)
sys.meta_path.insert(0,Block())
sys.addaudithook(no_writes)
from pptx_translation import artifact_paths
assert artifact_paths.project_root()==EXPECTED_ROOT
assert 'pptx_translation.finalized_reconstruction' not in sys.modules
assert 'pptx_translation.primary_translation' not in sys.modules
assert not list(Path.cwd().iterdir())
"""
        code = 'BLOCKED=' + repr(blocked + ['openai', 'anthropic', 'google', 'dotenv', 'pptx']) + '\n' + code.replace('EXPECTED_ROOT', 'Path(' + repr(str(ROOT)) + ')')
        with tempfile.TemporaryDirectory() as empty:
            env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
            subprocess.run([sys.executable, '-B', '-c', code], cwd=empty, env=env,
                           capture_output=True, text=True, check=True)


if __name__ == '__main__':
    unittest.main()
