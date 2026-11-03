"""Offline recovery regressions: fake providers, synthetic saved attempts, temp artifacts."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import primary_recovery as recovery
from pptx_translation import pptx_helpers as ph
from pptx_translation import evaluation as ev
from pptx_translation import fallback_translation as ft
from pptx_translation import fallback_evaluation as fe
from pptx_translation import finalization as fin
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import run_reporting as reporting
from pptx_translation.active_run import ActiveRun
from pptx_translation.workflow_config import WorkflowConfig
from test_primary_translation import FakeGemini
from test_fallback_translation import FakeClaude
from test_evaluation import FakeOpenAI


def translate(payload):
    rows=[]
    for u in payload['units']:
        text='撒母耳記上 1-8' if u['source_text']=='1 Samuel 1-8' else u['source_text'].replace('Hello','你好')
        r=dict(unit_key=u['unit_key'],source_text=u['source_text'])
        if 'styled_spans' in u:r['translated_spans']=[dict(span_id=s['span_id'],text=text) for s in u['styled_spans'] if s['kind']=='text']
        else:r['translated_text']=text
        rows.append(r)
    return dict(translations=rows)


def decision(passed):
    return lambda p:dict(evaluations=[dict(unit_key=u['unit_key'],passed=passed,
                        feedback='' if passed else 'Meaning changed.') for u in p['units']])


class PrimaryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source.pptx';self.source.write_bytes(b'offline source')
        self.units=ph.classify_pptx_text_units([dict(unit_key='good',text='Hello 1'),
            dict(unit_key='book',text='1 Samuel 1-8',styled_spans=[dict(span_id='p01',kind='text',text='1 Samuel 1-8')])])
        self.primary=self.root/'primary.json'
        self.generate()

    def generate(self):
        with patch.object(pt,'POLICY_VERSION',2):
            client=FakeGemini([translate]*3)
            pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese',
                'fake','Translate',self.primary)
        self.original=pt._read_checkpoint(self.primary);self.before=self.primary.read_bytes()

    def recover(self):
        result=recovery.recover(self.primary,self.source)
        return result,pt.read_primary_state(result['path'],self.source)

    def test_exact_third_attempt_overlay_preserves_candidates_history_and_accounting(self):
        result,state=self.recover()
        self.assertEqual(self.primary.read_bytes(),self.before)
        self.assertEqual(state['batches'],self.original['batches'])
        self.assertEqual(state['unit_results']['good'],self.original['unit_results']['good'])
        self.assertEqual(state['unit_results']['book']['attempt_count'],3)
        self.assertEqual(state['unit_results']['book']['request_ids'],['b1-a1','b1-a2','b1-a3'])
        self.assertEqual(state['unit_results']['book']['error'],self.original['unit_results']['book']['error'])
        event=state['offline_recovery']['events'][0]
        self.assertEqual(event['reference']['attempt'],3)
        self.assertEqual(event['reference']['request_id'],'b1-a3')
        self.assertEqual(event['reference']['original_sha256'],pt._file_hash(self.primary))
        self.assertEqual(len(event['candidate']['validation']['warnings']),1)
        self.assertEqual((reporting.stage_metrics(state)['submissions'],reporting.stage_metrics(state)['retries']),(3,2))
        self.assertEqual(len(pt.read_validated_candidates(result['path'],self.source)[1]),2)
        self.assertEqual(len(pt.read_validated_candidates(self.primary,self.source)[1]),1)

    def test_repeated_recovery_reuses_bytes_and_no_provider_is_needed(self):
        result,state=self.recover();path=Path(result['path']);before=path.read_bytes()
        with patch.object(pt,'_write_checkpoint',side_effect=AssertionError('unexpected write')):
            self.assertEqual(recovery.recover(self.primary,self.source),result)
        self.assertEqual(path.read_bytes(),before)
        client=FakeGemini()
        r=pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese','fake','Translate',path,resume=True)
        self.assertEqual(client.calls,[]);self.assertEqual(len(r['completed_unit_keys']),2)
        self.assertEqual(path.read_bytes(),before)
        with self.assertRaisesRegex(ValueError,'settings'):
            pt.run_primary_translation(client,self.units,self.source,'English','Traditional Chinese','other','Translate',path,resume=True)

    def test_tampered_event_attempt_raw_digest_candidate_warning_or_settings_rejected(self):
        result,_=self.recover();path=Path(result['path']);saved=path.read_bytes()
        for field in ('attempt','request_id','raw_response_sha256','response_row_index','request_hash'):
            data=json.loads(saved);data.pop('integrity');data['events'][0]['reference'][field]='wrong'
            pt._write_checkpoint(path,data)
            with self.assertRaises(ValueError):pt.read_primary_state(path,self.source)
            path.write_bytes(saved)
        for edit in (lambda a:a['events'][0]['candidate'].update(candidate_sha256='wrong'),
                     lambda a:a['events'][0]['candidate']['validation'].update(warnings=[]),
                     lambda a:a['original_settings'].update(model='wrong'),
                     lambda a:a.update(events=[])):
            data=json.loads(saved);data.pop('integrity');edit(data);pt._write_checkpoint(path,data)
            with self.assertRaises(ValueError):pt.read_primary_state(path,self.source)
            path.write_bytes(saved)

    def test_corrupt_or_changed_original_and_wrong_source_rejected(self):
        result,_=self.recover()
        self.primary.write_bytes(self.before+b' ')
        with self.assertRaises(ValueError):pt.read_primary_state(result['path'],self.source)
        self.primary.write_bytes(self.before)
        state=deepcopy(self.original);state['batches'][0]['attempts'][2]['raw_response']='{}'
        pt._write_checkpoint(self.primary,state)
        with self.assertRaises(ValueError):recovery.recover(self.primary,self.source)
        self.primary.write_bytes(self.before)
        self.source.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError,'source'):pt.read_primary_state(result['path'],self.source)

    def test_remaining_hard_failure_is_reported_without_publication(self):
        # Build genuine frozen history whose final response has both digit and whitespace errors.
        self.primary.unlink()
        def invalid(p):
            r=translate(p)
            for row in r['translations']:
                if row['unit_key']=='book':row['translated_spans'][0]['text']=' 撒母耳記上 1-8'
            return r
        with patch.object(pt,'POLICY_VERSION',2):
            pt.run_primary_translation(FakeGemini([invalid]*3),self.units,self.source,'English','Traditional Chinese',
                                       'fake','Translate',self.primary)
        with self.assertRaisesRegex(recovery.RecoveryValidationError,'boundary_whitespace_changed'):
            recovery.recover(self.primary,self.source)
        self.assertEqual(list(self.root.glob('primary_recovery*.json')),[])

    def test_policy3_or_unexhausted_checkpoints_not_recovery_inputs(self):
        with self.assertRaises(ValueError):recovery.recover(self.source,self.source)
        self.primary.unlink()
        pt.run_primary_translation(FakeGemini([translate]),self.units,self.source,'English','Traditional Chinese',
                                   'fake','Translate',self.primary)
        with self.assertRaisesRegex(ValueError,'policy-2'):recovery.recover(self.primary,self.source)

    def test_evaluation_and_claude_eligibility_bind_overlay_hash_and_advisories(self):
        result,state=self.recover();payload=ev.build_evaluation_payload(result['path'],self.source)
        self.assertEqual(payload['metadata']['unit_count'],2)
        self.assertEqual(payload['metadata']['primary_uncompleted_units'],0)
        self.assertEqual(payload['units'][0]['candidate_sha256'],self.original['unit_results']['good']['candidate']['candidate_sha256'])
        self.assertEqual(payload['units'][0]['validation_warnings'],[])
        self.assertEqual(len(payload['units'][1]['validation_warnings']),1)
        evaluator=self.root/'evaluation.json'
        ev.run_evaluation(FakeOpenAI([decision(False)]),result['path'],self.source,evaluator,'fake')
        self.assertEqual(ev._read(evaluator)['compatibility']['primary_sha256'],result['sha256'])
        p=ft.build_fallback_payload(result['path'],evaluator,self.source)
        self.assertEqual(len(p['units']),2);self.assertEqual(p['excluded']['primary_validation_errors'],[])
        fallback=self.root/'fallback.json'
        ft.run_fallback_translation(FakeClaude([translate]),result['path'],evaluator,self.source,fallback,'fake')
        self.assertEqual(ft._read(fallback)['compatibility']['primary_sha256'],result['sha256'])
        p=fe.build_fallback_evaluation_payload(fallback,self.source)
        self.assertEqual(len(p['units']),2)

    def managed(self):
        config=WorkflowConfig(input_path=self.source,output_root=self.root/'outputs',target_language='Traditional Chinese')
        run=ActiveRun.create(config,self.units,True)
        path,_=run.prepare('primary');path.write_bytes(self.before)
        run.record('primary',path)
        return run,config


    def test_cached_manifest_validation_still_checks_original_ancestry(self):
        run,config=self.managed();original=run.path('primary')
        run.recover_primary();run.sync()
        original.write_bytes(original.read_bytes()+b' ')
        with self.assertRaises(ValueError):run.path('primary')

    def test_recovery_saved_before_manifest_registration_is_reused_on_restart(self):
        run,config=self.managed()
        result=recovery.recover(run.path('primary'),self.source)
        with patch.object(run,'record',side_effect=OSError('manifest disk')):
            with self.assertRaises(OSError):run.recover_primary()
        again=ActiveRun.open(run.manifest_path,config,True)
        self.assertEqual(again.recover_primary(),result)
        self.assertEqual(len(list((run.manifest_path.parent/'checkpoints').glob('primary_recovery*.json'))),1)

    def test_registering_recovery_invalidates_prior_evaluation_without_deleting_it(self):
        run,config=self.managed()
        p,_=run.prepare('evaluation');ev.run_evaluation(FakeOpenAI([decision(True)]),run.path('primary'),self.source,p,'fake')
        run.record('evaluation',p);before=p.read_bytes()
        result=run.recover_primary()
        self.assertIsNone(run.path('evaluation'));self.assertEqual(p.read_bytes(),before)
        new,resume=run.prepare('evaluation');self.assertFalse(resume);self.assertNotEqual(p,new)
        self.assertEqual(run.state['stages']['primary']['sha256'],result['sha256'])

    def test_interrupted_artifact_publication_can_retry_without_touching_original(self):
        with patch.object(pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.recover()
        self.assertEqual(self.primary.read_bytes(),self.before)
        self.assertEqual(list(self.root.glob('*.lock')),[])
        self.recover()

    @unittest.skipUnless(ph.check_python_pptx_available()[0],'python-pptx required')
    def test_full_fake_evaluation_finalization_and_reconstruction_ancestry(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
        for y,text in ((1,'Hello 1'),(3,'1 Samuel 1-8')):
            slide.shapes.add_textbox(Inches(1),Inches(y),Inches(6),Inches(1)).text=text
        prs.save(self.source)
        self.units=ph.classify_pptx_text_units(ph.build_pptx_text_units(Presentation(self.source),pptx_path=self.source,include_styled_spans=True))
        self.primary.unlink();self.generate()
        run,config=self.managed();result=run.recover_primary()
        p,_=run.prepare('evaluation');ev.run_evaluation(FakeOpenAI([decision(True)]),run.path('primary'),self.source,p,'fake')
        run.record('evaluation',p)
        files=run.finalize();snapshot=fr.read_validated_snapshot(files['snapshot'],self.source)['snapshot']
        self.assertTrue(snapshot['summary']['translation_complete'])
        self.assertEqual(snapshot['primary_offline_recovery']['artifact_sha256'],result['sha256'])
        self.assertEqual(snapshot['checkpoint_histories']['primary'],self.original['batches'])
        output=run.reconstruct();run.record('reconstruction',output['report_path'])
        self.assertEqual(output['report']['status'],'passed')
        fr.validate_reconstruction_artifacts(output['report_path'],files['snapshot'],self.source)
        self.assertIn('撒母耳記上 1-8',[s.text for s in Presentation(output['output_path']).slides[0].shapes])
        ActiveRun.open(run.manifest_path,config,True)


if __name__=='__main__':unittest.main()
