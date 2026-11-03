"""Offline Stage 3A tests. All provider requests go to injected fake clients."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import pptx_helpers as ph


def make_response(payload):
    rows=[]
    for unit in payload['units']:
        row=dict(unit_key=unit['unit_key'],source_text=unit['source_text'])
        if 'styled_spans' in unit:
            row['translated_spans']=[dict(span_id=s['span_id'],text=s['text'].replace('Hello','Hola').replace('world','mundo'))
                                     for s in unit['styled_spans'] if s['kind']=='text']
        else:
            row['translated_text']=unit['source_text'].replace('Hello','Hola').replace('world','mundo')
        rows.append(row)
    return dict(translations=rows)


class FakeGemini:
    def __init__(self,steps=None):
        self.models=self
        self.steps=list(steps or [])
        self.calls=[]

    def generate_content(self,**kwargs):
        self.calls.append(deepcopy(kwargs))
        payload=json.loads(kwargs['contents'].split('\n\nINPUT JSON:\n',1)[1])
        step=self.steps.pop(0) if self.steps else make_response
        if isinstance(step,BaseException):
            raise step
        value=step(payload) if callable(step) else step
        return SimpleNamespace(text=json.dumps(value,ensure_ascii=False) if isinstance(value,dict) else value)


class PrimaryResponseParserTests(unittest.TestCase):
    def setUp(self):
        self.payload = pt.build_primary_payload([
            dict(unit_key='one',text='Hello',action='translate'),
            dict(unit_key='two',text='world',action='translate')], 'English', 'Spanish')
        self.response = make_response(self.payload)

    def test_complete_object_and_array_and_wrapped_responses(self):
        for value in (self.response, self.response['translations']):
            raw = json.dumps(value)
            for text in (raw, '```json\n'+raw+'\n```', 'Response:\n'+raw+'\nEnd.'):
                with self.subTest(text=text):
                    parsed = pt.parse_primary_response(text)
                    self.assertEqual(parsed,self.response)
                    self.assertTrue(pt.validate_primary_response(parsed,self.payload)['ok'])

    def test_malformed_and_unrelated_json_rejected(self):
        for text in ('not JSON', '{"translations": [}', '[{"unit_key":"one"}',
                     '[{"translations":[]},]', '{"translations":[],"translations":[]}',
                     '{"translations":[{"x":NaN}]}', 'null', '42', '"text"',
                     '[1]', '[[]]', '[{"unrelated":"value"}]'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                pt.parse_primary_response(text)
        for text in ('{}', '{"other":[]}', '{"translations":{}}', '{"translations":[1]}'):
            with self.subTest(text=text):
                self.assertFalse(pt.validate_primary_response(pt.parse_primary_response(text),self.payload)['ok'])
        evaluations = {'evaluations': []}
        self.assertEqual(pt.parse_primary_response(json.dumps(evaluations)),evaluations)

    def test_normalization_does_not_bypass_identity_or_completeness_checks(self):
        rows = self.response['translations']
        for bad in ([rows[0]], [rows[0],rows[0],rows[1]]):
            for value in (bad, {'translations':bad}):
                with self.subTest(value=value):
                    checked = pt.validate_primary_response(pt.parse_primary_response(json.dumps(value)),self.payload)
                    self.assertFalse(checked['ok'])
        for issue in ('source','span'):
            units = [dict(unit_key='styled',text='Hello',action='translate',
                          styled_spans=[dict(span_id='p01',kind='text',text='Hello')])]
            payload = pt.build_primary_payload(units,'English','Spanish')
            rows = [dict(unit_key='styled',source_text='Hello',
                         translated_spans=[dict(span_id='p01',text='Hola')])]
            if issue == 'source':rows[0]['source_text']='wrong'
            else:rows[0]['translated_spans'][0]['span_id']='wrong'
            checked = pt.validate_primary_response(pt.parse_primary_response(json.dumps(rows)),payload)
            self.assertFalse(checked['ok'])


class PrimaryTranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.source=self.folder/'source.pptx';self.source.write_bytes(b'primary-source')
        self.checkpoint=self.folder/'primary.json'
        self.units=ph.classify_pptx_text_units([
            dict(unit_key='ordinary',text='Hello 1'),
            dict(unit_key='styled',text='Hello\vworld',styled_spans=[
                dict(span_id='p01',kind='text',text='Hello'),dict(span_id='p02',kind='soft_break',text='\v'),
                dict(span_id='p03',kind='text',text='world')],span_formats={'private':'DO_NOT_SEND'}),
            dict(unit_key='protected',text='https://example.org')])

    def run_stage(self,client=None,**settings):
        return pt.run_primary_translation(client or FakeGemini(),self.units,self.source,'English','Spanish',
            'fake','Translate',self.checkpoint,**settings)

    def test_partial_success_retries_only_bad_paragraph_and_records_provenance(self):
        def broken(payload):
            r=make_response(payload);r['translations'][1]['translated_spans'].pop();return r
        client=FakeGemini([broken])
        result=self.run_stage(client)
        self.assertEqual(result['completed_unit_keys'],['ordinary','styled'])
        requests=[json.loads(c['contents'].split('\n\nINPUT JSON:\n')[1]) for c in client.calls]
        self.assertEqual([len(r['units']) for r in requests],[2,1])
        self.assertIn('invalid_or_incomplete_span_ids',requests[1]['units'][0]['validation_feedback'][0])
        self.assertNotIn('DO_NOT_SEND',json.dumps(client.calls))
        state=pt._read_checkpoint(self.checkpoint)
        self.assertEqual(state['unit_results']['ordinary']['attempt_count'],1)
        self.assertEqual(state['unit_results']['styled']['attempt_count'],2)
        for record in state['unit_results'].values():
            candidate=record['candidate'];self.assertTrue(candidate['validation']['ok'])
            self.assertIn('request_hash',candidate['provenance'])
        self.assertEqual(client.calls[0]['config']['http_options'],{'retry_options':{'attempts':1}})

    def test_legacy_array_parse_failures_remain_readable_and_third_attempt_can_resume(self):
        def legacy_parse(raw):
            return json.loads(raw[raw.find('{'):raw.rfind('}')+1],object_pairs_hook=pt._unique_object)
        def array_response(payload):
            return json.dumps(make_response(payload)['translations'])
        with patch.object(pt,'parse_primary_response',side_effect=legacy_parse):
            self.run_stage(FakeGemini([array_response,array_response]),max_batches=2)
        before=self.checkpoint.read_bytes()
        old=pt.read_primary_state(self.checkpoint)
        self.assertEqual(self.checkpoint.read_bytes(),before)
        self.assertTrue(all(r['attempt_count']==2 and r['status']=='retry_pending'
                            for r in old['unit_results'].values()))
        fake=FakeGemini([array_response])
        result=self.run_stage(fake,resume=True,max_batches=1)
        self.assertEqual(len(fake.calls),1)
        self.assertEqual(len(result['completed_unit_keys']),2)
        state=pt.read_primary_state(self.checkpoint)
        self.assertEqual(state['batches'][0]['attempts'][:2],old['batches'][0]['attempts'])
        self.assertEqual(state['batches'][0]['attempts'][2]['request_id'],'b1-a3')
        state['batches'][0]['attempts'][0]['error']='JSONDecodeError: fabricated'
        pt._write_checkpoint(self.checkpoint,state)
        with self.assertRaises(ValueError):pt.read_primary_state(self.checkpoint)

    def test_optional_domain_context_preserves_empty_prompt_and_binds_nonempty_identity(self):
        baseline=FakeGemini();self.run_stage(baseline)
        saved=pt.read_primary_state(self.checkpoint)
        before=self.checkpoint.read_bytes()
        for context in (None,'','   '):
            fake=FakeGemini()
            self.run_stage(fake,resume=True,domain_context=context)
            self.assertEqual(fake.calls,[])
            self.assertEqual(pt.read_primary_state(self.checkpoint),saved)
        for context in ('Encouraging workshop.',42):
            fake=FakeGemini()
            with self.assertRaises(ValueError):self.run_stage(fake,resume=True,domain_context=context)
            self.assertEqual(fake.calls,[])
        self.assertEqual(self.checkpoint.read_bytes(),before)
        self.checkpoint=self.folder/'with_context.json'
        fake=FakeGemini();self.run_stage(fake,domain_context='Encouraging workshop.')
        contextual=pt.read_primary_state(self.checkpoint)
        self.assertTrue(fake.calls[0]['contents'].startswith(
            'Translate\n\nSUPPLEMENTARY DOMAIN CONTEXT:\nEncouraging workshop.\n\nINPUT JSON:\n'))
        self.assertEqual(contextual['compatibility']['instruction'],
                         'Translate\n\nSUPPLEMENTARY DOMAIN CONTEXT:\nEncouraging workshop.')
        a=saved['batches'][0]['attempts'][0];b=contextual['batches'][0]['attempts'][0]
        self.assertEqual(a['request'],b['request'])
        self.assertNotEqual(a['request_hash'],b['request_hash'])
        for context in (None,'Different workshop.'):
            no_calls=FakeGemini()
            with self.assertRaises(ValueError):self.run_stage(no_calls,resume=True,domain_context=context)
            self.assertEqual(no_calls.calls,[])

    def test_duplicates_invalidate_only_ambiguous_unit_and_unknown_ids_are_not_accepted(self):
        payload=pt.build_primary_payload(self.units,'English','Spanish');response=make_response(payload)
        response['translations'].append(deepcopy(response['translations'][0]))
        checked=pt.validate_primary_response(response,payload)
        self.assertEqual([r['unit_key'] for r in checked['translations']],['styled'])
        self.assertIn('ordinary:duplicate_unit_id',checked['errors'])
        response=make_response(payload);response['translations'].append(dict(unit_key='alien'))
        checked=pt.validate_primary_response(response,payload)
        self.assertEqual(len(checked['translations']),2);self.assertFalse(checked['ok'])

    def test_styled_atomicity_and_numeric_prose(self):
        units=ph.classify_pptx_text_units([dict(unit_key='prose',text='4000 years ago: Abraham'),
                                          dict(unit_key='label',text='1. Hello')])
        self.assertEqual(units[0]['number_prefix'],'');self.assertEqual(units[1]['number_prefix'],'1. ')
        payload=pt.build_primary_payload(units,'English','Spanish')
        checked=pt.validate_primary_response(dict(translations=[
            dict(unit_key='prose',source_text=units[0]['text'],translated_text='Hace 4000 años: Abraham'),
            dict(unit_key='label',source_text=units[1]['text'],translated_text='Hola 1.')]),payload)
        self.assertEqual([r['unit_key'] for r in checked['translations']],['prose'])
        for prefix in ('1. ','1) ','(1) ','IV. '):
            self.assertEqual(ph.classify_pptx_text_units([dict(unit_key='x',text=prefix+'Hello')])[0]['number_prefix'],prefix)

    def test_budget_exhaustion_continues_to_independent_batch_and_never_resets(self):
        client=FakeGemini([RuntimeError('offline')]*3)
        r=self.run_stage(client,max_units=1)
        self.assertEqual(r['exhausted_unit_keys'],['ordinary']);self.assertEqual(r['completed_unit_keys'],['styled'])
        self.assertEqual(len(client.calls),4)
        again=FakeGemini();self.run_stage(again,max_units=1,resume=True)
        self.assertEqual(again.calls,[])
        state=pt._read_checkpoint(self.checkpoint)
        self.assertEqual(len(state['batches'][0]['attempts']),3)

    def test_limits_retry_pending_resume_and_zero_limit(self):
        c=FakeGemini(['malformed'])
        r=self.run_stage(c,max_batches=1)
        self.assertEqual(r['retry_pending_unit_keys'],['ordinary','styled'])
        self.assertEqual(r['pending_unit_keys'],[])
        r=self.run_stage(resume=True,max_batches=1)
        self.assertEqual(len(r['completed_unit_keys']),2)
        self.assertEqual(pt._read_checkpoint(self.checkpoint)['unit_results']['ordinary']['attempt_count'],2)
        no_calls=FakeGemini();self.run_stage(no_calls,resume=True,max_batches=0);self.assertEqual(no_calls.calls,[])

    def test_interruption_budget_survives_restart(self):
        with self.assertRaises(KeyboardInterrupt):self.run_stage(FakeGemini([KeyboardInterrupt()]))
        r=self.run_stage(resume=True)
        self.assertEqual(len(r['completed_unit_keys']),2)
        self.assertEqual(pt._read_checkpoint(self.checkpoint)['unit_results']['styled']['attempt_count'],2)

    def test_saved_raw_response_replays_after_persistence_failure_without_repeating_provider(self):
        real=pt._write_checkpoint
        def fail_commit(path,state):
            if state['batches'][0]['attempts'] and state['batches'][0]['attempts'][-1]['status']=='success':
                raise OSError('disk')
            return real(path,state)
        with patch.object(pt,'_write_checkpoint',side_effect=fail_commit):
            with self.assertRaises(OSError):self.run_stage()
        c=FakeGemini();r=self.run_stage(c,resume=True)
        self.assertEqual(c.calls,[]);self.assertEqual(len(r['completed_unit_keys']),2)

    def test_source_integrity_fatal_no_later_calls(self):
        def change(payload):self.source.write_bytes(b'changed');return make_response(payload)
        c=FakeGemini([change])
        with self.assertRaisesRegex(ValueError,'source changed'):self.run_stage(c,max_units=1)
        self.assertEqual(len(c.calls),1)

    def test_corruption_and_incompatible_resume_rejected_before_requests(self):
        self.run_stage(max_batches=0)
        c=FakeGemini()
        with self.assertRaises(ValueError):self.run_stage(c,resume=True,max_units=1)
        state=pt._read_checkpoint(self.checkpoint);state['unit_results']['ordinary']['attempt_count']=2
        pt._write_checkpoint(self.checkpoint,state)
        with self.assertRaises(ValueError):self.run_stage(c,resume=True)
        self.assertEqual(c.calls,[])

    def test_candidate_tampering_and_unsupported_schema_rejected(self):
        self.run_stage();state=pt._read_checkpoint(self.checkpoint)
        state['unit_results']['styled']['candidate']['primary_span_translations'][0]['text']='wrong'
        pt._write_checkpoint(self.checkpoint,state)
        with self.assertRaises(ValueError):pt._read_checkpoint(self.checkpoint)
        state['schema_version']=1;pt._write_checkpoint(self.checkpoint,state)
        with self.assertRaisesRegex(ValueError,'historical'):pt._read_checkpoint(self.checkpoint)

    def test_parser_and_configuration_fail_before_submitting(self):
        for text in ('{"x":1,"x":2}','{"x":NaN}','not JSON'):
            with self.assertRaises(ValueError):pt.parse_primary_response(text)
        for settings in ({'max_batches':True},{'max_batches':-1},{'max_units':0},{'temperature':float('nan')}):
            with self.assertRaises(ValueError):self.run_stage(**settings)
        self.assertFalse(self.checkpoint.exists())

    def test_missing_unit_retry_does_not_resubmit_valid_sibling(self):
        def missing(payload):
            response=make_response(payload);response['translations'].pop();return response
        client=FakeGemini([missing])
        result=self.run_stage(client)
        self.assertEqual(len(result['completed_unit_keys']),2)
        state=pt._read_checkpoint(self.checkpoint)
        self.assertEqual(state['batches'][0]['attempts'][1]['unit_keys'],['styled'])
        self.assertIn('styled:missing_unit_id',state['batches'][0]['attempts'][1]['request']['units'][0]['validation_feedback'])
        self.assertEqual(state['unit_results']['styled']['retry_count'],1)

    def test_invalid_styled_text_rejects_entire_paragraph(self):
        def blank_span(payload):
            response=make_response(payload);response['translations'][1]['translated_spans'][1]['text']='';return response
        result=self.run_stage(FakeGemini([blank_span]),max_batches=1)
        self.assertEqual(result['completed_unit_keys'],['ordinary'])
        state=pt._read_checkpoint(self.checkpoint)
        self.assertIsNone(state['unit_results']['styled']['candidate'])
        self.assertEqual(state['unit_results']['styled']['status'],'retry_pending')

    def test_validation_exhaustion_continues_and_records_every_raw_response(self):
        client=FakeGemini(['malformed']*3)
        result=self.run_stage(client,max_units=1)
        self.assertEqual(result['exhausted_unit_keys'],['ordinary'])
        self.assertEqual(result['completed_unit_keys'],['styled'])
        state=pt._read_checkpoint(self.checkpoint)
        self.assertEqual([a['raw_response'] for a in state['batches'][0]['attempts']],['malformed']*3)
        self.assertEqual(state['unit_results']['ordinary']['retry_count'],2)

    def test_partial_candidates_are_the_only_downstream_evaluation_inputs(self):
        from pptx_translation import evaluation as ev
        def missing(payload):
            response=make_response(payload);response['translations'].pop();return response
        self.run_stage(FakeGemini([missing]),max_batches=1)
        payload=ev.build_evaluation_payload(self.checkpoint,self.source)
        self.assertEqual([u['unit_key'] for u in payload['units']],['ordinary'])
        record=pt._read_checkpoint(self.checkpoint)['unit_results']['ordinary']['candidate']
        self.assertEqual(payload['units'][0]['candidate_sha256'],record['candidate_sha256'])
        self.assertEqual(payload['units'][0]['generation_provenance'],record['provenance'])

    def test_invalid_client_and_local_configuration_errors_are_fatal(self):
        with self.assertRaisesRegex(ValueError,'Gemini client'):
            pt.run_primary_translation(None,self.units,self.source,'English','Spanish','fake','Translate',self.checkpoint)
        self.assertEqual(pt._read_checkpoint(self.checkpoint)['unit_results']['ordinary']['attempt_count'],0)
        client=FakeGemini([TypeError('invalid local SDK configuration')])
        with self.assertRaises(TypeError):self.run_stage(client,resume=True)
        self.assertEqual(len(client.calls),1)
        self.assertEqual(pt._read_checkpoint(self.checkpoint)['batches'][0]['attempts'][0]['error_kind'],'configuration')

    def test_lock_overwrite_and_atomic_failure(self):
        self.run_stage(max_batches=0);before=self.checkpoint.read_bytes()
        with self.assertRaises(FileExistsError):self.run_stage()
        lock=self.checkpoint.with_name('primary.json.lock');lock.write_text('writer')
        with self.assertRaises(FileExistsError):self.run_stage(resume=True)
        lock.unlink()
        with patch.object(pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.run_stage(resume=True)
        self.assertEqual(self.checkpoint.read_bytes(),before)


if __name__=='__main__':unittest.main()
