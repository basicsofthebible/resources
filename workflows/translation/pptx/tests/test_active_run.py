"""Explicit manifest selection, crash recovery and handoffs, using injected clients."""
from copy import deepcopy
from dataclasses import replace
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import evaluation as ev
from pptx_translation import fallback_translation as ft
from pptx_translation import fallback_evaluation as fe
from pptx_translation.active_run import ActiveRun
from pptx_translation.workflow_config import WorkflowConfig
import test_evaluation as fixtures
from test_evaluation import FakeOpenAI
from test_primary_translation import FakeGemini
from test_fallback_translation import FakeClaude


class ActiveRunTests(unittest.TestCase):
    def setUp(self):
        fixtures.EvaluationTests.setUp(self)
        self.config=WorkflowConfig(input_path=self.source,output_root=self.folder/'managed')
        self.run=ActiveRun.create(self.config,self.units)

    def generate(self,client=None,**kwargs):
        path,resume=self.run.prepare('primary')
        try:
            return pt.run_primary_translation(client or FakeGemini(),self.units,self.source,'English','Spanish',
                'fake','Translate',path,resume=resume,**kwargs)
        finally:
            if path.exists():self.run.record('primary',path)

    def evaluation(self,client=None,**kwargs):
        path,resume=self.run.prepare('evaluation')
        try:return ev.run_evaluation(client or FakeOpenAI(),self.run.path('primary'),self.source,path,'fake',resume=resume,**kwargs)
        finally:
            if path.exists():self.run.record('evaluation',path)

    def test_unique_fresh_runs_and_explicit_selection(self):
        other=ActiveRun.create(self.config,self.units)
        self.assertNotEqual(other.state['run_id'],self.run.state['run_id'])
        self.assertEqual(self.run.state['stages'],{})
        loaded=ActiveRun.open(self.run.manifest_path,self.config)
        self.assertEqual(loaded.state,self.run.state)
        self.assertIsNone(loaded.path('primary'))
        with self.assertRaises(FileNotFoundError):ActiveRun.open(self.folder/'missing.json',self.config)

    def test_wrong_source_language_extraction_and_manifest_integrity_rejected(self):
        for config,styled in [(replace(self.config,target_language='French'),True),(self.config,False),
                              (replace(self.config,include_smartart=False),True)]:
            with self.assertRaises(ValueError):ActiveRun.open(self.run.manifest_path,config,styled)
        state=deepcopy(self.run.state);state['schema_version']=99;pt._write_checkpoint(self.run.manifest_path,state)
        with self.assertRaises(ValueError):ActiveRun.open(self.run.manifest_path,self.config)

    def test_primary_resume_after_restart_preserves_per_unit_progress_and_budget(self):
        result=self.generate(FakeGemini(['invalid']),max_batches=1)
        self.assertEqual(len(result['retry_pending_unit_keys']),3)
        path=self.run.path('primary')
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        result=self.generate(max_batches=1)
        self.assertEqual(len(result['completed_unit_keys']),3)
        self.assertEqual(self.run.path('primary'),path)
        self.assertTrue(all(r['attempt_count']==2 for r in pt._read_checkpoint(path)['unit_results'].values()))

    def test_completed_primary_never_calls_client_on_resume(self):
        self.generate();client=FakeGemini()
        self.generate(client)
        self.assertEqual(client.calls,[])

    def test_checkpoint_saved_before_manifest_commit_is_recovered_at_exact_path(self):
        path,resume=self.run.prepare('primary');self.assertFalse(resume)
        pt.run_primary_translation(FakeGemini(['invalid']),self.units,self.source,'English','Spanish','fake','Translate',path,max_batches=1)
        self.assertEqual(self.run.state['stages'],{})
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        recovered,resume=self.run.prepare('primary')
        self.assertEqual(recovered,path);self.assertTrue(resume)
        self.assertEqual(self.run.state['stages']['primary']['sha256'],pt._file_hash(path))
        self.assertEqual(self.generate()['completed_unit_keys'],['one','styled','three'])

    def test_interrupted_requests_are_registered_and_resume_without_resetting_budget(self):
        with self.assertRaises(KeyboardInterrupt):self.generate(FakeGemini([KeyboardInterrupt()]))
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        self.generate()
        self.assertTrue(all(r['attempt_count']==2 for r in pt._read_checkpoint(self.run.path('primary'))['unit_results'].values()))

    def test_evaluator_failure_is_automatically_resumed_from_same_owned_checkpoint(self):
        self.generate()
        with self.assertRaises(ev.EvaluationStageError):self.evaluation(FakeOpenAI([RuntimeError('offline')]))
        target=self.run.path('evaluation')
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        result=self.evaluation()
        self.assertEqual(result['evaluated'],3);self.assertEqual(self.run.path('evaluation'),target)
        self.assertEqual(len(ev._read(target)['batches'][0]['attempts']),2)

    def test_primary_update_invalidates_downstream_but_preserves_files(self):
        self.generate(max_units=1,max_batches=1);self.evaluation()
        old=self.run.path('evaluation');saved=old.read_bytes()
        self.generate(max_units=1,max_batches=1)
        self.assertIsNone(self.run.path('evaluation'));self.assertEqual(old.read_bytes(),saved)
        target,resume=self.run.prepare('evaluation');self.assertFalse(resume);self.assertNotEqual(target,old)
        self.evaluation();self.assertEqual(ev.inspect_evaluation_checkpoint(target)['evaluated'],2)
        self.assertTrue(any(r['stage']=='evaluation' for r in self.run.state['history']))

    def test_external_persisted_progress_is_reconciled_and_stale_handoffs_removed(self):
        self.generate(max_units=1,max_batches=1);self.evaluation();old=self.run.path('evaluation')
        path=self.run.path('primary')
        pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Spanish','fake','Translate',path,
                                   resume=True,max_units=1,max_batches=1)
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        self.assertIsNone(self.run.path('evaluation'));self.assertTrue(old.exists())

    def test_mismatched_candidate_or_ancestry_never_enters_manifest(self):
        self.generate();self.evaluation();before=self.run.manifest_path.read_bytes()
        path,_=self.run.prepare('fallback')
        ft.run_fallback_translation(FakeClaude(),self.run.path('primary'),self.run.path('evaluation'),self.source,path,'fake')
        state=ft._read(path);state['compatibility']['primary_sha256']='wrong';pt._write_checkpoint(path,state)
        with self.assertRaises(ValueError):self.run.record('fallback',path)
        self.assertEqual(self.run.manifest_path.read_bytes(),before)
        p=self.run.path('primary');state=pt._read_checkpoint(p)
        state['unit_results']['one']['candidate']['candidate_sha256']='wrong';pt._write_checkpoint(p,state)
        with self.assertRaises(ValueError):ActiveRun.open(self.run.manifest_path,self.config)

    def test_source_change_and_missing_registered_artifact_are_fatal(self):
        self.generate();self.run.path('primary').unlink()
        with self.assertRaises(FileNotFoundError):self.run.prepare('primary')
        self.source.write_bytes(b'changed')
        with self.assertRaises(ValueError):ActiveRun.open(self.run.manifest_path,self.config)

    def test_manifest_persistence_failure_does_not_advance_disk_state(self):
        path,_=self.run.prepare('primary')
        pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Spanish','fake','Translate',path)
        before=self.run.manifest_path.read_bytes()
        with patch.object(pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.run.record('primary',path)
        self.assertEqual(self.run.manifest_path.read_bytes(),before)
        loaded=ActiveRun.open(self.run.manifest_path,self.config)
        self.assertTrue(loaded.prepare('primary')[1])

    def test_concurrent_manifest_selection_is_rejected(self):
        old=ActiveRun.open(self.run.manifest_path,self.config)
        self.generate()
        with self.assertRaisesRegex(ValueError,'another session'):old.prepare('evaluation')

    def test_changed_checkpoint_settings_remain_fatal_on_repeated_validation(self):
        self.generate()
        path=self.run.path('primary');state=pt._read_checkpoint(path)
        # Even an internally valid empty-plan checkpoint cannot replace this run's settings.
        state['compatibility']['instruction']='Changed instruction'
        pt._write_checkpoint(path,state)
        for _ in range(2):
            with self.assertRaises(ValueError):self.run.sync()

    def test_current_extraction_is_checked_before_primary_resume(self):
        self.generate()
        changed=deepcopy(self.units);changed[0]['text']='Changed source paragraph'
        with self.assertRaises(ValueError):self.run.primary_units(changed)
        self.assertEqual(self.run.primary_units(self.units),self.units)

    def test_preview_lookups_and_open_never_persist_manifest_changes(self):
        self.generate(max_units=1,max_batches=1);self.evaluation()
        manifest=self.run.manifest_path;before=manifest.read_bytes();p=self.run.path('primary')
        pt.run_primary_translation(FakeGemini(),self.units,self.source,'English','Spanish','fake','Translate',p,
                                   resume=True,max_units=1,max_batches=1)
        with patch.object(pt,'_write_checkpoint',side_effect=AssertionError('preview write')):
            selected=ActiveRun.open(manifest,self.config)
            self.assertIsNone(selected.path('evaluation'))
            self.assertEqual(selected.path('primary'),p)
        self.assertEqual(manifest.read_bytes(),before)
        selected.sync()
        self.assertNotEqual(manifest.read_bytes(),before)

    def test_all_api_handoffs_and_candidate_digests(self):
        self.generate();self.evaluation()
        path,resume=self.run.prepare('fallback')
        ft.run_fallback_translation(FakeClaude(),self.run.path('primary'),self.run.path('evaluation'),self.source,path,'fake',resume=resume)
        self.run.record('fallback',path)
        target,resume=self.run.prepare('fallback_evaluation')
        fe.run_fallback_evaluation(FakeOpenAI(),path,self.source,target,'fake',resume=resume)
        self.run.record('fallback_evaluation',target)
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        self.assertEqual(set(self.run.state['stages']),{'primary','evaluation','fallback','fallback_evaluation'})
        decisions=fe.inspect_fallback_evaluation_checkpoint(target)['evaluations']
        candidates={c['unit_key']:c for c in ft.inspect_fallback_checkpoint(path)['candidates']}
        self.assertTrue(decisions)
        for decision in decisions:self.assertEqual(decision['candidate_sha256'],candidates[decision['unit_key']]['candidate_sha256'])


from pptx_translation import pptx_helpers as ph




if __name__=='__main__':unittest.main()
