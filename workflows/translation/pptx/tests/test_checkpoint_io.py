"""Frozen serialization vectors, atomic failure boundaries, and legacy hooks."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import checkpoint_io as cio
from pptx_translation import primary_translation as pt
from pptx_translation import fallback_checkpoint as fc

GOLDEN = json.loads((ROOT / 'tests/fixtures/checkpoint_io_golden.json').read_text(encoding='utf-8'))


class CheckpointIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'checkpoint.json'

    def test_utc_timestamp_and_primary_wrapper(self):
        fixed = datetime(2026, 10, 28, 12, 34, 56, 789012, tzinfo=timezone.utc)
        class FrozenDatetime:
            @staticmethod
            def now(tz):
                if tz is not timezone.utc:
                    raise AssertionError('Clock must use explicit UTC.')
                return fixed
        with patch.object(cio, 'datetime', FrozenDatetime):
            self.assertEqual(cio.now(), GOLDEN['utc_example'])
            self.assertEqual(pt._now(), GOLDEN['utc_example'])

    def test_canonical_digest_frozen_vectors_and_key_order(self):
        for row in GOLDEN['vectors']:
            with self.subTest(vector=row['name']):
                self.assertEqual(cio.digest(row['state']), row['digest'])
                self.assertEqual(pt._digest(row['state']), row['digest'])
        by_name = {row['name']: row for row in GOLDEN['vectors']}
        self.assertEqual(by_name['key_order_a']['digest'], by_name['key_order_b']['digest'])
        self.assertNotEqual(by_name['key_order_a']['checkpoint_text'], by_name['key_order_b']['checkpoint_text'])

    def test_file_hash_raw_bytes_path_and_string_inputs(self):
        row = GOLDEN['binary_file']
        self.path.write_bytes(bytes.fromhex(row['hex']))
        for reader in (cio.file_hash, pt._file_hash):
            self.assertEqual(reader(self.path), row['sha256'])
            self.assertEqual(reader(str(self.path)), row['sha256'])
            with self.assertRaises(FileNotFoundError):
                reader(self.root / 'missing.json')

    def test_exact_checkpoint_bytes_and_input_immutability(self):
        for writer in (cio.write_checkpoint, pt._write_checkpoint):
            for row in GOLDEN['vectors']:
                with self.subTest(writer=writer.__name__, vector=row['name']):
                    state = deepcopy(row['state'])
                    self.assertIsNone(writer(self.path, state))
                    # The original text stream uses native newline translation.
                    expected = row['checkpoint_text'].replace('\n', os.linesep).encode('utf-8')
                    self.assertEqual(self.path.read_bytes(), expected)
                    self.assertEqual(state, row['state'])
                    self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['integrity'], row['digest'])
                    self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_nonfinite_and_mixed_key_failures_leave_existing_file_untouched(self):
        self.path.write_bytes(b'existing checkpoint')
        for value in (float('nan'), float('inf'), float('-inf')):
            for writer in (cio.write_checkpoint, pt._write_checkpoint):
                with self.subTest(value=value, writer=writer.__name__):
                    with self.assertRaises(ValueError):
                        writer(self.path, {'bad': value})
                    self.assertEqual(self.path.read_bytes(), b'existing checkpoint')
                    self.assertEqual(list(self.root.iterdir()), [self.path])
        for digest in (cio.digest, pt._digest):
            with self.assertRaises(TypeError):
                digest({1: 'numeric key', 'a': 'text key'})

    def test_failed_fsync_and_replace_preserve_target_and_clean_temporary_file(self):
        self.assertIs(pt.os, cio.os)
        for operation in ('fsync', 'replace'):
            for writer in (cio.write_checkpoint, pt._write_checkpoint):
                with self.subTest(operation=operation, writer=writer.__name__):
                    self.path.write_bytes(b'existing checkpoint')
                    with patch.object(pt.os, operation, side_effect=OSError('injected persistence failure')):
                        with self.assertRaises(OSError):
                            writer(self.path, {'new': 'value'})
                    self.assertEqual(self.path.read_bytes(), b'existing checkpoint')
                    self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_success_fsync_precedes_same_directory_atomic_replace(self):
        self.path.write_bytes(b'old checkpoint')
        events = []
        fsync, replace = cio.os.fsync, cio.os.replace
        def synced(fd):
            events.append('fsync')
            return fsync(fd)
        def published(source, destination):
            source = Path(source)
            self.assertEqual(events, ['fsync'])
            self.assertEqual(source.parent, self.path.parent)
            self.assertTrue(source.name.startswith('checkpoint.json.'))
            self.assertEqual(source.suffix, '.tmp')
            self.assertEqual(self.path.read_bytes(), b'old checkpoint')
            self.assertEqual(json.loads(source.read_text(encoding='utf-8'))['value'], 'ready')
            events.append('replace')
            return replace(source, destination)
        with patch.object(cio.os, 'fsync', side_effect=synced), patch.object(cio.os, 'replace', side_effect=published):
            cio.write_checkpoint(self.path, {'value': 'ready'})
        self.assertEqual(events, ['fsync', 'replace'])
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_legacy_digest_writer_and_tempfile_patch_points(self):
        with patch.object(pt, '_digest', return_value='legacy-digest-hook') as digest:
            pt._write_checkpoint(self.path, {'value': 1})
            digest.assert_called_once_with({'value': 1})
        self.assertEqual(json.loads(self.path.read_text())['integrity'], 'legacy-digest-hook')
        with patch.object(pt, '_write_checkpoint', side_effect=OSError('stage hook')) as writer:
            with self.assertRaises(OSError):
                fc.save(self.root / 'fallback.json', {'historical_batches': [], 'value': 2})
            writer.assert_called_once_with(self.root / 'fallback.json', {'value': 2})
        self.assertIs(pt.tempfile, cio.tempfile)
        with patch.object(pt.tempfile, 'mkstemp', side_effect=OSError('temporary allocation hook')):
            with self.assertRaises(OSError):
                pt._write_checkpoint(self.path, {'value': 3})
        self.assertEqual(json.loads(self.path.read_text())['integrity'], 'legacy-digest-hook')

    def test_saved_checkpoint_byte_replay_and_source_hash_ancestry(self):
        source = self.root / 'source.pptx'
        source.write_bytes(b'offline source identity')
        units = [dict(unit_key='u1', text='Hello', action='translate', preserve_source=False,
                      source_type='text_frame_paragraph', slide_number=1)]
        # Planning only: a client is deliberately absent and no request is allowed.
        pt.run_primary_translation(None, units, source, 'English', 'Spanish', 'offline',
                                   'Translate', self.path, max_batches=0)
        state, candidates = pt.read_validated_candidates(self.path, source)
        self.assertEqual(candidates, {})
        self.assertEqual(state['compatibility']['source_sha256'], cio.file_hash(source))
        self.assertEqual(state['compatibility']['units_sha256'], cio.digest(units))
        child = self.root / 'copied.json'
        cio.write_checkpoint(child, state)
        self.assertEqual(child.read_bytes(), self.path.read_bytes())
        self.assertEqual(pt.read_primary_state(child), state)
        source.write_bytes(b'changed temporary source identity')
        with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
            pt.read_validated_candidates(child, source)

    def test_neutral_import_has_no_workflow_provider_or_write_side_effects(self):
        blocked = sorted({'pptx_translation.' + file.stem for file in (ROOT / 'pptx_translation').glob('*.py')
                          if file.stem not in ('__init__', 'checkpoint_io')})
        code = """import sys, importlib.abc, os
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname in BLOCKED or fullname.split('.')[0] in BLOCKED:
   raise AssertionError('Unexpected import: '+fullname)
def no_writes(event,args):
 if event=='open' and (set(args[1] or '') & set('wax+') or (args[2] or 0) & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
  raise AssertionError('Import attempted a write')
 if event in ('os.mkdir','os.remove','os.rename','os.rmdir','socket.connect','socket.getaddrinfo'):
  raise AssertionError(event)
sys.meta_path.insert(0,Block())
sys.addaudithook(no_writes)
from pptx_translation import checkpoint_io
assert 'pptx_translation.primary_translation' not in sys.modules
assert not list(Path.cwd().iterdir())
"""
        code = 'BLOCKED=' + repr(blocked + ['google', 'openai', 'anthropic', 'dotenv', 'pptx']) + '\n' + code
        with tempfile.TemporaryDirectory() as empty:
            env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
            subprocess.run([sys.executable, '-B', '-c', code], cwd=empty, env=env,
                           capture_output=True, text=True, check=True)


if __name__ == '__main__':
    unittest.main()
