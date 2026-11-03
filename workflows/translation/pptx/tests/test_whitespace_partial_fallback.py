"""Policy 4 and Claude schema 2: fully offline, synthetic units and temporary PPTX."""
from copy import deepcopy
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pptx_translation import validation_policy as vp
from pptx_translation import primary_translation as pt
from pptx_translation import fallback_translation as ft
from pptx_translation import fallback_checkpoint as fc
from pptx_translation import fallback_evaluation as fe
from pptx_translation import evaluation as ev
from pptx_translation import finalization as fin
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import pptx_helpers as ph
from pptx_translation import run_reporting as reporting
from pptx_translation.active_run import ActiveRun
from pptx_translation.workflow_config import WorkflowConfig
from test_primary_translation import FakeGemini,make_response
from test_fallback_translation import FakeClaude
from test_evaluation import FakeOpenAI


def verdict(passed):
    return lambda p:dict(evaluations=[dict(unit_key=u['unit_key'],passed=passed,feedback='' if passed else 'Meaning needs correction.') for u in p['units']])


def answer(payload):
    return dict(translations=[dict(unit_key=u['unit_key'],source_text=u['source_text'],
        translated_spans=[dict(span_id=s['span_id'],text=s['text'].replace('Hello','你好')) for s in u['styled_spans'] if s['kind']=='text'])
        if 'styled_spans' in u else dict(unit_key=u['unit_key'],source_text=u['source_text'],translated_text=u['source_text'].replace('Hello','你好'))
        for u in payload['units']])


class WhitespaceTests(unittest.TestCase):
    def payload(self,label='Hezekiah: ',policy=4):
        units=ph.classify_pptx_text_units([dict(unit_key='label',text=label+'2 Kings 19-20',styled_spans=[
            dict(span_id='p01',kind='text',text=label),dict(span_id='p02',kind='text',text='2 Kings 19-20')])])
        return pt.build_primary_payload(units,'English','Traditional Chinese',policy_version=policy)

    def test_three_actual_patterns_allow_ordinary_space_without_repair(self):
        for source,target in [('South (Judah): ','南國（猶大）：'),('Hezekiah: ','希西家：'),('Isaiah: ','以賽亞：')]:
            for policy in (2,3,4):
                p=self.payload(source,policy);r=answer(p);r['translations'][0]['translated_spans'][0]['text']=target
                checked=pt.validate_primary_response(r,p)
                self.assertEqual(checked['ok'],policy==4)
                if policy==4:self.assertEqual(checked['translations'][0]['primary_span_translations'][0]['text'],target)
                else:self.assertIn('boundary_whitespace_changed',' '.join(checked['errors']))

    def test_language_independent_text_spacing_and_combining_characters(self):
        p=self.payload()
        for text in ('希西家：','حزقيا:','हिजकिय्याह:','Hezekiah:',' Hezekiah :  '):
            r=answer(p);r['translations'][0]['translated_spans'][0]['text']=text
            checked=pt.validate_primary_response(r,p)
            self.assertTrue(checked['ok'],checked)
            self.assertEqual(checked['translations'][0]['primary_span_translations'][0]['text'],text)

    def test_significant_unicode_cannot_disappear_appear_or_move_off_boundary(self):
        for char in ('\u00a0','\u202f','\u3000','\u200e','\u200f','\u061c','\u200d'):
            for source,target in ((char+'Hello', '你好'),('Hello',char+'你好'),(char+'Hello','你好'+char)):
                self.assertEqual(vp.text_error(source,target,policy_version=4),'significant_unicode_changed')
            self.assertIsNone(vp.text_error(char+'Hello',char+'你好',policy_version=4))

    def test_fixed_whitespace_tabs_soft_breaks_ids_and_markers_stay_protected(self):
        units=ph.classify_pptx_text_units([dict(unit_key='x',text='1. Hello  \t\vworld',styled_spans=[
            dict(span_id='p01',kind='text',text='1. Hello'),dict(span_id='p02',kind='fixed_whitespace',text='  '),
            dict(span_id='p03',kind='tab',text='\t'),dict(span_id='p04',kind='soft_break',text='\v'),
            dict(span_id='p05',kind='text',text='world')])])
        p=pt.build_primary_payload(units,'English','Chinese');r=answer(p)
        self.assertTrue(pt.validate_primary_response(r,p)['ok'])
        self.assertIn('  \t\v',pt.validate_primary_response(r,p)['translations'][0]['primary_translation'])
        for edit in (lambda a:a['translations'][0]['translated_spans'].append(dict(span_id='p02',text=' ')),
                     lambda a:a['translations'][0]['translated_spans'].reverse(),
                     lambda a:a['translations'][0]['translated_spans'][0].update(text='一、你好'),
                     lambda a:a['translations'][0]['translated_spans'][1].update(text='\tworld'),
                     lambda a:a['translations'][0]['translated_spans'][1].update(text=' ')):
            changed=deepcopy(r);edit(changed);self.assertFalse(pt.validate_primary_response(changed,p)['ok'])

    def test_provider_contracts_are_aligned_and_historical_contracts_frozen(self):
        p=self.payload();contract=p['metadata']['response_contract']['structure']
        self.assertIn('U+0020',contract);self.assertNotIn('exact leading/trailing',contract)
        self.assertIn('U+0020',ft.DEFAULT_SYSTEM_INSTRUCTION)
        self.assertIn('exact leading/trailing',self.payload(policy=3)['metadata']['response_contract']['structure'])
        self.assertIn('change boundary whitespace',ft.POLICY3_SYSTEM_INSTRUCTION)


class PartialFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.source=self.folder/'source.pptx';self.source.write_bytes(b'fake source')
        self.units=ph.classify_pptx_text_units([dict(unit_key=k,text='Hello '+str(i),styled_spans=[
            dict(span_id='p01',kind='text',text='Hello '),dict(span_id='p02',kind='text',text=str(i))]) for i,k in enumerate(('a','b','c','d','e'),1)])
        self.primary=self.folder/'primary.json';self.evaluator=self.folder/'evaluation.json';self.fallback=self.folder/'fallback.json'
        pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Spanish','fake','Translate',self.primary)
        ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')

    def run_stage(self,client=None,**kwargs):
        return ft.run_fallback_translation(client or FakeClaude(),self.primary,self.evaluator,self.source,self.fallback,'fake',**kwargs)

    @staticmethod
    def mixed(p):
        r=answer(p)
        r['translations'][1]['translated_spans'].pop()  # atomic invalid paragraph
        r['translations'].pop(2)  # missing c
        return r

    def test_mixed_missing_duplicate_and_partial_resume_retains_valid_candidates(self):
        client=FakeClaude([self.mixed]);r=self.run_stage(client)
        self.assertEqual([u['unit_key'] for u in r['candidates']],['a','d','e'])
        self.assertEqual(r['missing_unit_keys'],['c']);self.assertEqual(r['invalid_unit_keys'],['b'])
        before=ft._read(self.fallback);self.assertEqual(len(client.calls),1)
        later=FakeClaude();self.run_stage(later,resume=True)
        request=json.loads(later.calls[0]['messages'][0]['content'].split('\n\n',1)[1])
        self.assertEqual([u['unit_key'] for u in request['units']],['b','c'])
        state=ft._read(self.fallback)
        self.assertEqual(state['batches'][0]['attempts'][0],before['batches'][0]['attempts'][0])
        for key in ('a','d','e'):self.assertEqual(state['unit_results'][key],before['unit_results'][key])
        self.assertEqual((reporting.stage_metrics(state)['submissions'],reporting.stage_metrics(state)['retries']),(2,1))
        never=FakeClaude();self.run_stage(never,resume=True);self.assertEqual(never.calls,[])

    def test_duplicate_only_invalidates_ambiguous_paragraph(self):
        def duplicate(p):
            r=answer(p);r['translations'].append(deepcopy(r['translations'][0]));return r
        self.run_stage(FakeClaude([duplicate]))
        state=ft._read(self.fallback)
        self.assertEqual(state['unit_results']['a']['status'],'invalid')
        self.assertEqual(len(state['batches'][0]['candidates']),4)

    def test_partial_validation_does_not_block_next_batch_or_retry_automatically(self):
        def partial(p):
            r=answer(p);r['translations'][0]['translated_spans'].pop();return r
        client=FakeClaude([partial,answer,answer]);self.run_stage(client,max_units=2)
        self.assertEqual(len(client.calls),3)
        state=ft._read(self.fallback)
        self.assertEqual([len(b['attempts']) for b in state['batches']],[1,1,1])
        self.assertEqual(len([v for v in state['unit_results'].values() if v['status']=='validated']),4)

    def test_provider_failure_keeps_previously_accepted_candidates(self):
        self.run_stage(FakeClaude([self.mixed]));state=ft._read(self.fallback)
        with self.assertRaises(ft.FallbackStageError):self.run_stage(FakeClaude([RuntimeError('offline')]),resume=True)
        after=ft._read(self.fallback)
        for key in ('a','d','e'):self.assertEqual(after['unit_results'][key],state['unit_results'][key])
        for key in ('b','c'):self.assertEqual(after['unit_results'][key]['status'],'provider_error')

    def test_saved_raw_response_replays_after_commit_failure_without_another_call(self):
        real=pt._write_checkpoint
        def fail(path,state):
            if state.get('stage')=='claude_fallback' and state['batches'][0]['attempts'] and state['batches'][0]['attempts'][-1]['status']=='success':raise OSError('disk')
            return real(path,state)
        with patch.object(pt,'_write_checkpoint',side_effect=fail):
            with self.assertRaises(OSError):self.run_stage()
        state=ft._read(self.fallback);self.assertEqual(state['batches'][0]['attempts'][0]['status'],'response_saved')
        client=FakeClaude();self.run_stage(client,resume=True);self.assertEqual(client.calls,[])
        self.assertEqual(reporting.stage_metrics(ft._read(self.fallback))['submissions'],1)

    def test_tampered_raw_text_retry_subset_candidate_and_provenance_rejected(self):
        self.run_stage(FakeClaude([self.mixed]));self.run_stage(resume=True);original=ft._read(self.fallback)
        for mutate in (lambda s:s['batches'][0]['attempts'][0].update(raw_response='{}'),
                       lambda s:s['batches'][0]['attempts'][1].update(unit_keys=['a','b','c']),
                       lambda s:s['unit_results']['a']['candidate'].update(candidate_sha256='bad'),
                       lambda s:s['unit_results']['a']['candidate']['provenance'].update(request_hash='bad'),
                       lambda s:s['batches'][0]['attempts'][0]['validation'].update(candidates=[])):
            state=deepcopy(original);mutate(state);pt._write_checkpoint(self.fallback,state)
            with self.assertRaises(ValueError):ft._read(self.fallback)

    def test_fallback_evaluation_and_finalization_accept_partial_batch_candidates(self):
        self.run_stage(FakeClaude([self.mixed]))
        payload=fe.build_fallback_evaluation_payload(self.fallback,self.source)
        self.assertEqual([u['unit_key'] for u in payload['units']],['a','d','e'])
        self.assertEqual(payload['metadata']['fallback_error_unit_keys'],['b','c'])
        evaluator=self.folder/'fallback_evaluation.json'
        fe.run_fallback_evaluation(FakeOpenAI([verdict(True)]),self.fallback,self.source,evaluator,'fake')
        with patch.object(fin,'_source_units',return_value=deepcopy(self.units)):
            snapshot=fin.build_finalization_snapshot(self.primary,self.evaluator,self.source,
                fallback_checkpoint=self.fallback,fallback_evaluation_checkpoint=evaluator)
        self.assertEqual(snapshot['summary']['counts']['accepted_fallback'],3)
        self.assertEqual(snapshot['summary']['counts']['failed'],2)
        self.assertEqual(snapshot['summary']['counts']['pending'],0)

    def test_active_run_handoffs_partial_candidates_and_invalidates_changed_branches(self):
        config=WorkflowConfig(input_path=self.source,output_root=self.folder/'runs',target_language='Spanish')
        run=ActiveRun.create(config,self.units,True)
        for stage,path in (('primary',self.primary),('evaluation',self.evaluator)):
            owned,_=run.prepare(stage);owned.write_bytes(path.read_bytes());run.record(stage,owned)
        owned,_=run.prepare('fallback')
        ft.run_fallback_translation(FakeClaude([self.mixed]),run.path('primary'),run.path('evaluation'),self.source,owned,'fake')
        run.record('fallback',owned)
        evaluator,_=run.prepare('fallback_evaluation')
        fe.run_fallback_evaluation(FakeOpenAI([verdict(True)]),owned,self.source,evaluator,'fake')
        run.record('fallback_evaluation',evaluator)
        with patch.object(fin,'_source_units',return_value=deepcopy(self.units)):
            files=run.finalize()
            records=reporting.load_run(run.manifest_path)['records']
            self.assertEqual(reporting.unit_counts(records)['accepted_fallback'],3)
            self.assertEqual(reporting.unit_counts(records)['failed'],2)
            old=evaluator.read_bytes()
            ft.run_fallback_translation(FakeClaude(),run.path('primary'),run.path('evaluation'),self.source,owned,'fake',resume=True)
            run.record('fallback',owned)
        self.assertIsNone(run.path('fallback_evaluation'));self.assertIsNone(run.path('finalization'))
        self.assertEqual(evaluator.read_bytes(),old);self.assertTrue(Path(files['snapshot']).is_file())

    def test_v1_policy3_resume_does_not_reinterpret_saved_whitespace_failure(self):
        self.primary.unlink()
        with patch.object(pt,'POLICY_VERSION',3):
            pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Spanish','fake','Translate',self.primary)
        self.evaluator.unlink();ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        def boundary(p):
            r=answer(p);r['translations'][0]['translated_spans'][0]['text']='你好';return r
        with self.assertRaises(ft.FallbackStageError):
            ft._run_legacy(FakeClaude([boundary]),self.primary,self.evaluator,self.source,self.fallback,'fake')
        before=ft._read(self.fallback);self.assertEqual(before['schema_version'],1)
        self.assertEqual(before['batches'][0]['candidates'],[])
        ft.run_fallback_translation(FakeClaude(),self.primary,self.evaluator,self.source,self.fallback,'fake',resume=True)
        after=ft._read(self.fallback)
        self.assertEqual(after['schema_version'],1)
        self.assertEqual(after['batches'][0]['attempts'][0],before['batches'][0]['attempts'][0])
        self.assertEqual(pt.vp.payload_policy(after['payload']),3)

    def test_fresh_claude_policy4_can_follow_policy3_primary(self):
        self.primary.unlink()
        with patch.object(pt,'POLICY_VERSION',3):
            pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Spanish','fake','Translate',self.primary)
        self.evaluator.unlink();ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        self.run_stage();state=ft._read(self.fallback)
        self.assertEqual(state['schema_version'],2);self.assertEqual(vp.payload_policy(state['payload']),4)

    @unittest.skipUnless(ph.check_python_pptx_available()[0],'python-pptx required')
    def test_selected_claude_policy4_reconstruction_with_policy3_primary_and_reuse(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
        box=slide.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(1))
        p=box.text_frame.paragraphs[0]
        p.add_run().text='Hezekiah: ';run=p.add_run();run.text='2 Kings 19-20';run.font.bold=True
        prs.save(self.source)
        self.units=ph.classify_pptx_text_units(ph.build_pptx_text_units(Presentation(self.source),pptx_path=self.source,include_styled_spans=True))
        self.primary.unlink()
        with patch.object(pt,'POLICY_VERSION',3):pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Chinese','fake','Translate',self.primary)
        self.evaluator.unlink();ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        def corrected(p):
            u=p['units'][0]
            return dict(translations=[dict(unit_key=u['unit_key'],source_text=u['source_text'],translated_spans=[
                dict(span_id='p01',text='希西家：'),dict(span_id='p02',text='列王紀下 19-20')])])
        self.run_stage(FakeClaude([corrected]));evaluator=self.folder/'claude_evaluation.json'
        fe.run_fallback_evaluation(FakeOpenAI([verdict(True)]),self.fallback,self.source,evaluator,'fake')
        snapshot=fin.build_finalization_snapshot(self.primary,self.evaluator,self.source,fallback_checkpoint=self.fallback,fallback_evaluation_checkpoint=evaluator)
        selected=snapshot['units'][0]['selected_candidate']
        self.assertEqual(selected['validation_policy_version'],4);self.assertEqual(snapshot['policy']['validation_policy_version'],3)
        files=fin.write_finalization_artifacts(snapshot,self.folder/'finalization')
        result=fr.reconstruct_finalized_snapshot(files['snapshot'],self.folder/'outputs',self.source)
        self.assertEqual(result['report']['status'],'passed')
        out=Presentation(result['output_path']);self.assertEqual(out.slides[0].shapes[0].text,'希西家：列王紀下 19-20')
        self.assertTrue(out.slides[0].shapes[0].text_frame.paragraphs[0].runs[1].font.bold)
        fr.validate_reconstruction_artifacts(result['report_path'],files['snapshot'],self.source)


if __name__=='__main__':unittest.main()
