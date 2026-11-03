"""Inspect notebook controls without executing any notebook code."""
import ast
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NotebookStaticTests(unittest.TestCase):
    def test_public_input_disabled_flags_and_unexecuted_cells(self):
        notebook = json.loads((ROOT/'pptx_workflow.ipynb').read_text(encoding='utf-8'))
        self.assertEqual(notebook['nbformat'], 4)
        flags = {'enable_primary_translation','enable_evaluation','enable_fallback_translation',
                 'enable_fallback_evaluation','enable_finalization','enable_reconstruction'}
        seen = set()
        inputs = []
        outputs = []
        manifest_defaults = []
        for cell in notebook['cells']:
            if cell['cell_type'] != 'code': continue
            self.assertIsNone(cell['execution_count'])
            self.assertEqual(cell['outputs'], [])
            tree = ast.parse(''.join(cell['source']))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, 'dotenv')
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id == 'run_manifest_path' and isinstance(node.value, ast.Constant):
                            manifest_defaults.append(node.value.value)
                        if isinstance(target, ast.Name) and target.id in flags:
                            self.assertIsInstance(node.value, ast.Constant)
                            self.assertIs(node.value.value, False)
                            seen.add(target.id)
                if isinstance(node, ast.keyword) and node.arg == 'input_path':
                    inputs.append(ast.dump(node.value, include_attributes=False))
                if isinstance(node, ast.keyword) and node.arg == 'output_root':
                    outputs.append(ast.dump(node.value, include_attributes=False))
                if isinstance(node, ast.keyword) and node.arg == 'write_reports':
                    self.assertIsInstance(node.value, ast.Constant)
                    self.assertIs(node.value.value, False)
        self.assertEqual(seen, flags)
        self.assertEqual(inputs, [ast.dump(ast.parse('project_root / "pptx_files" / "Help_I_Dont_Understand_The_Bible.pptx"',mode='eval').body,include_attributes=False)])
        self.assertEqual(outputs, [ast.dump(ast.parse('project_root / "outputs"', mode='eval').body, include_attributes=False)])
        self.assertEqual(manifest_defaults, [None])
        self.assertTrue((ROOT / 'pptx_files' / 'Help_I_Dont_Understand_The_Bible.pptx').is_file())
        self.assertTrue((ROOT / 'examples' / 'stage1_sample.pptx').is_file())


if __name__ == '__main__':
    unittest.main()
