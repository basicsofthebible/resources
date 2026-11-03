"""Stage 3E adapter, exact candidate binding, fake API and notebook regressions."""
from copy import deepcopy
from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import evaluation as ev
from pptx_translation import fallback_evaluation as fe
from pptx_translation import fallback_translation as ft
from pptx_translation import primary_translation as pt
import test_evaluation as fixtures
from test_fallback_translation import FakeClaude
from pptx_translation.workflow_config import WorkflowConfig, LazyAPIClients


def reject_all(payload):
    return dict(evaluations=[dict(unit_key=u['unit_key'], passed=False, feedback='Meaning omitted.') for u in payload['units']])


class FallbackEvaluationTests(unittest.TestCase):
    def setUp(self):
        fixtures.EvaluationTests.setUp(self)
        ev.run_evaluation(fixtures.FakeOpenAI([reject_all]),self.primary,self.source,self.output,'fake')
        self.fallback=self.folder/'fallback.json'
        ft.run_fallback_translation(FakeClaude(),self.primary,self.output,self.source,self.fallback,'fake')
        self.target=self.folder/'fallback-evaluation.json'
        self.upstream={p:p.read_bytes() for p in (self.primary,self.output,self.fallback,self.source)}

    def tearDown(self):
        for p,data in self.upstream.items():self.assertEqual(p.read_bytes(),data)

    def run_stage(self,client=None,**kwargs):
        return fe.run_fallback_evaluation(client or fixtures.FakeOpenAI(),self.fallback,self.source,self.target,'fake-evaluator',**kwargs)

    def test_inherit_primary_model_override_and_missing_link(self):
        self.assertEqual(fe.resolve_evaluation_model(self.fallback), 'fake')
        self.assertEqual(fe.resolve_evaluation_model(self.fallback, 'override'), 'override')
        result=fe.run_fallback_evaluation(fixtures.FakeOpenAI(),self.fallback,self.source,self.target)
        self.assertEqual(result['evaluated'],3)
        self.assertEqual(fe._read(self.target)['compatibility']['model'],'fake')
        with self.assertRaises(ValueError):
            fe.run_fallback_evaluation(fixtures.FakeOpenAI(),self.fallback,self.source,self.target,'override',resume=True)
        state=ft._read(self.fallback)
        state['provenance']['evaluation_checkpoint']=str(self.folder/'missing.json')
        other=self.folder/'missing-link.json';pt._write_checkpoint(other,state)
        with self.assertRaisesRegex(ValueError,'exact linked'):
            fe.resolve_evaluation_model(other)
        self.assertEqual(fe.resolve_evaluation_model(other,'override'),'override')
        state['provenance']['evaluation_checkpoint']=str(self.output)
        state['compatibility']['evaluation_sha256']='stale'
        pt._write_checkpoint(other,state)
        with self.assertRaises(ValueError):fe.resolve_evaluation_model(other)

    def test_context_policy_binding_decisions_and_rejection_comments(self):
        payload=fe.build_fallback_evaluation_payload(self.fallback,self.source)
        self.assertEqual(len(payload['units']),3)
        self.assertEqual(payload['units'][1]['primary_translation'],'Hello\vworld')
        self.assertEqual(len(payload['units'][1]['translated_spans']),2)
        self.assertNotIn('DO_NOT_SEND_XML',json.dumps(payload))
        self.assertNotIn('rejected_translation',payload['units'][1])
        client=fixtures.FakeOpenAI()
        r=self.run_stage(client)
        self.assertEqual((r['evaluated'],r['accepted'],r['rejected']),(3,2,1))
        self.assertEqual(r['evaluations'][1]['feedback'],'Meaning omitted.')
        self.assertEqual(client.calls[0]['input'][0]['content'],ev.DEFAULT_SYSTEM_INSTRUCTION)
        request=json.loads(client.calls[0]['input'][1]['content'].split('\n\n',1)[1])
        self.assertNotIn('provenance',request)
        self.assertNotIn('diagnostic',request['metadata'])
        self.assertEqual([r['candidate_sha256'] for r in r['evaluations']], [u['candidate_sha256'] for u in payload['units']])
        state=fe._read(self.target)
        self.assertEqual(state['compatibility']['fallback_sha256'],pt._file_hash(self.fallback))
        self.assertEqual(state['stage'],fe.STAGE)
        self.assertEqual(r,fe.inspect_fallback_evaluation_checkpoint(self.target))
        self.assertFalse(any('final' in row for row in r['evaluations']))

    def test_caps_resume_skip_completed_and_changed_settings(self):
        first=fixtures.FakeOpenAI()
        r=self.run_stage(first,max_batches=0)
        self.assertEqual(len(r['pending_unit_keys']),3);self.assertEqual(first.calls,[])
        r=self.run_stage(first,resume=True,max_batches=1)
        self.assertEqual(r['evaluated'],3)
        self.run_stage(first,resume=True)
        self.assertEqual(len(first.calls),1)
        with self.assertRaises(ValueError):
            fe.run_fallback_evaluation(first,self.fallback,self.source,self.target,'different',resume=True)
        with self.assertRaises(FileExistsError):self.run_stage(first)

    def test_errors_strict_validation_and_explicit_resume(self):
        steps=[RuntimeError('offline'), 'not JSON', {'evaluations':[]},
               {'evaluations':[dict(unit_key='one',passed=False,feedback='')]},KeyboardInterrupt()]
        for step in steps:
            with self.subTest(step=step):
                self.target.unlink(missing_ok=True)
                client=fixtures.FakeOpenAI([step])
                with self.assertRaises(KeyboardInterrupt if isinstance(step,KeyboardInterrupt) else ev.EvaluationStageError):
                    self.run_stage(client)
                r=fe.inspect_fallback_evaluation_checkpoint(self.target)
                self.assertEqual(r['evaluations'],[]);self.assertEqual(len(r['error_unit_keys']),3)
                self.assertEqual(len(client.calls),1)
                r=self.run_stage(resume=True)
                self.assertEqual(len(r['batch_results'][0]['attempts']),2)

    def test_partial_generation_only_completed_candidates_and_errors(self):
        partial=self.folder/'partial.json'
        ft.run_fallback_translation(FakeClaude(),self.primary,self.output,self.source,partial,'fake',max_units=1,max_batches=1)
        payload=fe.build_fallback_evaluation_payload(partial,self.source)
        self.assertEqual([u['unit_key'] for u in payload['units']],['one'])
        self.assertEqual(payload['metadata']['fallback_pending_unit_keys'],['styled','three'])
        with self.assertRaises(ft.FallbackStageError):
            ft.run_fallback_translation(FakeClaude([RuntimeError('offline')]),self.primary,self.output,self.source,partial,'fake',max_units=1,resume=True)
        payload=fe.build_fallback_evaluation_payload(partial,self.source)
        self.assertEqual(payload['metadata']['fallback_error_unit_keys'],['styled'])
        self.assertEqual(payload['metadata']['fallback_pending_unit_keys'],['three'])

    def test_stale_candidate_and_span_response_rejected_even_with_new_checksum(self):
        self.run_stage(max_batches=0)
        original=ft._read(self.fallback)
        changed=deepcopy(original)
        changed['batches'][0]['candidates'][1]['fallback_span_translations'][0]['text']='Changed'
        bad=self.folder/'tampered.json';pt._write_checkpoint(bad,changed)
        with self.assertRaises(ValueError):fe.build_fallback_evaluation_payload(bad,self.source)
        # Change the raw response and matching candidate coherently: valid new
        # generation snapshot still cannot reuse decisions from the old snapshot.
        changed=deepcopy(original)
        attempt=changed['batches'][0]['attempts'][-1]
        attempt['response_json']['translations'][1]['translated_spans'][0]['text']='Changed'
        checked=ft.validate_fallback_response(attempt['response_json'],changed['payload'])
        self.assertTrue(checked['ok'])
        changed['batches'][0]['candidates']=checked['candidates']
        pt._write_checkpoint(bad,changed)
        client=fixtures.FakeOpenAI()
        with self.assertRaises(ValueError):
            fe.run_fallback_evaluation(client,bad,self.source,self.target,'fake-evaluator',resume=True)
        self.assertEqual(client.calls,[])
        state=fe._read(self.target)
        state['payload']['units'][1]['translated_spans'][0]['text']='Different'
        pt._write_checkpoint(self.target,state)
        with self.assertRaises(ValueError):fe.inspect_fallback_evaluation_checkpoint(self.target)

    def test_completed_decision_identity_tampering_and_source_mismatch(self):
        self.run_stage()
        state=fe._read(self.target)
        state['batches'][0]['evaluations'][0]['candidate_sha256']='stale'
        pt._write_checkpoint(self.target,state)
        with self.assertRaises(ValueError):self.run_stage(resume=True)
        source=self.folder/'other.pptx';source.write_bytes(b'other')
        with self.assertRaises(ValueError):fe.build_fallback_evaluation_payload(self.fallback,source)
        with self.assertRaises(ValueError):
            fe.run_fallback_evaluation(fixtures.FakeOpenAI(),self.fallback,self.source,self.fallback,'fake')

    def test_atomic_failure_and_lock(self):
        self.run_stage(max_batches=0)
        saved=self.target.read_bytes()
        with patch.object(pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.run_stage(resume=True)
        self.assertEqual(saved,self.target.read_bytes())
        lock=self.target.with_name(self.target.name+'.lock');lock.write_text('writer')
        with self.assertRaises(FileExistsError):self.run_stage(resume=True)




if __name__=='__main__':unittest.main()
