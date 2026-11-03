"""Offline saved-finalization -> maintained reconstruction adapter regressions."""
from copy import deepcopy
from dataclasses import replace
import contextlib
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
from pptx_translation import evaluation as ev
from pptx_translation import finalization as fin
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import pptx_helpers as ph
from test_primary_translation import FakeGemini
from test_evaluation import FakeOpenAI
from pptx_translation.workflow_config import WorkflowConfig,LazyAPIClients


@unittest.skipUnless(ph.check_python_pptx_available()[0], 'python-pptx required')
class FinalizedReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.source=self.folder/'source.pptx'
        from pptx import Presentation
        from pptx.util import Inches
        prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
        box=slide.shapes.add_textbox(Inches(1),Inches(1),Inches(6),Inches(1))
        box.text_frame.text='Hello\vworld 1'
        box.text_frame.paragraphs[0].runs[0].font.bold=True
        box.text_frame.paragraphs[0].runs[1].hyperlink.address='https://example.org/meaning'
        for y,text in [(3,'Hello 2'),(4,'Hello 3'),(5,'https://example.org')]:
            slide.shapes.add_textbox(Inches(1),Inches(y),Inches(6),Inches(1)).text=text
        prs.save(self.source)
        self.before=self.source.read_bytes()
        self.protected={}

    def tearDown(self):
        self.assertEqual(self.source.read_bytes(),self.before)
        for p,before in self.protected.items():self.assertEqual(p.read_bytes(),before)

    def fixture(self,styled=True):
        from pptx import Presentation
        units=ph.classify_pptx_text_units(ph.build_pptx_text_units(Presentation(self.source),pptx_path=self.source,include_styled_spans=styled))
        primary=self.folder/'primary.json';evaluator=self.folder/'evaluation.json'
        pt.run_primary_translation(FakeGemini(),units,self.source,'English','Spanish','fake','Translate',primary,max_units=1,max_batches=2)
        ev.run_evaluation(FakeOpenAI(),primary,self.source,evaluator,'fake')
        snapshot=fin.build_finalization_snapshot(primary,evaluator,self.source)
        files=fin.write_finalization_artifacts(snapshot,self.folder/'finalization')
        self.snapshot=Path(files['snapshot'])
        self.protected.update({p:p.read_bytes() for p in [primary,evaluator,*map(Path,files.values())]})
        return snapshot

    def test_styled_targets_preserve_spans_hyperlinks_breaks_and_non_targets(self):
        self.fixture()
        plan=fr.build_reconstruction_plan(self.snapshot,self.source)
        self.assertEqual(len(plan['span_replacements']),2)
        self.assertEqual(plan['paragraph_replacements'],[])
        result=fr.reconstruct_finalized_snapshot(self.snapshot,self.folder/'outputs',self.source)
        report=result['report']
        self.assertEqual(report['status'],'passed')
        self.assertEqual((report['applied_translations'],report['verified_non_target_units']),(2,2))
        self.assertEqual(report['completeness']['counts']['pending'],1)
        self.assertEqual(report['completeness']['counts']['preserved'],1)
        self.assertEqual(report['validation']['intentional_changes'],[])
        self.assertTrue(all(c['passed'] for c in report['validation']['span_checks']))
        self.assertEqual(report['failures'],[])
        self.assertIn('Spanish_partial_translation',result['output_path'])
        from pptx import Presentation
        rebuilt=Presentation(result['output_path']);frame=rebuilt.slides[0].shapes[0].text_frame
        self.assertEqual(frame.text,'Hola\vmundo 1')
        self.assertTrue(frame.paragraphs[0].runs[0].font.bold)
        self.assertEqual(frame.paragraphs[0].runs[1].hyperlink.address,'https://example.org/meaning')
        saved=json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
        integrity=saved.pop('integrity');self.assertEqual(integrity,pt._digest(saved))
        again=fr.reconstruct_finalized_snapshot(self.snapshot,self.folder/'outputs',self.source)
        self.assertNotEqual(result['output_path'],again['output_path'])

    def test_ordinary_targets_keep_unsafe_paragraph_unchanged_for_manual_review(self):
        self.fixture(styled=False)
        plan=fr.build_reconstruction_plan(self.snapshot,self.source)
        self.assertEqual(plan['span_replacements'],[])
        self.assertEqual(len(plan['paragraph_replacements']),2)
        result=fr.reconstruct_finalized_snapshot(self.snapshot,self.folder/'outputs',self.source)
        report=result['report']
        self.assertEqual(report['status'],'manual_review')
        self.assertEqual(report['applied_translations'],1)
        self.assertEqual(len(report['manual_review']),1)
        self.assertEqual(report['failures'],[])
        from pptx import Presentation
        self.assertEqual(Presentation(result['output_path']).slides[0].shapes[0].text_frame.text,'Hello\vworld 1')

    def test_reject_corrupt_digest_selected_span_text_and_provenance_before_writes(self):
        self.fixture()
        state=json.loads(self.snapshot.read_text(encoding='utf-8'));state.pop('integrity')
        changes=[lambda s:s['units'][0]['selected_candidate'].update(candidate_sha256='stale'),
                 lambda s:s['units'][0]['selected_candidate'].update(span_translations=None),
                 lambda s:s['units'][0]['selected_candidate'].update(text='Tampered'),
                 lambda s:s['units'][0]['selected_candidate'].update(generation_checkpoint_sha256='stale'),
                 lambda s:s['summary']['counts'].update(pending=0)]
        bad=self.folder/'bad.json'
        for mutate in changes:
            changed=deepcopy(state);mutate(changed);pt._write_checkpoint(bad,changed)
            with self.assertRaises(ValueError):fr.reconstruct_finalized_snapshot(bad,self.folder/'not-created',self.source)
            self.assertFalse((self.folder/'not-created').exists())
        bad.write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError):fr.build_reconstruction_plan(bad,self.source)

    def test_source_mismatch_and_output_protection(self):
        self.fixture()
        wrong=self.folder/'wrong.pptx';wrong.write_bytes(self.before+b'changed')
        with self.assertRaises(ValueError):fr.reconstruct_finalized_snapshot(self.snapshot,self.folder/'not-created',wrong)
        self.assertFalse((self.folder/'not-created').exists())
        with self.assertRaises(ValueError):fr.reconstruct_finalized_snapshot(self.snapshot,self.folder,self.source)
        with self.assertRaises(ValueError):fr.reconstruct_finalized_snapshot(self.snapshot,self.source,self.source)






if __name__=='__main__':unittest.main()
