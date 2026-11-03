"""Offline finalization: branch identity, selection, coverage and review artifacts."""
from copy import deepcopy
import contextlib
import csv
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
from pptx_translation import fallback_translation as ft
from pptx_translation import fallback_evaluation as fe
from pptx_translation import finalization as fin
from pptx_translation import pptx_helpers as ph
import test_evaluation as fixtures
from test_fallback_translation import FakeClaude


def accept_all(payload):
    return dict(evaluations=[dict(unit_key=u['unit_key'],passed=True,feedback='') for u in payload['units']])


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        fixtures.EvaluationTests.setUp(self)
        ev.run_evaluation(fixtures.FakeOpenAI(),self.primary,self.source,self.output,'fake-evaluator')
        self.originals={p:p.read_bytes() for p in (self.primary,self.output,self.source)}
        # Fake-client fixtures intentionally use a byte-only source identity.
        # Real-PPTX extraction is exercised separately below and by notebook tests.
        self.extract=patch.object(fin,'_source_units',return_value=deepcopy(self.units))
        self.extract.start();self.addCleanup(self.extract.stop)
        self.fallback=self.folder/'fallback.json';self.fallback_eval=self.folder/'fallback-eval.json'

    def tearDown(self):
        for p,data in self.originals.items():self.assertEqual(p.read_bytes(),data)

    def snapshot(self,**kwargs):
        return fin.build_finalization_snapshot(self.primary,self.output,self.source,**kwargs)

    def generate(self, evaluate=True, accept=True):
        ft.run_fallback_translation(FakeClaude(),self.primary,self.output,self.source,self.fallback,'fake-claude')
        if evaluate:
            fe.run_fallback_evaluation(fixtures.FakeOpenAI([accept_all] if accept else None),
                                       self.fallback,self.source,self.fallback_eval,'fake-evaluator')

    def test_primary_selection_rejection_preservation_and_no_silent_drop(self):
        s=self.snapshot();self.assertEqual(s['summary']['total_units'],4)
        self.assertEqual(s['summary']['counts']['accepted_primary'],2)
        self.assertEqual(s['summary']['counts']['preserved'],1)
        self.assertEqual(s['summary']['counts']['recovery_eligible'],1)
        r={r['unit_key']:r for r in s['units']}
        self.assertEqual(r['one']['selected_candidate']['text'],'Hola 1')
        self.assertIsNone(r['styled']['selected_candidate'])
        self.assertIsNone(r['styled']['final_text'])
        self.assertEqual(r['styled']['output_text'],'Hello\vworld')
        self.assertFalse(r['styled']['needs_manual_followup'])
        self.assertEqual(r['styled']['primary_decision']['feedback'],'Meaning omitted.')
        self.assertEqual(sum(s['summary']['counts'].values()),len(s['units']))

    def test_accepted_fallback_preserves_exact_spans_digest_and_source_formats(self):
        self.generate()
        s=self.snapshot(fallback_checkpoint=self.fallback,fallback_evaluation_checkpoint=self.fallback_eval)
        r=next(r for r in s['units'] if r['unit_key']=='styled')
        candidate=ft._read(self.fallback)['batches'][0]['candidates'][0]
        self.assertEqual(r['status'],'accepted_fallback')
        self.assertEqual(r['selected_candidate']['span_translations'],candidate['fallback_span_translations'])
        self.assertEqual(r['selected_candidate']['candidate_sha256'],candidate['candidate_sha256'])
        self.assertEqual(r['selected_candidate']['text'],candidate['fallback_translation'])
        self.assertEqual(r['source_unit']['span_formats'],self.units[1]['span_formats'])
        self.assertTrue(s['summary']['translation_complete'])
        self.assertEqual(r['semantic_recovery']['completed_attempts'],1)
        self.assertEqual(r['semantic_recovery']['remaining_attempts'],1)

    def test_generated_but_unevaluated_pending_then_rejected_recovery_eligible(self):
        self.generate(evaluate=False)
        r=self.snapshot(fallback_checkpoint=self.fallback)['units'][1]
        self.assertEqual(r['status'],'pending')
        self.assertEqual(r['reason'],'fallback_evaluation_pending')
        fe.run_fallback_evaluation(fixtures.FakeOpenAI(),self.fallback,self.source,self.fallback_eval,'fake')
        r=self.snapshot(fallback_checkpoint=self.fallback,fallback_evaluation_checkpoint=self.fallback_eval)['units'][1]
        self.assertEqual(r['status'],'recovery_eligible')
        self.assertFalse(r['needs_manual_followup']);self.assertFalse(r['semantic_recovery']['exhausted'])

    def test_operational_retries_do_not_spend_semantic_rounds(self):
        with self.assertRaises(ft.FallbackStageError):
            ft.run_fallback_translation(FakeClaude([RuntimeError('offline')]),self.primary,self.output,self.source,self.fallback,'fake')
        r=self.snapshot(fallback_checkpoint=self.fallback)['units'][1]
        self.assertEqual(r['status'],'failed')
        self.assertEqual(r['semantic_recovery']['completed_attempts'],0)
        self.assertFalse(r['needs_manual_followup'])
        ft.run_fallback_translation(FakeClaude(),self.primary,self.output,self.source,self.fallback,'fake',resume=True)
        r=self.snapshot(fallback_checkpoint=self.fallback)['units'][1]
        self.assertEqual(r['semantic_recovery']['completed_attempts'],1)
        self.assertEqual(r['operational_attempt_counts']['fallback'],2)
        self.assertEqual(r['operational_retry_counts']['fallback'],1)
        self.assertIn('offline',r['history']['fallback']['attempts'][0]['error'])

    def test_manual_review_label_reports_and_source_retention(self):
        s=self.snapshot(manual_followup={'styled':'Reviewer requested manual translation.'})
        r=s['units'][1]
        self.assertEqual(r['status'],'manual_followup')
        self.assertEqual(r['status_before_manual'],'recovery_eligible')
        self.assertIsNone(r['final_text']);self.assertEqual(r['output_text'],r['source_unit']['text'])
        output=fin.write_finalization_artifacts(s,self.folder/'artifacts')
        self.assertEqual(len(list(Path(output['snapshot']).parent.iterdir())),3)
        saved=json.loads(Path(output['snapshot']).read_text(encoding='utf-8'))
        integrity=saved.pop('integrity');self.assertEqual(integrity,pt._digest(saved))
        self.assertEqual(saved['summary'],s['summary'])
        with Path(output['summary']).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
        self.assertEqual(len(rows),4);self.assertEqual(rows[1]['review_label'],fin.MANUAL_LABEL)
        report=Path(output['manual_review']).read_text(encoding='utf-8')
        for value in (fin.MANUAL_LABEL,'styled','Hello','Meaning omitted.','primary_evaluation'):
            self.assertIn(value,report)
        with self.assertRaises(ValueError):self.snapshot(manual_followup={'one':'Accepted cannot be flagged'})
        with self.assertRaises(ValueError):self.snapshot(manual_followup={'unknown':'No unit'})
        with self.assertRaises(ValueError):self.snapshot(manual_followup={'styled':''})
        again=fin.write_finalization_artifacts(s,self.folder/'artifacts')
        self.assertNotEqual(output['snapshot'],again['snapshot'])

    def test_primary_pending_and_evaluator_errors_remain_distinct(self):
        partial=self.folder/'partial.json';evaluator=self.folder/'partial-eval.json'
        pt.run_primary_translation(fixtures.FakePrimary(),self.units,self.source,'English','Spanish','fake','Translate',partial,max_units=1,max_batches=1)
        with self.assertRaises(ev.EvaluationStageError):
            ev.run_evaluation(fixtures.FakeOpenAI([RuntimeError('offline')]),partial,self.source,evaluator,'fake')
        s=fin.build_finalization_snapshot(partial,evaluator,self.source)
        self.assertEqual(s['summary']['counts']['pending'],2)
        self.assertEqual(s['summary']['counts']['failed'],1)
        self.assertEqual(s['summary']['counts']['manual_followup'],0)
        with self.assertRaises(ValueError):
            fin.build_finalization_snapshot(partial,evaluator,self.source,manual_followup={'styled':'Pending cannot be exhausted'})

    def test_ancestry_mismatch_diagnostic_and_span_tampering_rejected(self):
        self.generate()
        state=ft._read(self.fallback);state['compatibility']['primary_sha256']='different'
        bad=self.folder/'bad.json';pt._write_checkpoint(bad,state)
        with self.assertRaisesRegex(ValueError,'branch'):
            self.snapshot(fallback_checkpoint=bad)
        state=ft._read(self.fallback);state['batches'][0]['candidates'][0]['fallback_span_translations'][0]['text']='wrong'
        pt._write_checkpoint(bad,state)
        with self.assertRaises(ValueError):self.snapshot(fallback_checkpoint=bad)
        with self.assertRaises(ValueError):self.snapshot(fallback_evaluation_checkpoint=self.fallback_eval)
        state=pt._read_checkpoint(self.primary);state['diagnostic']={'kind':'calibration'}
        pt._write_checkpoint(bad,state)
        with self.assertRaisesRegex(ValueError,'Diagnostic'):
            fin.build_finalization_snapshot(bad,self.output,self.source)
        state=fe._read(self.fallback_eval);state['compatibility']['fallback_sha256']='different'
        pt._write_checkpoint(bad,state)
        with self.assertRaises(ValueError):self.snapshot(fallback_checkpoint=self.fallback,fallback_evaluation_checkpoint=bad)

    def test_export_source_protection_and_stale_inputs(self):
        s=self.snapshot()
        with self.assertRaises(ValueError):fin.write_finalization_artifacts(s,self.folder)
        other=deepcopy(s);other['ancestry']['primary']['sha256']='changed'
        with self.assertRaises(ValueError):fin.write_finalization_artifacts(other,self.folder/'not-created')
        self.assertFalse((self.folder/'not-created').exists())
        with patch.object(fin.pt.os,'replace',side_effect=OSError('disk')):
            with self.assertRaises(OSError):fin.write_finalization_artifacts(s,self.folder/'failed-export')
        self.assertEqual(list((self.folder/'failed-export').iterdir()),[])

    def test_changed_formatting_is_rejected_before_selection(self):
        altered=deepcopy(self.units)
        altered[1]['span_formats']={'local':'<rPr b="1"/>'}
        with patch.object(fin,'_source_units',return_value=altered):
            with self.assertRaises((ValueError,fin.ET.ParseError)):self.snapshot()

    def test_source_coverage_and_primary_evaluator_request_validation(self):
        with patch.object(fin,'_source_units',return_value=self.units[:-1]):
            with self.assertRaisesRegex(ValueError,'Complete source units'):self.snapshot()
        state=ev._read(self.output);state['batches'][0]['request_hash']='stale'
        bad=self.folder/'bad-evaluation.json';pt._write_checkpoint(bad,state)
        with self.assertRaises(ValueError):fin.build_finalization_snapshot(self.primary,bad,self.source)
        state=ev._read(self.output);state['compatibility']['primary_sha256']='other'
        pt._write_checkpoint(bad,state)
        with self.assertRaises(ValueError):fin.build_finalization_snapshot(self.primary,bad,self.source)





if __name__=='__main__':unittest.main()
