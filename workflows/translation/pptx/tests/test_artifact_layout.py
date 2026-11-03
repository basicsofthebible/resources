"""New-run layout and publication recovery; fake providers and temporary outputs only."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import artifact_layout as layout
from pptx_translation.active_run import ActiveRun
from pptx_translation.workflow_config import WorkflowConfig
from pptx_translation import primary_translation as pt
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import finalization as fin
from pptx_translation import pptx_helpers as ph
from checkpoint_fixtures import managed_run
import test_finalized_reconstruction as fixtures


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'Help_I_dont_understand_the_Bible.pptx';self.source.write_bytes(b'source')
        self.config=WorkflowConfig(input_path=self.source,output_root=self.root/'outputs')

    def test_readable_layout_internal_identity_and_collision(self):
        fixed_timestamp=datetime(2026,1,1,tzinfo=timezone.utc)
        # A collision requires the same timestamp as well as the same random ID.
        with patch.object(layout,'datetime') as clock, \
             patch.object(layout.secrets,'token_hex',side_effect=['abcd1234','abcd1234','deadbeef']):
            clock.now.return_value=fixed_timestamp
            first=ActiveRun.create(self.config,[]);second=ActiveRun.create(self.config,[])
        self.assertRegex(first.manifest_path.parent.name,r'^Help_I_dont_understand_the_Bible__es__\d{8}T\d{6}Z__abcd1234$')
        self.assertEqual(first.state['run_id'],'run_abcd1234')
        self.assertEqual(second.state['run_id'],'run_deadbeef')
        self.assertEqual({p.name for p in first.manifest_path.parent.iterdir()},
                         {'run.json','RUN_SUMMARY.md','checkpoints','reports','final'})
        self.assertEqual(first.prepare('primary'),(first.manifest_path.parent/'checkpoints'/'primary.json',False))
        self.assertEqual(ActiveRun.open(first.manifest_path,self.config).state,first.state)
        with self.assertRaises(ValueError):ActiveRun.open(first.manifest_path,replace(self.config,target_language='French'))

    def test_safe_components_languages_and_lengths(self):
        for value in ('../CON','NUL','..','a/b\\c:*?"<>|\x00','x'*300,' accented é '):
            component=layout.safe_component(value)
            self.assertLessEqual(len(component),100)
            self.assertNotRegex(component,r'[/\\:*?"<>|\x00]')
            self.assertNotIn(component,('.','..','CON','NUL'))
        self.assertEqual(layout.language_tag('Spanish'),'es')
        self.assertEqual(layout.language_tag('Simplified Chinese'),'zh-Hans')
        self.assertEqual(layout.language_tag('pt-BR'),'pt-BR')

    def test_original_manifest_layout_remains_supported_without_migration(self):
        run=ActiveRun.create(self.config,[])
        run.state.pop('artifact_layout');run.state.pop('naming');run._save()
        before=run.manifest_path.read_bytes()
        selected=ActiveRun.open(run.manifest_path,self.config)
        self.assertEqual(selected.prepare('primary'),(run.manifest_path.parent/'primary.json',False))
        self.assertEqual(run.manifest_path.read_bytes(),before)

    def test_qc_is_run_local_and_reused_without_overwrite(self):
        run=ActiveRun.create(self.config,[])
        report=dict(source_sha256=pt._file_hash(self.source),units=[])
        path=layout.write_qc_report(run,report);before=path.read_bytes()
        self.assertEqual(path.parent,run.manifest_path.parent/'reports')
        self.assertEqual(layout.write_qc_report(run,report),path)
        self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(ValueError):layout.write_qc_report(run,dict(report,units=['changed']))


@unittest.skipUnless(ph.check_python_pptx_available()[0],'python-pptx required')
class PublicationTests(unittest.TestCase):
    def setUp(self):
        fixtures.FinalizedReconstructionTests.setUp(self)
        fixtures.FinalizedReconstructionTests.fixture(self)
        self.config=WorkflowConfig(input_path=self.source,output_root=self.folder/'managed')
        self.run=managed_run(self.config,self.folder/'primary.json',self.folder/'evaluation.json')
        self.root=self.run.manifest_path.parent

    def tearDown(self):fixtures.FinalizedReconstructionTests.tearDown(self)

    def test_snapshot_reports_and_successful_versions_reuse_and_explicit_regeneration(self):
        files=self.run.finalize()
        self.assertEqual(Path(files['snapshot']).parent,self.root/'checkpoints')
        self.assertTrue(all(Path(files[k]).parent==self.root/'reports' for k in ('summary','manual_review')))
        first=self.run.reconstruct();self.run.record('reconstruction',first['report_path'])
        output=Path(first['output_path']);before=output.read_bytes()
        self.assertEqual(output.parent,self.root/'final');self.assertTrue(output.name.endswith('__es__v001.pptx'))
        self.assertEqual(first['report']['attempt_id'],'a001')
        with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=AssertionError('duplicate')):
            self.assertEqual(self.run.reconstruct()['output_path'],str(output))
        second=self.run.reconstruct(regenerate=True);self.run.record('reconstruction',second['report_path'])
        self.assertTrue(second['output_path'].endswith('__es__v002.pptx'))
        self.assertEqual(second['report']['attempt_id'],'a002')
        self.assertEqual(output.read_bytes(),before)
        self.assertEqual(len(list((self.root/'final').glob('*.pptx'))),2)
        self.assertTrue((self.root/'checkpoints'/'reconstruction__a001.json').exists())
        self.assertTrue((self.root/'checkpoints'/'reconstruction__a002.json').exists())
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        self.assertEqual(self.run.reconstruct()['output_path'],second['output_path'])

    def test_failed_attempts_and_manual_review_do_not_consume_versions(self):
        self.run.finalize();generate=fr.reconstruct_finalized_snapshot
        for status in ('failed','manual_review'):
            def attempt(*args,**kwargs):
                result=generate(*args,**kwargs);result['report']['status']=status
                if status=='failed':result['report']['failures']=['offline injected failure']
                pt._write_checkpoint(Path(result['report_path']),result['report']);return result
            with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=attempt):
                result=self.run.reconstruct(regenerate=True)
            self.assertNotIn('deliverable_version',result['report'])
            self.assertEqual(list((self.root/'final').iterdir()),[])
            self.assertIn(self.root/'reports',Path(result['output_path']).parents)
            self.assertTrue(Path(result['output_path']).exists())
        success=self.run.reconstruct(regenerate=True)
        self.assertTrue(success['output_path'].endswith('__v001.pptx'))
        self.assertEqual(success['report']['attempt_id'],'a003')

    def test_export_interruption_recovers_exact_staging_report(self):
        self.run.finalize()
        with patch.object(layout,'_publish',side_effect=OSError('before publication')):
            with self.assertRaises(OSError):self.run.reconstruct()
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=AssertionError('regenerated')):
            result=self.run.reconstruct()
        self.assertEqual(result['report']['attempt_id'],'a001')
        self.assertTrue(result['output_path'].endswith('__v001.pptx'))

    def test_move_interruption_recovers_without_second_version(self):
        self.run.finalize();write=pt._write_checkpoint
        def interrupted(path,state):
            if Path(path).name=='reconstruction_report.json' and state.get('artifact_layout')==1:
                raise OSError('after move before report commit')
            return write(path,state)
        with patch.object(pt,'_write_checkpoint',side_effect=interrupted):
            with self.assertRaises(OSError):self.run.reconstruct()
        self.assertEqual(len(list((self.root/'final').glob('*.pptx'))),1)
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=AssertionError('regenerated')):
            result=self.run.reconstruct()
        self.assertEqual(result['report']['deliverable_version'],1)
        self.assertEqual(len(list((self.root/'final').glob('*.pptx'))),1)
        self.run.record('reconstruction',result['report_path'])
        self.assertEqual(ActiveRun.open(self.run.manifest_path,self.config).state,self.run.state)

    def test_partial_finalization_recovers_without_extra_snapshot(self):
        write=pt._write_checkpoint
        def interrupted(path,state):
            if Path(path).name.startswith('finalization_'):raise OSError('snapshot commit interrupted')
            return write(path,state)
        with patch.object(pt,'_write_checkpoint',side_effect=interrupted):
            with self.assertRaises(OSError):self.run.finalize()
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        result=self.run.finalize()
        self.assertTrue(Path(result['snapshot']).exists())
        self.assertEqual(len(list((self.root/'checkpoints').glob('finalization_*.json'))),1)
        self.assertEqual(len(list((self.root/'reports').glob('unit_summary_*.csv'))),1)

    def test_partial_reconstruction_retained_as_diagnostic(self):
        self.run.finalize()
        def interrupted(*args,**kwargs):
            (kwargs['artifact_dir']/'partial.pptx').write_bytes(b'partial')
            raise OSError('interrupted construction')
        with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=interrupted):
            with self.assertRaises(OSError):self.run.reconstruct()
        self.assertEqual(list((self.root/'final').iterdir()),[])
        self.assertTrue((self.root/'reports'/'reconstruction__a001'/'interrupted.txt').exists())
        result=self.run.reconstruct()
        self.assertEqual(result['report']['attempt_id'],'a002')
        self.assertTrue(result['output_path'].endswith('__v001.pptx'))

    def test_legacy_final_stages_keep_original_directories(self):
        self.run.state.pop('artifact_layout');self.run.state.pop('naming');self.run._save()
        selected=ActiveRun.open(self.run.manifest_path,self.config)
        files=selected.finalize();result=selected.reconstruct()
        selected.record('reconstruction',result['report_path'])
        self.assertIn(self.root/'finalization',Path(files['snapshot']).parents)
        self.assertIn(self.root/'translated_pptx',Path(result['output_path']).parents)
        self.assertEqual(list((self.root/'final').iterdir()),[])
        with self.assertRaises(ValueError):selected.reconstruct(regenerate=True)


    def test_publication_record_tampering_and_concurrent_writer_rejected(self):
        self.run.finalize();self.run.reconstruct()
        journal=next((self.root/'checkpoints').glob('reconstruction_publication_*.json'))
        data=json.loads(journal.read_text());data['version']=999;journal.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.run.reconstruct()
        with layout.writer(self.run,'reconstruction'):
            with self.assertRaises(FileExistsError):self.run.reconstruct()


if __name__=='__main__':unittest.main()
