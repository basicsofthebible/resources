"""Offline Claude recovery: synthetic immutable histories, fake clients, temp outputs."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import fallback_translation as ft
from pptx_translation import fallback_checkpoint as fc
from pptx_translation import fallback_recovery as recovery
from pptx_translation import fallback_evaluation as fe
from pptx_translation import evaluation as ev
from pptx_translation import finalization as fin
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import pptx_helpers as ph
from pptx_translation import run_reporting as reporting
from pptx_translation.active_run import ActiveRun
from pptx_translation.workflow_config import WorkflowConfig
from test_primary_translation import FakeGemini
from test_fallback_translation import FakeClaude
from test_evaluation import FakeOpenAI


def verdict(passed):
    return lambda p:dict(evaluations=[dict(unit_key=u['unit_key'],passed=passed,
            feedback='' if passed else 'Meaning needs correction.') for u in p['units']])


def saved_response(p):
    rows=[]
    for i,u in enumerate(p['units']):
        values=[]
        for s in u['styled_spans']:
            if s['kind']!='text':continue
            value=s['text'].replace('Hello','你好')
            if u['source_text'].startswith(('South','Hezekiah','Isaiah')):
                value={'p01':'標籤：','p02':'列王紀下 19-20'}[s['span_id']]
            values.append(dict(span_id=s['span_id'],text=value))
        rows.append(dict(unit_key=u['unit_key'],source_text=u['source_text'],translated_spans=values))
    return dict(translations=rows)


class FallbackRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);self.source=self.folder/'source.pptx';self.source.write_bytes(b'offline source')
        self.units=ph.classify_pptx_text_units([dict(unit_key='k'+str(i),text=text,
            styled_spans=[dict(span_id='p01',kind='text',text=text)]) for i,text in enumerate(('Hello 1','Hello 2'))]+[
            dict(unit_key='k'+str(i),text=label+'2 Kings 19-20',styled_spans=[
                dict(span_id='p01',kind='text',text=label),dict(span_id='p02',kind='text',text='2 Kings 19-20')])
            for i,label in enumerate(('South (Judah): ','Hezekiah: ','Isaiah: '),2)]+[
            dict(unit_key='k'+str(i),text='Hello '+str(i),styled_spans=[dict(span_id='p01',kind='text',text='Hello '+str(i))]) for i in (5,6)])
        self.primary=self.folder/'primary.json';self.evaluator=self.folder/'evaluation.json';self.fallback=self.folder/'fallback.json'
        self.generate()

    def generate(self):
        with patch.object(pt,'POLICY_VERSION',3):
            pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Chinese','fake','Translate',self.primary)
        ev.run_evaluation(FakeOpenAI([verdict(False)]),self.primary,self.source,self.evaluator,'fake')
        with self.assertRaises(ft.FallbackStageError):
            ft._run_legacy(FakeClaude([saved_response]),self.primary,self.evaluator,self.source,self.fallback,'fake',max_batches=1)
        self.original=self.fallback.read_bytes();self.old=ft._read(self.fallback)

    def recover(self):
        result=recovery.recover(self.fallback,self.source)
        return result,ft._read(result['path'])

    def test_exact_text_policies_histories_and_submission_counts(self):
        result,state=self.recover()
        self.assertEqual(self.fallback.read_bytes(),self.original)
        self.assertEqual(state['historical_batches'],self.old['batches'])
        self.assertEqual(len(state['fallback_offline_recovery']['events']),5)
        self.assertEqual((reporting.stage_metrics(state)['submissions'],reporting.stage_metrics(state)['retries']),(1,0))
        self.assertEqual((reporting.stage_metrics(state)['completed'],reporting.stage_metrics(state)['pending']),(5,2))
        rows={r['unit_key']:r for r in self.old['batches'][0]['attempts'][0]['response_json']['translations']}
        for i in range(5):
            key='k'+str(i);c=state['unit_results'][key]['candidate']
            self.assertEqual(c['fallback_span_translations'],rows[key]['translated_spans'])
            self.assertEqual(c['validation']['policy_version'],3 if i<2 else 4)
            pair={k:c[k] for k in ('unit_key','fallback_translation','fallback_span_translations')}
            self.assertEqual(c['candidate_sha256'],pt._digest(pair))
            self.assertEqual(state['unit_results'][key]['attempt_count'],1)
        for key in ('k5','k6'):
            self.assertEqual(state['unit_results'][key]['status'],'pending')
            self.assertEqual(state['unit_results'][key]['attempt_count'],0)

    def test_repeated_recovery_reuses_artifact_bytes_without_writes(self):
        result,_=self.recover();before=Path(result['path']).read_bytes()
        with patch.object(pt,'_write_checkpoint',side_effect=AssertionError('unexpected write')):
            self.assertEqual(recovery.recover(self.fallback,self.source),result)
        self.assertEqual(Path(result['path']).read_bytes(),before)
        self.assertEqual(len(list(self.folder.glob('fallback_recovery_*.json'))),1)

    def test_wrong_attempt_candidate_digest_policy_and_warning_rejected(self):
        result,_=self.recover();path=Path(result['path']);before=path.read_bytes()
        edits=(lambda s:s['events'][0]['reference'].update(attempt=2),
               lambda s:s['events'][0]['reference'].update(raw_response_sha256='wrong'),
               lambda s:s['events'][0]['candidate'].update(candidate_sha256='wrong'),
               lambda s:s['events'][2]['candidate']['validation'].update(policy_version=3),
               lambda s:s['events'][2]['candidate']['validation'].update(warnings=[]),
               lambda s:s['original_settings'].update(model='other'),lambda s:s.update(events=[]))
        for edit in edits:
            data=json.loads(before);data.pop('integrity');edit(data);pt._write_checkpoint(path,data)
            with self.assertRaises(ValueError):ft._read(path)
            path.write_bytes(before)

    def test_changed_original_raw_response_or_upstream_rejected(self):
        result,_=self.recover()
        self.fallback.write_bytes(self.original+b' ')
        with self.assertRaises(ValueError):ft._read(result['path'])
        self.fallback.write_bytes(self.original)
        data=deepcopy(self.old);data['batches'][0]['attempts'][0]['raw_response']='{}';pt._write_checkpoint(self.fallback,data)
        with self.assertRaises(ValueError):recovery.recover(self.fallback,self.source)
        self.fallback.write_bytes(self.original)
        self.evaluator.write_bytes(self.evaluator.read_bytes()+b' ')
        with self.assertRaises(ValueError):ft._read(result['path'])

    def test_incorrect_historical_request_hash_and_settings_rejected(self):
        for edit in (lambda s:s['batches'][0].update(request_hash='wrong'),lambda s:s['compatibility'].update(max_units=4)):
            data=deepcopy(self.old);edit(data);pt._write_checkpoint(self.fallback,data)
            with self.assertRaises(ValueError):recovery.recover(self.fallback,self.source)
            self.fallback.write_bytes(self.original)

    def test_remaining_hard_failure_aborts_without_publishing_recovery(self):
        self.fallback.unlink()
        def unsafe(p):
            r=saved_response(p);r['translations'][2]['translated_spans'][0]['text']='標籤：\u00a0';return r
        with self.assertRaises(ft.FallbackStageError):
            ft._run_legacy(FakeClaude([unsafe]),self.primary,self.evaluator,self.source,self.fallback,'fake',max_batches=1)
        with self.assertRaisesRegex(recovery.RecoveryValidationError,'significant_unicode_changed'):
            recovery.recover(self.fallback,self.source)
        self.assertEqual(list(self.folder.glob('fallback_recovery_*.json')),[])

    def test_evaluation_payload_contains_five_bound_candidates_and_two_pending(self):
        result,state=self.recover();payload=fe.build_fallback_evaluation_payload(result['path'],self.source)
        self.assertEqual([u['unit_key'] for u in payload['units']],['k0','k1','k2','k3','k4'])
        self.assertEqual(payload['metadata']['fallback_pending_unit_keys'],['k5','k6'])
        self.assertEqual(payload['metadata']['fallback_error_unit_keys'],[])
        self.assertEqual([u['validation_policy_version'] for u in payload['units']],[3,3,4,4,4])
        self.assertEqual(sum(len(u['validation_warnings']) for u in payload['units']),3)

    def test_resume_uses_separate_child_and_requests_only_unsubmitted_units(self):
        result,state=self.recover();before=Path(result['path']).read_bytes();client=FakeClaude()
        completed=ft.run_fallback_translation(client,self.primary,self.evaluator,self.source,result['path'],'fake',resume=True)
        child=Path(completed['checkpoint_path']);self.assertNotEqual(child,Path(result['path']))
        request=json.loads(client.calls[0]['messages'][0]['content'].split('\n\n',1)[1])
        self.assertEqual([u['unit_key'] for u in request['units']],['k5','k6'])
        after=ft._read(child)
        for i in range(5):self.assertEqual(after['unit_results']['k'+str(i)],state['unit_results']['k'+str(i)])
        self.assertEqual((reporting.stage_metrics(after)['submissions'],reporting.stage_metrics(after)['retries']),(2,0))
        self.assertEqual(Path(result['path']).read_bytes(),before);self.assertEqual(self.fallback.read_bytes(),self.original)
        empty=FakeClaude()
        ft.run_fallback_translation(empty,self.primary,self.evaluator,self.source,result['path'],'fake',resume=True)
        self.assertEqual(empty.calls,[])

    def test_continuation_replays_durable_response_without_repeating_submission(self):
        result,state=self.recover();child=recovery.continuation(result['path']);real=pt._write_checkpoint
        def fail(path,state):
            if state.get('offline_recovery_parent') and any(b['attempts'] and b['attempts'][-1]['status']=='success' for b in state['batches']):raise OSError('disk')
            return real(path,state)
        with patch.object(pt,'_write_checkpoint',side_effect=fail):
            with self.assertRaises(OSError):ft.run_fallback_translation(FakeClaude(),self.primary,self.evaluator,self.source,child,'fake',resume=True)
        client=FakeClaude();ft.run_fallback_translation(client,self.primary,self.evaluator,self.source,child,'fake',resume=True)
        self.assertEqual(client.calls,[])
        self.assertEqual(reporting.stage_metrics(ft._read(child))['submissions'],2)

    def managed(self):
        config=WorkflowConfig(input_path=self.source,output_root=self.folder/'runs',target_language='Chinese')
        run=ActiveRun.create(config,self.units,True)
        for stage,path in [('primary',self.primary),('evaluation',self.evaluator),('fallback',self.fallback)]:
            owned,_=run.prepare(stage);owned.write_bytes(path.read_bytes());run.record(stage,owned)
        return run,config

    def test_manifest_summary_and_future_prepare_select_only_continuation(self):
        run,config=self.managed();original=run.path('fallback');before=original.read_bytes()
        result=run.recover_fallback();self.assertEqual(run.path('fallback'),Path(result['path']))
        self.assertEqual(original.read_bytes(),before)
        self.assertEqual(run.state['history'][-1]['artifact']['sha256'],pt._file_hash(original))
        summary=(run.manifest_path.parent/'RUN_SUMMARY.md').read_text(encoding='utf-8')
        self.assertIn('5 structurally eligible; 2 pending',summary)
        self.assertIn('historical failed batch',summary)
        self.assertEqual(reporting.unit_counts(reporting.load_run(run.manifest_path)['records'])['accepted_fallback'],0)
        stream=io.StringIO()
        with redirect_stdout(stream):reporting.print_stage_summary(run,'fallback')
        self.assertIn('Recorded submissions: 1',stream.getvalue())
        reloaded=ActiveRun.open(run.manifest_path,config,True)
        self.assertEqual(reloaded.recover_fallback(),result)
        child,resume=reloaded.prepare('fallback');self.assertTrue(resume)
        self.assertNotEqual(child,Path(result['path']))
        client=FakeClaude();ft.run_fallback_translation(client,reloaded.path('primary'),reloaded.path('evaluation'),self.source,child,'fake',resume=True)
        reloaded.record('fallback',child)
        self.assertEqual(len(ft.inspect_fallback_checkpoint(child)['candidates']),7)
        self.assertEqual(len(client.calls),1)

    def test_cached_manifest_rechecks_original_and_interrupted_registration_reuses_artifact(self):
        run,config=self.managed()
        with patch.object(run,'record',side_effect=OSError('disk')):
            with self.assertRaises(OSError):run.recover_fallback()
        run=ActiveRun.open(run.manifest_path,config,True);result=run.recover_fallback()
        original=Path(ft._read(result['path'])['fallback_offline_recovery']['original_checkpoint']['path'])
        original.write_bytes(original.read_bytes()+b' ')
        with self.assertRaises(ValueError):run.path('fallback')

    def test_finalization_retains_historical_failure_and_candidate_specific_policies(self):
        result,state=self.recover();evaluator=self.folder/'fallback_evaluation.json'
        fe.run_fallback_evaluation(FakeOpenAI([verdict(True)]),result['path'],self.source,evaluator,'fake')
        with patch.object(fin,'_source_units',return_value=deepcopy(self.units)):
            snapshot=fin.build_finalization_snapshot(self.primary,self.evaluator,self.source,
                fallback_checkpoint=result['path'],fallback_evaluation_checkpoint=evaluator)
        self.assertEqual(snapshot['summary']['counts']['accepted_fallback'],5)
        self.assertEqual(snapshot['summary']['counts']['pending'],2)
        self.assertEqual(snapshot['fallback_historical_batches'],self.old['batches'])
        for i,row in enumerate(snapshot['units']):
            self.assertEqual(row['operational_attempt_counts']['fallback'],1 if i<5 else 0)
            if i<5:self.assertEqual(row['selected_candidate']['validation_policy_version'],3 if i<2 else 4)

    @unittest.skipUnless(ph.check_python_pptx_available()[0],'python-pptx required')
    def test_saved_recovery_snapshot_and_reconstruction_follow_candidate_policies(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
        for i,u in enumerate(self.units):
            p=slide.shapes.add_textbox(Inches(1),Inches(i*.5),Inches(6),Inches(.5)).text_frame.paragraphs[0]
            for j,span in enumerate(u['styled_spans']):
                run=p.add_run();run.text=span['text'];run.font.bold=bool(j)
        prs.save(self.source)
        self.units=ph.classify_pptx_text_units(ph.build_pptx_text_units(Presentation(self.source),pptx_path=self.source,include_styled_spans=True))
        for p in (self.primary,self.evaluator,self.fallback):p.unlink()
        self.generate();result,state=self.recover()
        evaluator=self.folder/'fallback_evaluation.json'
        fe.run_fallback_evaluation(FakeOpenAI([verdict(True)]),result['path'],self.source,evaluator,'fake')
        snapshot=fin.build_finalization_snapshot(self.primary,self.evaluator,self.source,
            fallback_checkpoint=result['path'],fallback_evaluation_checkpoint=evaluator)
        files=fin.write_finalization_artifacts(snapshot,self.folder/'finalization')
        saved=fr.read_validated_snapshot(files['snapshot'],self.source)['snapshot']
        self.assertEqual(saved['fallback_offline_recovery']['artifact_sha256'],result['sha256'])
        self.assertEqual([u['selected_candidate']['validation_policy_version'] for u in saved['units'][:5]],[3,3,4,4,4])
        output=fr.reconstruct_finalized_snapshot(files['snapshot'],self.folder/'outputs',self.source)
        self.assertEqual(output['report']['status'],'passed')
        fr.validate_reconstruction_artifacts(output['report_path'],files['snapshot'],self.source)

    def test_atomic_recovery_publication_failure_leaves_original_untouched(self):
        with patch.object(pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.recover()
        self.assertEqual(self.fallback.read_bytes(),self.original)
        self.assertEqual(list(self.folder.glob('*.lock')),[])
        self.recover()


if __name__=='__main__':unittest.main()
