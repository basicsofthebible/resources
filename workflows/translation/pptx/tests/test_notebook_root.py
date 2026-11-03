"""Notebook root discovery only; no stage cells, imports or provider calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CELL = ''.join(json.loads((ROOT / 'pptx_workflow.ipynb').read_text(encoding='utf-8'))['cells'][2]['source'])
DISCOVERY = CELL.split('sys.path.insert(0, str(project_root))')[0]


class NotebookRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.checkout = self.make_checkout('PPTX Translation Workflow')

    def make_checkout(self, name):
        root = self.parent / name
        marker = root / 'pptx_translation' / 'workflow_config.py'
        marker.parent.mkdir(parents=True)
        marker.touch()
        return root.resolve()

    def discover(self, cwd, **environment):
        scope = {}
        with patch.dict(os.environ, environment, clear=True), patch.object(Path, 'cwd', return_value=cwd):
            exec(compile(DISCOVERY, '<notebook-root-only>', 'exec'), scope)
        return scope['project_root']

    def test_renamed_checkout_and_its_parent(self):
        self.assertEqual(self.discover(self.checkout), self.checkout)
        self.assertEqual(self.discover(self.parent), self.checkout)

    def test_original_directory_name_still_supported(self):
        self.checkout.rename(self.parent / 'pptx_workflow_v3')
        expected = (self.parent / 'pptx_workflow_v3').resolve()
        self.assertEqual(self.discover(expected), expected)
        self.assertEqual(self.discover(self.parent), expected)

    def test_explicit_roots_from_elsewhere_and_new_name_precedence(self):
        elsewhere = self.parent / 'elsewhere'
        elsewhere.mkdir()
        for variable in ('PPTX_WORKFLOW_ROOT', 'PPTX_V3_ROOT'):
            self.assertEqual(self.discover(elsewhere, **{variable: str(self.checkout)}), self.checkout)
        self.assertEqual(self.discover(elsewhere, PPTX_WORKFLOW_ROOT=str(self.checkout),
                                       PPTX_V3_ROOT=str(elsewhere)), self.checkout)

    def test_explicit_relative_root(self):
        previous = Path.cwd()
        try:
            os.chdir(self.parent)
            self.assertEqual(self.discover(self.parent, PPTX_WORKFLOW_ROOT=self.checkout.name), self.checkout)
        finally:
            os.chdir(previous)

    def test_invalid_explicit_root_does_not_fall_back(self):
        with self.assertRaisesRegex(RuntimeError, 'containing pptx_translation'):
            self.discover(self.checkout, PPTX_WORKFLOW_ROOT=str(self.parent))

    def test_missing_and_ambiguous_checkouts_require_explicit_selection(self):
        elsewhere = self.parent / 'empty'
        elsewhere.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'no unique checkout'):
            self.discover(elsewhere)
        self.make_checkout('another-checkout')
        with self.assertRaisesRegex(RuntimeError, 'no unique checkout'):
            self.discover(self.parent)
        self.assertEqual(self.discover(self.checkout), self.checkout)


if __name__ == '__main__':
    unittest.main()
