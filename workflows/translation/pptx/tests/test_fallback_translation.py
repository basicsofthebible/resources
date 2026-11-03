"""Offline Claude generation, upstream binding and notebook regressions."""
from copy import deepcopy
from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import evaluation as ev
from pptx_translation import fallback_translation as ft
import test_evaluation as evaluation_fixtures
from test_evaluation import FakeOpenAI
from pptx_translation.workflow_config import WorkflowConfig, LazyAPIClients


class FakeClaude:
    def __init__(self, steps=()):
        self.messages=self
        self.steps=list(steps)
        self.calls=[]
        self.options=[]

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        payload=json.loads(kwargs['messages'][0]['content'].split('\n\n',1)[1])
        step=self.steps.pop(0) if self.steps else None
        if isinstance(step,BaseException):raise step
        rows=[]
        for u in payload['units']:
            row=dict(unit_key=u['unit_key'],source_text=u['source_text'])
            if 'styled_spans' in u:
                row['translated_spans']=[dict(span_id=s['span_id'],text=s['text']) for s in u['styled_spans'] if s['kind']=='text']
            else:row['translated_text']=u['source_text']
            rows.append(row)
        result=step if step is not None else dict(translations=rows)
        if callable(result):result=result(payload)
        return NS(content=[NS(type='text',text=json.dumps(result) if isinstance(result,dict) else result)],stop_reason='end_turn')


class FallbackTests(unittest.TestCase):
    def setUp(self):
        # Reuse fixture setup only, not evaluator test methods.
        evaluation_fixtures.EvaluationTests.setUp(self)
        ev.run_evaluation(FakeOpenAI(),self.primary,self.source,self.output,'fake')
        self.evaluation_original=self.output.read_bytes()
        self.fallback=self.folder/'fallback.json'

    def tearDown(self):
        evaluation_fixtures.EvaluationTests.tearDown(self)
        self.assertEqual(self.output.read_bytes(),self.evaluation_original)

    def run_stage(self,client=None,**kwargs):
        return ft.run_fallback_translation(client or FakeClaude(),self.primary,self.output,self.source,self.fallback,'fake',**kwargs)

    def test_only_semantic_rejection_context_and_separate_span_candidates(self):
        p=ft.build_fallback_payload(self.primary,self.output,self.source)
        self.assertEqual([u['unit_key'] for u in p['units']],['styled'])
        self.assertEqual(p['units'][0]['rejected_translation'],'Hola\vworld')
        self.assertEqual(p['units'][0]['evaluator_feedback'],'Meaning omitted.')
        self.assertNotIn('DO_NOT_SEND_XML',json.dumps(p))
        c=FakeClaude();r=self.run_stage(c)
        self.assertEqual(r['status'],'completed')
        self.assertEqual(r['candidates'][0]['fallback_translation'],'Hello\vworld')
        self.assertEqual(len(r['candidates'][0]['fallback_span_translations']),2)
        self.assertNotIn('primary_translation',r['candidates'][0])
        self.assertEqual(c.options,[dict(timeout=60,max_retries=0)])
        self.assertEqual(r,ft.inspect_fallback_checkpoint(self.fallback))
        self.run_stage(c,resume=True)
        self.assertEqual(len(c.calls),1)

    def test_ordinary_candidate_and_no_rejections(self):
        evaluator=self.folder/'all-rejected.json'
        def reject(payload):
            return dict(evaluations=[dict(unit_key=u['unit_key'],passed=False,feedback='Wrong meaning.') for u in payload['units']])
        ev.run_evaluation(FakeOpenAI([reject]),self.primary,self.source,evaluator,'fake')
        r=ft.run_fallback_translation(FakeClaude(),self.primary,evaluator,self.source,self.fallback,'fake')
        self.assertEqual(len(r['candidates']),3)
        self.assertNotIn('fallback_span_translations',r['candidates'][0])
        accepted=self.folder/'accepted.json'
        ev.run_evaluation(FakeOpenAI([lambda p:dict(evaluations=[dict(unit_key=u['unit_key'],passed=True,feedback='') for u in p['units']])]),self.primary,self.source,accepted,'fake')
        self.assertEqual(ft.build_fallback_payload(self.primary,accepted,self.source)['units'],[])

    def test_zero_cap_resume_mismatch_and_output_protection(self):
        c=FakeClaude();self.run_stage(c,max_batches=0)
        self.assertEqual(c.calls,[])
        with self.assertRaises(ValueError):self.run_stage(c,resume=True,max_units=1)
        with self.assertRaises(FileExistsError):self.run_stage(c)
        self.run_stage(c,resume=True,max_batches=1)
        self.assertEqual(len(c.calls),1)
        with self.assertRaises(ValueError):
            ft.run_fallback_translation(c,self.primary,self.output,self.source,self.primary,'fake')

    def test_errors_atomic_validation_and_explicit_retry(self):
        for step,kind in [(RuntimeError('offline outage'),'provider'),('invalid JSON','validation'),
                          ({'translations':[]},'validation'),(KeyboardInterrupt(),'provider')]:
            with self.subTest(kind=kind,step=step):
                self.fallback.unlink(missing_ok=True)
                c=FakeClaude([step])
                with self.assertRaises(KeyboardInterrupt if isinstance(step,KeyboardInterrupt) else ft.FallbackStageError):self.run_stage(c)
                r=ft.inspect_fallback_checkpoint(self.fallback)
                self.assertEqual(r['candidates'],[])
                self.assertEqual(r['error_unit_keys'],['styled'])
                self.assertEqual(r['batch_results'][0]['attempts'][-1]['error_kind'],kind)
                self.assertEqual(len(c.calls),1)
                r=self.run_stage(resume=True)
                self.assertEqual(len(r['batch_results'][0]['attempts']),2)

    def test_span_contract_rejects_missing_duplicate_and_structural_ids(self):
        p=ft.build_fallback_payload(self.primary,self.output,self.source)
        for spans in ([],[dict(span_id='p01',text='Hello')]*2,
                      [dict(span_id='p01',text='Hello'),dict(span_id='p02',text='\v'),dict(span_id='p03',text='world')]):
            checked=ft.validate_fallback_response(dict(translations=[dict(unit_key='styled',source_text='Hello\vworld',translated_spans=spans)]),p)
            self.assertFalse(checked['ok']);self.assertEqual(checked['candidates'],[])

    def test_primary_provider_validation_and_pending_exclusions(self):
        from test_primary_translation import FakeGemini
        for step,category in [(RuntimeError('offline'),'primary_provider_errors'),
                              ('not JSON','primary_validation_errors')]:
            primary=self.folder/(category+'.json')
            pt.run_primary_translation(FakeGemini([step]*3),self.units,self.source,'English','Spanish',
                                       'fake','Translate',primary,max_units=1,max_batches=3)
            evaluator=self.folder/(category+'-eval.json')
            ev.run_evaluation(FakeOpenAI(),primary,self.source,evaluator,'fake')
            payload=ft.build_fallback_payload(primary,evaluator,self.source)
            self.assertEqual(payload['units'],[])
            self.assertEqual(payload['excluded'][category],['one'])
            self.assertEqual(len(payload['excluded']['primary_pending']),2)

    def test_truncation_and_upstream_changes_on_resume(self):
        class Truncated(FakeClaude):
            def create(self,**kwargs):
                result=super().create(**kwargs)
                result.stop_reason='max_tokens'
                return result
        with self.assertRaises(ft.FallbackStageError):self.run_stage(Truncated())
        r=ft.inspect_fallback_checkpoint(self.fallback)
        self.assertEqual(r['candidates'],[])
        self.assertEqual(r['batch_results'][0]['attempts'][0]['stop_reason'],'max_tokens')
        state=ev._read(self.output);state['created_at']='changed'
        changed=self.folder/'changed-evaluation.json';pt._write_checkpoint(changed,state)
        c=FakeClaude()
        with self.assertRaises(ValueError):
            ft.run_fallback_translation(c,self.primary,changed,self.source,self.fallback,'fake',resume=True)
        self.assertEqual(c.calls,[])

    def test_upstream_binding_and_evaluator_errors_excluded(self):
        other=self.folder/'failed-evaluation.json'
        with self.assertRaises(ev.EvaluationStageError):ev.run_evaluation(FakeOpenAI([RuntimeError('offline')]),self.primary,self.source,other,'fake')
        p=ft.build_fallback_payload(self.primary,other,self.source)
        self.assertEqual(p['units'],[])
        self.assertEqual(len(p['excluded']['evaluator_errors']),3)
        state=ev._read(other);state['compatibility']['primary_sha256']='stale';pt._write_checkpoint(other,state)
        with self.assertRaises(ValueError):ft.build_fallback_payload(self.primary,other,self.source)
        state=ev._read(self.output);state['batches'][0]['request_hash']='wrong'
        bad=self.folder/'bad.json';pt._write_checkpoint(bad,state)
        with self.assertRaises(ValueError):ft.build_fallback_payload(self.primary,bad,self.source)

    def test_completed_candidate_tampering_lock_and_atomic_write(self):
        self.run_stage(max_batches=0)
        saved=self.fallback.read_bytes()
        with patch.object(pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.run_stage(resume=True)
        self.assertEqual(saved,self.fallback.read_bytes())
        lock=self.fallback.with_name('fallback.json.lock');lock.write_text('another process')
        with self.assertRaises(FileExistsError):self.run_stage(resume=True)
        lock.unlink();self.run_stage(resume=True)
        state=ft._read(self.fallback);state['batches'][0]['candidates'][0]['fallback_translation']='changed'
        pt._write_checkpoint(self.fallback,state)
        with self.assertRaises(ValueError):self.run_stage(resume=True)




if __name__=='__main__':unittest.main()
