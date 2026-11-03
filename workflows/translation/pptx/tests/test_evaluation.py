"""Offline evaluator regression; all requests use injected clients."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import evaluation as ev
from pptx_translation import pptx_helpers as ph
from pptx_translation.workflow_config import WorkflowConfig, LazyAPIClients


class FakePrimary:
    def __init__(self):
        self.models = self

    def generate_content(self, **kwargs):
        payload = json.loads(kwargs['contents'].split('\n\nINPUT JSON:\n')[1])
        rows = []
        for u in payload['units']:
            row = dict(unit_key=u['unit_key'], source_text=u['source_text'])
            if 'styled_spans' in u:
                row['translated_spans'] = [dict(span_id=s['span_id'], text=s['text'].replace('Hello','Hola'))
                                           for s in u['styled_spans'] if s['kind']=='text']
            else:
                row['translated_text'] = u['source_text'].replace('Hello','Hola')
            rows.append(row)
        return SimpleNamespace(text=json.dumps(dict(translations=rows)))


class FakeOpenAI:
    def __init__(self, steps=None):
        self.responses = self
        self.calls = []
        self.steps = list(steps or [])
        self.timeouts = []

    def with_options(self, **kwargs):
        self.timeouts.append(kwargs)
        return self

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        payload = json.loads(kwargs['input'][1]['content'].split('\n\n',1)[1])
        step = self.steps.pop(0) if self.steps else None
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            result = step(payload)
        elif step is not None:
            result = step
        else:
            result = {'evaluations': [dict(unit_key=u['unit_key'], passed=u['unit_key']!='styled',
                         feedback='Meaning omitted.' if u['unit_key']=='styled' else '') for u in payload['units']]}
        return SimpleNamespace(status='completed', output_text=json.dumps(result) if isinstance(result,dict) else result)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder/'source.pptx'
        self.source.write_bytes(b'offline evaluator source identity')
        self.primary = self.folder/'primary.json'
        self.output = self.folder/'evaluation.json'
        self.units = ph.classify_pptx_text_units([
            dict(unit_key='one',text='Hello 1',slide_number=1),
            dict(unit_key='styled',text='Hello\vworld',slide_number=2, styled_spans=[
                dict(span_id='p01',kind='text',text='Hello'),
                dict(span_id='p02',kind='soft_break',text='\v'),
                dict(span_id='p03',kind='text',text='world')],span_formats={'local':'DO_NOT_SEND_XML'}),
            dict(unit_key='three',text='Hello 3',slide_number=3),
            dict(unit_key='protected',text='https://example.org')])
        pt.run_primary_translation(FakePrimary(),self.units,self.source,'English','Spanish','fake','Translate',self.primary)
        self.original = self.primary.read_bytes()
        self.source_original = self.source.read_bytes()

    def tearDown(self):
        self.assertEqual(self.primary.read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.source_original)

    def run_stage(self, client=None, **kwargs):
        return ev.run_evaluation(client or FakeOpenAI(),self.primary,self.source,self.output,'fake-evaluator',**kwargs)

    def test_payload_full_context_and_results_preserve_rejection_feedback(self):
        payload = ev.build_evaluation_payload(self.primary,self.source)
        self.assertEqual(len(payload['units']),3)
        self.assertNotIn('DO_NOT_SEND_XML', json.dumps(payload))
        self.assertEqual(payload['units'][1]['source_text'],'Hello\vworld')
        self.assertEqual(payload['units'][1]['primary_translation'],'Hola\vworld')
        client = FakeOpenAI()
        result = self.run_stage(client)
        self.assertEqual((result['evaluated'],result['accepted'],result['rejected']),(3,2,1))
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['evaluations'][1]['feedback'],'Meaning omitted.')
        self.assertEqual(client.timeouts,[dict(timeout=60)])
        self.assertEqual(client.calls[0]['input'][0]['role'],'system')
        self.assertIn('FAIL only when there is a clear substantive problem',client.calls[0]['input'][0]['content'])
        self.assertEqual(result, ev.inspect_evaluation_checkpoint(self.output))

    def test_partial_limit_and_resume_skip_success(self):
        first = FakeOpenAI()
        r = self.run_stage(first,max_units=1,max_batches=1)
        self.assertEqual((r['evaluated'],len(r['pending_unit_keys'])),(1,2))
        second = FakeOpenAI()
        r = self.run_stage(second,max_units=1,max_batches=None,resume=True)
        self.assertEqual(len(second.calls),2)
        self.assertEqual(r['evaluated'],3)
        third = FakeOpenAI()
        self.run_stage(third,max_units=1,resume=True)
        self.assertEqual(third.calls,[])

    def test_zero_limit_creates_plan_without_calls(self):
        client = FakeOpenAI()
        result = self.run_stage(client,max_batches=0)
        self.assertEqual(result['pending_unit_keys'],['one','styled','three'])
        self.assertEqual(client.calls,[])

    def test_only_completed_primary_batches_are_eligible(self):
        partial = self.folder/'partial-primary.json'
        pt.run_primary_translation(FakePrimary(),self.units,self.source,'English','Spanish',
                                   'fake','Translate',partial,max_units=1,max_batches=1)
        payload = ev.build_evaluation_payload(partial,self.source)
        self.assertEqual([u['unit_key'] for u in payload['units']],['one'])
        self.assertEqual(payload['metadata']['primary_uncompleted_units'],2)

    def test_completed_result_tampering_rejected_even_with_valid_checksum(self):
        self.run_stage()
        state = ev._read(self.output)
        state['batches'][0]['evaluations'][0]['passed']=False
        pt._write_checkpoint(self.output,state)
        client = FakeOpenAI()
        with self.assertRaises(ValueError):self.run_stage(client,resume=True)
        self.assertEqual(client.calls,[])

    def test_invalid_limits_and_settings_do_not_create_output(self):
        for settings in ({'max_batches':-1},{'max_batches':True},{'max_units':0},
                         {'max_chars':0},{'timeout':float('nan')},{'system_instruction':''}):
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):self.run_stage(**settings)
                self.assertFalse(self.output.exists())

    def test_response_protocol_validation(self):
        good = dict(evaluations=[dict(unit_key='a',passed=True,feedback='')])
        mutations = [None, {'evaluations':None}, {'evaluations':[None]},
                     dict(evaluations=[]), dict(evaluations=good['evaluations']*2),
                     dict(evaluations=[dict(unit_key='unknown',passed=True,feedback='')]),
                     dict(evaluations=[dict(unit_key='a',passed='true',feedback='')]),
                     dict(evaluations=[dict(unit_key='a',passed=False,feedback=' ')]),
                     dict(evaluations=[dict(unit_key='a',passed=True,feedback='comment')])]
        for value in mutations:
            with self.subTest(value=value):
                checked = ev.validate_evaluation_response(value,['a'])
                self.assertFalse(checked['ok'])
                self.assertEqual(checked['evaluations'],[])
        self.assertTrue(ev.validate_evaluation_response(good,['a'])['ok'])

    def test_provider_malformed_and_partial_errors_are_durable_and_retryable(self):
        for step in (RuntimeError('provider unavailable'), 'not JSON', {'evaluations':[]}):
            with self.subTest(step=step):
                self.output.unlink(missing_ok=True)
                with self.assertRaises(ev.EvaluationStageError):
                    self.run_stage(FakeOpenAI([step]),max_units=1)
                result = ev.inspect_evaluation_checkpoint(self.output)
                self.assertEqual(result['evaluated'],0)
                self.assertEqual(result['error_unit_keys'],['one'])
                self.assertEqual(len(result['pending_unit_keys']),2)
                result = self.run_stage(resume=True,max_units=1,max_batches=1)
                self.assertEqual(result['evaluated'],1)
                self.assertEqual(len(result['batch_results'][0]['attempts']),2)

    def test_interruption_resume_and_completed_batch_survives_later_failure(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_stage(FakeOpenAI([lambda p: {'evaluations':[dict(unit_key='one',passed=True,feedback='')]}, KeyboardInterrupt()]),max_units=1)
        r = ev.inspect_evaluation_checkpoint(self.output)
        self.assertEqual(r['evaluated'],1)
        client = FakeOpenAI()
        r = self.run_stage(client,resume=True,max_units=1)
        self.assertEqual(len(client.calls),2)
        self.assertEqual(r['evaluated'],3)

    def test_resume_incompatibility_corruption_and_output_protection(self):
        self.run_stage(max_batches=0)
        client = FakeOpenAI()
        with self.assertRaises(ValueError):self.run_stage(client,resume=True,max_units=1)
        with self.assertRaises(FileExistsError):self.run_stage(client)
        state = json.loads(self.output.read_text());state['status']='changed'
        self.output.write_text(json.dumps(state))
        with self.assertRaises(ValueError):self.run_stage(client,resume=True)
        self.assertEqual(client.calls,[])
        with self.assertRaises(ValueError):
            ev.run_evaluation(client,self.primary,self.source,self.primary,'fake')

    def test_source_mismatch_and_primary_pair_mismatch_rejected_before_call(self):
        state = pt._read_checkpoint(self.primary)
        state['units'][1]['primary_translation']='incorrect'
        pt._write_checkpoint(self.primary,state)
        with self.assertRaises(ValueError):ev.build_evaluation_payload(self.primary,self.source)
        self.primary.write_bytes(self.original)
        self.source.write_bytes(b'changed')
        with self.assertRaises(ValueError):ev.build_evaluation_payload(self.primary,self.source)
        self.source.write_bytes(self.source_original)

    def test_atomic_failure_and_lock_do_not_overwrite_results(self):
        self.run_stage(max_batches=0)
        saved = self.output.read_bytes()
        with patch.object(pt.os,'replace',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):self.run_stage(resume=True)
        self.assertEqual(saved,self.output.read_bytes())
        lock = self.output.with_name('evaluation.json.lock')
        lock.write_text('other writer')
        with self.assertRaises(FileExistsError):self.run_stage(resume=True)




if __name__ == '__main__':
    unittest.main()
