"""Frozen evaluator wire-contract oracles, recorded requests and strict replay.

The independent serializer below predates consolidation. It intentionally does
not call the production builder or production digest to establish equivalence.
All clients are injected offline fakes; saved fixtures are read, never rewritten.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import evaluation as ev
from pptx_translation import fallback_evaluation as fe
from pptx_translation import fallback_translation as ft
from pptx_translation import finalization as fin
from pptx_translation import primary_translation as pt
import test_evaluation as fixtures



def legacy_messages(metadata, units, count, instruction, fallback=False):
    """Independent literal oracle for the pre-consolidation message bytes."""
    request = dict(metadata=dict(metadata, unit_count=count), units=units)
    prefix = (
        'Evaluate the Claude fallback PPTX translations in this JSON payload and return only the required JSON response object.\n\n'
        if fallback else
        'Evaluate the primary PPTX translations in this JSON payload and return only the required JSON response object.\n\n'
    )
    return [dict(role='system', content=instruction),
            dict(role='user', content=prefix + json.dumps(request, ensure_ascii=False, indent=2))]


def wire_hash(messages):
    return hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


@contextmanager
def pipeline():
    fixture = fixtures.EvaluationTests(methodName='runTest')
    try:
        fixture.setUp()
        yield fixture
        fixture.tearDown()
    finally:
        fixture.doCleanups()


class EvaluatorContractTests(unittest.TestCase):
    def test_builder_preserves_unicode_order_explicit_count_and_input_values(self):
        metadata = dict(target_language='中文', unit_count=999, source_language='Français',
                        validation_policy_version=4, empty=None)
        units = [dict(unit_key='b', source_text='é\n\t"quoted"\\text', primary_translation='神 – 😀',
                      translated_spans=[dict(span_id='p02', text='  中文 ')]),
                 dict(unit_key='a', source_text='1\u00a02', primary_translation='uno')]
        original = deepcopy((metadata, units))
        for fallback in (False, True):
            for count in (0, 7):
                messages = ev.build_evaluation_messages(metadata, units, count, 'Custom\n指示', fallback=fallback)
                expected = legacy_messages(metadata, units, count, 'Custom\n指示', fallback)
                self.assertEqual(messages, expected)
                self.assertEqual(messages[1]['content'].encode('utf-8'), expected[1]['content'].encode('utf-8'))
                self.assertEqual(pt._digest(messages), wire_hash(expected))
        self.assertEqual((metadata, units), original)
        self.assertEqual(list(metadata), list(original[0]))
        self.assertEqual([u['unit_key'] for u in units], ['b', 'a'])

    def test_builder_keeps_native_serialization_errors_and_has_no_hash_or_io_work(self):
        with patch.object(pt, '_digest', side_effect=AssertionError('Builder must not hash')), \
             patch.object(Path, 'open', side_effect=AssertionError('Builder must not open files')):
            self.assertEqual(ev.build_evaluation_messages({}, [], 0, 'Exact'),
                             legacy_messages({}, [], 0, 'Exact'))
            for units in ([dict(value=object())],):
                with self.assertRaises(TypeError):
                    ev.build_evaluation_messages({}, units, 1, 'Exact')
            circular = []
            circular.append(circular)
            with self.assertRaisesRegex(ValueError, 'Circular reference'):
                ev.build_evaluation_messages({}, circular, 1, 'Exact')

    def test_each_consumer_retains_its_own_count_and_hash_validation(self):
        with pipeline() as f:
            payload = ev.build_evaluation_payload(f.primary, f.source)
            plan = ev.batch_evaluation_payload(payload)
            plan[0]['unit_count'] = 7  # Deliberately inconsistent injected planner.
            with patch.object(ev, 'batch_evaluation_payload', return_value=plan), \
                 patch.object(ev, 'build_evaluation_messages', wraps=ev.build_evaluation_messages) as builder:
                ev.run_evaluation(fixtures.FakeOpenAI(), f.primary, f.source, f.output, 'exact-model')
                self.assertEqual(builder.call_args.args[2], 7)
                fin._validate_evaluation(f.output, f.primary, f.source, payload)
                self.assertEqual(builder.call_args.args[2], 7)
                with self.assertRaisesRegex(ValueError, 'Evaluation batch/request mismatch'):
                    ft.build_fallback_payload(f.primary, f.output, f.source)
                self.assertEqual(builder.call_args.args[2], len(plan[0]['units']))

    def test_system_instruction_bytes_are_frozen(self):
        expected = {
            'LEGACY_SYSTEM_INSTRUCTION': 'f09b453de82765eb8fb5354555ca2ca825c1ebdbcae5f7c4eec03c33ef572f15',
            'DEFAULT_SYSTEM_INSTRUCTION': 'fb1cfdbb2cfb0cfe74802e6bb972d6bee442da5eedbfc127f607fc7eb3dd79d0',
        }
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256(getattr(ev, name).encode('utf-8')).hexdigest(), digest)

    def test_live_request_bodies_hashes_and_model_options(self):
        with pipeline() as f:
            baseline = ev.build_evaluation_payload(f.primary, f.source)
            for policy in (2, 3, 4):
                for fallback in (False, True):
                    for custom in (None, 'Exact custom instruction: 中文\nKeep tabs\tand accents é.'):
                        with self.subTest(policy=policy, fallback=fallback, custom=custom):
                            payload = deepcopy(baseline)
                            payload['metadata']['validation_policy_version'] = policy
                            path = f.folder / ('eval-%s-%s-%s.json' % (policy, fallback, custom is None))
                            client = fixtures.FakeOpenAI()
                            stage = fe.STAGE if fallback else 'openai_evaluation'
                            ev._run_evaluation(client, f.primary, f.source, path, 'exact-model', custom,
                                resume=False, max_batches=None, max_units=1, max_chars=8000,
                                timeout=17, payload_builder=lambda *_: payload, stage=stage,
                                input_hash_key='fallback_sha256' if fallback else 'primary_sha256')
                            state = ev._read(path, stage)
                            instruction = custom if custom is not None else (
                                ev.LEGACY_SYSTEM_INSTRUCTION if policy == 2 else ev.DEFAULT_SYSTEM_INSTRUCTION)
                            plan = ev.batch_evaluation_payload(payload, 1, 8000)
                            self.assertEqual(len(client.calls), len(plan))
                            self.assertEqual(client.timeouts, [dict(timeout=17)] * len(plan))
                            for batch, call, saved in zip(plan, client.calls, state['batches']):
                                messages = legacy_messages(payload['metadata'], batch['units'],
                                                           batch['unit_count'], instruction, fallback)
                                self.assertEqual(call, dict(model='exact-model', input=messages))
                                self.assertEqual(saved['request_hash'], wire_hash(messages))
                            self.assertEqual(state['compatibility']['instruction'], instruction)

    def test_fallback_selection_and_finalization_replay_exact_primary_messages(self):
        with pipeline() as f:
            ev.run_evaluation(fixtures.FakeOpenAI(), f.primary, f.source, f.output, 'exact-model', max_units=1)
            state = ev._read(f.output)
            payload = state['payload']
            expected = [legacy_messages(payload['metadata'], b['units'], b['unit_count'],
                                        state['compatibility']['instruction'])
                        for b in ev.batch_evaluation_payload(payload, 1, 8000)]
            for replay in (lambda: ft.build_fallback_payload(f.primary, f.output, f.source),
                           lambda: fin._validate_evaluation(f.output, f.primary, f.source, payload)):
                with patch.object(pt, '_digest', wraps=pt._digest) as digest:
                    replay()
                messages = [call.args[0] for call in digest.call_args_list
                            if isinstance(call.args[0], list) and call.args[0]
                            and isinstance(call.args[0][0], dict) and call.args[0][0].get('role') == 'system']
                self.assertEqual(messages, expected)


    def test_resealed_primary_request_tampering_rejected_by_both_consumers(self):
        with pipeline() as f:
            ev.run_evaluation(fixtures.FakeOpenAI(), f.primary, f.source, f.output, 'exact-model')
            state = ev._read(f.output)
            for alteration in ('request_hash', 'instruction'):
                changed = deepcopy(state)
                if alteration == 'request_hash':
                    changed['batches'][0]['request_hash'] = 'stale'
                else:
                    changed['compatibility']['instruction'] += '\nChanged.'
                bad = f.folder / ('bad-' + alteration + '.json')
                pt._write_checkpoint(bad, changed)
                with self.assertRaisesRegex(ValueError, 'Evaluation batch/request mismatch'):
                    ft.build_fallback_payload(f.primary, bad, f.source)
                with self.assertRaisesRegex(ValueError, 'Evaluation batch/request identity mismatch'):
                    fin._validate_evaluation(bad, f.primary, f.source, state['payload'])


    def test_replay_keeps_ancestry_and_payload_checks(self):
        with pipeline() as f:
            ev.run_evaluation(fixtures.FakeOpenAI(), f.primary, f.source, f.output, 'exact-model')
            state = ev._read(f.output)
            for field in ('primary_sha256', 'source_sha256', 'payload_sha256'):
                changed = deepcopy(state)
                changed['compatibility'][field] = 'stale'
                bad = f.folder / ('bad-' + field + '.json')
                pt._write_checkpoint(bad, changed)
                with self.assertRaisesRegex(ValueError, 'not bound'):
                    ft.build_fallback_payload(f.primary, bad, f.source)
                with self.assertRaisesRegex(ValueError, 'ancestry/candidate payload mismatch'):
                    fin._validate_evaluation(bad, f.primary, f.source, state['payload'])

    def test_resume_request_identity_does_not_resubmit(self):
        with pipeline() as f:
            ev.run_evaluation(fixtures.FakeOpenAI(), f.primary, f.source, f.output, 'exact-model', max_units=1)
            before = ev._read(f.output)
            client = fixtures.FakeOpenAI()
            ev.run_evaluation(client, f.primary, f.source, f.output, 'exact-model', max_units=1, resume=True)
            self.assertEqual(client.calls, [])
            self.assertEqual(ev._read(f.output)['batches'], before['batches'])
            for kwargs in (dict(model_name='different-model'), dict(system_instruction='different instruction'),
                           dict(max_units=2)):
                options = dict(model_name='exact-model', max_units=1, resume=True)
                options.update(kwargs)
                with self.assertRaisesRegex(ValueError, 'compatibility mismatch'):
                    ev.run_evaluation(client, f.primary, f.source, f.output, **options)
            self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main()
