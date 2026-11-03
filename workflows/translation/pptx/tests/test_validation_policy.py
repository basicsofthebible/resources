"""Offline policy contract tests: synthetic candidates, fake providers, temporary outputs."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import validation_policy as vp
from pptx_translation import primary_translation as pt
from pptx_translation import evaluation as ev
from pptx_translation import fallback_translation as ft
from pptx_translation import fallback_evaluation as fe
from pptx_translation import finalization as fin
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import pptx_helpers as ph
from test_primary_translation import FakeGemini
from test_evaluation import FakeOpenAI
from test_fallback_translation import FakeClaude


def verdict(passed):
    return lambda payload:dict(evaluations=[dict(unit_key=u['unit_key'],passed=passed,
                            feedback='' if passed else 'Numerical meaning changed.') for u in payload['units']])


def response(payload,text):
    rows=[]
    for u in payload['units']:
        row=dict(unit_key=u['unit_key'],source_text=u['source_text'])
        if 'styled_spans' in u:
            row['translated_spans']=[dict(span_id=s['span_id'],text=text) for s in u['styled_spans'] if s['kind']=='text']
        else:row['translated_text']=text
        rows.append(row)
    return dict(translations=rows)


class ValidationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.source=self.folder/'source.pptx'
        self.source.write_bytes(b'offline source')
        self.primary=self.folder/'primary.json';self.evaluator=self.folder/'evaluation.json'
        self.units=ph.classify_pptx_text_units([dict(unit_key='book',text='1 Samuel 1-8',
            styled_spans=[dict(span_id='p01',kind='text',text='1 Samuel 1-8')])])

    def payload(self,policy=3):
        return pt.build_primary_payload(self.units,'English','Traditional Chinese',policy_version=policy)

    def primary_stage(self,text='撒母耳記上 1-8'):
        client=FakeGemini([lambda p:response(p,text)])
        pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese',
                                   'fake','Translate',self.primary)
        return client

    def test_localized_numbers_are_advisory_under_current_and_hard_under_legacy(self):
        for text in ('撒母耳記上 1-8','١ صموئيل ١-٨','१ शमूएल १-८','๑ ซามูเอล ๑-๘','Samuel one through eight'):
            with self.subTest(text=text):
                p=self.payload();r=pt.validate_primary_response(response(p,text),p)
                self.assertTrue(r['ok']);self.assertEqual(len(r['warnings_by_unit']['book']),1)
                w=r['warnings_by_unit']['book'][0]
                self.assertEqual(w['span_id'],'p01');self.assertEqual(w['source_numbers'],['1','1','8'])
                p=self.payload(2);r=pt.validate_primary_response(response(p,text),p)
                self.assertFalse(r['ok']);self.assertIn('numeric_markers_changed',' '.join(r['errors']))
                self.assertNotIn('warnings_by_unit',r)

    def test_wrong_number_is_warning_not_automatic_semantic_acceptance(self):
        p=self.payload();r=pt.validate_primary_response(response(p,'撒母耳記上 9-99'),p)
        self.assertTrue(r['ok']);self.assertEqual(r['warnings_by_unit']['book'][0]['translated_numbers'],['9','99'])
        self.primary_stage('撒母耳記上 9-99')
        ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        self.assertEqual(len(ft.build_fallback_payload(self.primary,self.evaluator,self.source)['units']),1)

    def test_hard_structural_checks_still_reject(self):
        p=self.payload()
        for mutate in (lambda r:r['translations'][0].update(source_text='other'),
                       lambda r:r['translations'][0].update(unit_key='other'),
                       lambda r:r['translations'][0]['translated_spans'][0].update(span_id='other'),
                       lambda r:r['translations'][0]['translated_spans'].append(dict(span_id='p01',text='x')),
                       lambda r:r['translations'][0]['translated_spans'][0].update(text=''),
                       lambda r:r['translations'][0]['translated_spans'][0].update(text='中文\x00'),
                       lambda r:r['translations'][0]['translated_spans'][0].update(text=' 中文')):
            r=response(p,'撒母耳記上 1-8');mutate(r)
            self.assertFalse(pt.validate_primary_response(r,p)['ok'])
        for before,after,code in [('Hello  world','中文 世界','structural_tokens_changed'),
                                  (' Hello ',' 中文','boundary_whitespace_changed')]:
            self.assertEqual(vp.text_error(before,after,policy_version=3),code)

    def test_declared_markers_remain_immutable(self):
        units=ph.classify_pptx_text_units([dict(unit_key='list',text='1. Hello')])
        p=pt.build_primary_payload(units,'English','Chinese')
        r=pt.validate_primary_response(response(p,'一、你好'),p)
        self.assertFalse(r['ok']);self.assertIn('number_prefix_or_suffix_changed',' '.join(r['errors']))
        p['units'][0]['number_prefix']='';p['units'][0]['number_suffix']=' 1'
        self.assertFalse(pt.validate_primary_response(response(p,'你好 一'),p)['ok'])

    def test_historical_fallback_response_contract_keeps_legacy_numeric_checks(self):
        payload=self.payload(2);payload['metadata']['response_contract_version']=1
        self.assertEqual(vp.payload_policy(payload),2)
        self.assertFalse(ft.validate_fallback_response(response(payload,'撒母耳記上 1-8'),payload)['ok'])

    def test_no_warning_when_digits_unchanged_and_unknown_policies_rejected(self):
        p=self.payload();r=pt.validate_primary_response(response(p,'撒母耳記 1 1-8'),p)
        self.assertTrue(r['ok']);self.assertEqual(r['warnings_by_unit']['book'],[])
        for version in (True,1,5,'3'):
            with self.assertRaises(ValueError):self.payload(version)

    def test_warning_does_not_retry_and_resume_reuses_exact_candidate(self):
        client=self.primary_stage();state=pt._read_checkpoint(self.primary)
        self.assertEqual(len(client.calls),1)
        record=state['unit_results']['book'];self.assertEqual(record['attempt_count'],1)
        candidate=record['candidate'];self.assertTrue(candidate['validation']['warnings'])
        pair={k:candidate[k] for k in ('unit_key','primary_translation','primary_span_translations')}
        self.assertEqual(candidate['candidate_sha256'],pt._digest(pair))
        before=self.primary.read_bytes();client=FakeGemini()
        pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese','fake','Translate',
                                   self.primary,resume=True)
        self.assertEqual(client.calls,[])
        self.assertEqual(pt._read_checkpoint(self.primary)['unit_results']['book']['candidate'],candidate)
        self.assertTrue(before)

    def test_warning_tampering_detected_even_with_recomputed_outer_hash(self):
        self.primary_stage();state=pt._read_checkpoint(self.primary)
        state['unit_results']['book']['candidate']['validation']['warnings']=[]
        pt._write_checkpoint(self.primary,state)
        with self.assertRaises(ValueError):pt._read_checkpoint(self.primary)

    def test_evaluator_receives_bound_warnings_and_new_policy_instruction(self):
        self.primary_stage();payload=ev.build_evaluation_payload(self.primary,self.source)
        self.assertEqual(payload['metadata']['validation_policy_version'],4)
        self.assertEqual(payload['units'][0]['validation_warnings'][0]['unit_key'],'book')
        client=FakeOpenAI([verdict(True)])
        ev.run_evaluation(client,self.primary,self.source,self.evaluator,'fake')
        self.assertEqual(ev._read(self.evaluator)['compatibility']['instruction'],ev.DEFAULT_SYSTEM_INSTRUCTION)

    def test_claude_recomputes_warnings_and_evaluation_uses_its_candidate(self):
        self.primary_stage()
        ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        payload=ft.build_fallback_payload(self.primary,self.evaluator,self.source)
        self.assertTrue(payload['units'][0]['rejected_validation_warnings'])
        self.assertNotIn('validation_warnings',payload['units'][0])
        fallback=self.folder/'fallback.json'
        ft.run_fallback_translation(FakeClaude([lambda p:response(p,'撒母耳記 1 1-8')]),
            self.primary,self.evaluator,self.source,fallback,'fake')
        saved=ft._read(fallback);candidate=saved['batches'][0]['candidates'][0]
        self.assertEqual(candidate['validation']['warnings'],[])
        p=fe.build_fallback_evaluation_payload(fallback,self.source)
        self.assertEqual(p['units'][0]['validation_warnings'],[])
        output=self.folder/'fallback_evaluation.json'
        fe.run_fallback_evaluation(FakeOpenAI([verdict(True)]),fallback,self.source,output,'fake')
        self.assertEqual(ev._read(output,fe.STAGE)['compatibility']['instruction'],ev.DEFAULT_SYSTEM_INSTRUCTION)

    def test_legacy_checkpoint_replay_and_resume_keep_original_contract(self):
        client=FakeGemini()
        with patch.object(pt,'POLICY_VERSION',2):
            pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese',
                                       'fake','Translate',self.primary)
        self.assertEqual(pt.primary_validation_policy(self.primary),2)
        payload=ev.build_evaluation_payload(self.primary,self.source)
        self.assertNotIn('validation_warnings',payload['units'][0])
        self.assertNotIn('validation_policy_version',payload['metadata'])
        client=FakeGemini()
        pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese',
                                   'fake','Translate',self.primary,resume=True)
        self.assertEqual(client.calls,[])
        ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        self.assertEqual(ev._read(self.evaluator)['compatibility']['instruction'],ev.LEGACY_SYSTEM_INSTRUCTION)
        fallback=self.folder/'fallback.json'
        ft._run_legacy(FakeClaude(),self.primary,self.evaluator,self.source,fallback,'fake')
        candidate=ft._read(fallback)['batches'][0]['candidates'][0]
        self.assertNotIn('validation',candidate)
        p=fe.build_fallback_evaluation_payload(fallback,self.source)
        self.assertNotIn('validation_warnings',p['units'][0])
        self.assertNotIn('validation_policy_version',p['metadata'])

    @unittest.skipUnless(ph.check_python_pptx_available()[0],'python-pptx required')
    def test_finalization_and_reconstruction_keep_text_and_warning(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(1)).text='1 Samuel 1-8'
        prs.save(self.source)
        self.units=ph.classify_pptx_text_units(ph.build_pptx_text_units(Presentation(self.source),
            pptx_path=self.source,include_styled_spans=True))
        self.primary_stage();ev.run_evaluation(FakeOpenAI([verdict(True)]),self.primary,self.source,self.evaluator,'fake')
        snapshot=fin.build_finalization_snapshot(self.primary,self.evaluator,self.source)
        self.assertEqual(snapshot['policy']['validation_policy_version'],4)
        selected=[u['selected_candidate'] for u in snapshot['units'] if u.get('selected_candidate')][0]
        self.assertTrue(selected['validation_warnings']);self.assertEqual(selected['text'],'撒母耳記上 1-8')
        files=fin.write_finalization_artifacts(snapshot,self.folder/'finalization')
        result=fr.reconstruct_finalized_snapshot(files['snapshot'],self.folder/'outputs',self.source)
        self.assertEqual(result['report']['status'],'passed')
        self.assertEqual(Presentation(result['output_path']).slides[0].shapes[0].text,'撒母耳記上 1-8')
        fr.validate_reconstruction_artifacts(result['report_path'],files['snapshot'],self.source)


if __name__=='__main__':unittest.main()
