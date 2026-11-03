"""Saved-record reporting regressions; no provider access or PPTX generation."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import primary_translation as pt
from pptx_translation import run_reporting as reporting
import test_active_run as fixtures
from pptx_translation.active_run import ActiveRun
from test_evaluation import FakeOpenAI
from test_primary_translation import FakeGemini


def accept_all(payload):
    return dict(evaluations=[dict(unit_key=u['unit_key'],passed=True,feedback='') for u in payload['units']])


class ReportingTests(unittest.TestCase):
    setUp=fixtures.ActiveRunTests.setUp
    generate=fixtures.ActiveRunTests.generate
    evaluation=fixtures.ActiveRunTests.evaluation

    @property
    def summary(self):return self.run.manifest_path.parent/'RUN_SUMMARY.md'

    def finalize_saved_fixture(self, statuses=None):
        """Minimal saved finalization fixture; bypasses selection, not reporting."""
        records=reporting.load_run(self.run.manifest_path)['records'];primary=records['primary']
        statuses=statuses or ['accepted_primary']*len(primary['units'])
        counts={s:statuses.count(s) for s in reporting.COUNTS}
        ancestry=dict(source=dict(path=str(self.source),sha256=pt._file_hash(self.source)))
        for stage,name in [('primary','primary'),('evaluation','primary_evaluation')]:
            path=self.run.path(stage);ancestry[name]=dict(path=str(path),sha256=pt._file_hash(path))
        snapshot=dict(schema_version=1,stage='offline_finalization',status='complete' if not any(counts[s] for s in ('pending','failed','recovery_eligible','manual_followup')) else 'incomplete',
                      ancestry=ancestry,summary=dict(total_units=len(statuses),counts=counts),
                      units=[dict(unit_key=u['unit_key'],source_unit=u,status=status,reason='reviewer reason') for u,status in zip(primary['units'],statuses)])
        token=pt._digest(self.run._upstream('finalization'))[:20];root=self.run.manifest_path.parent
        path=root/'checkpoints'/f'finalization_{token}.json';pt._write_checkpoint(path,snapshot)
        (root/'reports'/f'unit_summary_{token}.csv').write_text('saved CSV',encoding='utf-8')
        (root/'reports'/f'manual_followup_{token}.md').write_text('saved review',encoding='utf-8')
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            self.run.record('finalization',path)
        return path

    def reconstruction_saved_fixture(self, version=1, status='passed', register=True):
        root=self.run.manifest_path.parent;folder=root/'reports'/f'reconstruction__a{version:03d}';folder.mkdir()
        output=(root/'final'/f'source__es__v{version:03d}.pptx' if status=='passed' else folder/'diagnostic.pptx')
        output.write_bytes(b'preexisting output fixture '+str(version).encode())
        final=self.run._path('finalization')
        counts=reporting.read_record(final)['summary']['counts']
        applied=counts['accepted_primary']+counts['accepted_fallback']
        complete=not any(counts[s] for s in ('pending','failed','recovery_eligible','manual_followup'))
        report=dict(schema_version=1,stage='finalized_pptx_reconstruction',status=status,
                    finalization_snapshot=dict(path=str(final),sha256=pt._file_hash(final)),output=str(output),output_sha256=pt._file_hash(output),
                    reopened_slide_count=1,applied_translations=applied,verified_non_target_units=3-applied,manual_review=[],
                    failures=[] if status=='passed' else ['Preservation failed on slide 1'],validation=dict(span_checks=[dict(passed=True)]),
                    translation_coverage='complete' if complete else 'partial',deliverable_version=version)
        if status!='passed':report.pop('deliverable_version')
        path=folder/'reconstruction_report.json';pt._write_checkpoint(path,report)
        upstream=dict(finalization=pt._file_hash(final));token=pt._digest(upstream)[:20]
        journal=root/'checkpoints'/f'reconstruction_publication_{token}.json'
        pt._write_checkpoint(journal,dict(upstream=upstream,report_path=str(path),status=status))
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            if register:self.run.record('reconstruction',path)
            else:self.run.refresh_summary()
        return path

    def test_fresh_incomplete_failure_retries_and_on_demand_details(self):
        self.assertIn('unit counts not yet saved',self.summary.read_text(encoding='utf-8'))
        self.generate(FakeGemini(['malformed response']),max_batches=1)
        state=reporting.load_run(self.run.manifest_path)['records']['primary'];metrics=reporting.stage_metrics(state)
        self.assertEqual((metrics['submissions'],metrics['operational_failures'],metrics['pending']),(1,1,3))
        self.assertIn('No registered passing deliverable',self.summary.read_text(encoding='utf-8'))
        detail=reporting.inspect_unit(self.run.manifest_path,'one')
        self.assertIn('raw_response',detail['primary']['batches'][0]['attempts'][0])
        self.generate(max_batches=1);self.evaluation()
        counts=reporting.unit_counts(reporting.load_run(self.run.manifest_path)['records'])
        self.assertEqual(counts['accepted_primary'],2);self.assertEqual(counts['recovery_eligible'],1)
        stream=io.StringIO()
        with redirect_stdout(stream):reporting.print_stage_summary(self.run,'evaluation')
        self.assertIn('Meaning omitted.',stream.getvalue());self.assertNotIn('one accepted',stream.getvalue())
        metrics=reporting.stage_metrics(reporting.load_run(self.run.manifest_path)['records']['primary'])
        self.assertEqual((metrics['submissions'],metrics['retries']),(2,1))

    def test_invalidated_handoffs_refresh_without_old_release_claims(self):
        self.generate(max_units=1,max_batches=1);self.evaluation()
        before=self.summary.read_bytes()
        self.generate(max_units=1,max_batches=1)
        text=self.summary.read_text(encoding='utf-8')
        self.assertNotEqual(before,self.summary.read_bytes())
        self.assertIn('Not used / not started',text)
        self.assertIsNone(self.run.path('evaluation'))
        self.assertEqual(reporting.unit_counts(reporting.load_run(self.run.manifest_path)['records'])['pending'],3)

    def test_primary_successful_retry_labels_prior_errors_as_history(self):
        self.generate(FakeGemini(['malformed response']*2),max_batches=2)
        self.generate(max_batches=1)
        path=self.run.path('primary');before=path.read_bytes()
        stream=io.StringIO()
        with redirect_stdout(stream):reporting.print_stage_summary(self.run,'primary')
        text=stream.getvalue()
        self.assertIn('Completed: 3 | failed: 0 | pending: 0',text)
        self.assertIn('Retry-pending: 0 | exhausted: 0',text)
        self.assertIn('Recorded submissions: 3 | retries: 2 | operational failures: 2',text)
        self.assertIn('Latest submission b1-a3: success',text)
        self.assertEqual(text.count('Historical submission error'),2)
        self.assertNotIn('Current submission error',text)
        self.assertEqual(path.read_bytes(),before)

    def test_primary_unresolved_error_remains_current_until_exhausted(self):
        self.generate(FakeGemini(['malformed response']),max_batches=1)
        for exhausted in (False,True):
            if exhausted:self.generate(FakeGemini(['malformed response']*2),max_batches=2)
            path=self.run.path('primary');before=path.read_bytes()
            stream=io.StringIO()
            with redirect_stdout(stream):reporting.print_stage_summary(self.run,'primary')
            text=stream.getvalue()
            self.assertIn('Current submission error',text)
            self.assertIn(f'Latest submission b1-a{3 if exhausted else 1}: error',text)
            self.assertIn('Retry-pending: 0 | exhausted: 3' if exhausted else 'Retry-pending: 3 | exhausted: 0',text)
            self.assertIn('Completed: 0 | failed: 3 | pending: 0' if exhausted else 'Completed: 0 | failed: 0 | pending: 3',text)
            self.assertEqual(path.read_bytes(),before)

    def test_atomic_reporting_failure_preserves_committed_manifest_and_retry(self):
        self.generate();before=self.summary.read_bytes()
        stream=io.StringIO()
        with patch.object(reporting,'_atomic_markdown',side_effect=OSError('interrupted reporting')),redirect_stdout(stream):
            self.evaluation()
        self.assertIn('RUN_SUMMARY.md refresh failed',stream.getvalue())
        self.assertEqual(self.summary.read_bytes(),before)
        self.assertIn('evaluation',reporting.read_record(self.run.manifest_path)['stages'])
        reporting.write_summary(self.run.manifest_path)
        self.assertNotEqual(self.summary.read_bytes(),before)
        saved=self.summary.read_bytes()
        with patch.object(reporting.os,'replace',side_effect=OSError('atomic replacement interrupted')):
            with self.assertRaises(OSError):reporting._atomic_markdown(self.summary,'partial')
        self.assertEqual(self.summary.read_bytes(),saved)
        self.assertEqual(list(self.summary.parent.glob('RUN_SUMMARY.md.*.tmp')),[])
        self.assertFalse(self.run.manifest_path.with_name('run.json.lock').exists())

    def test_preview_is_read_only_and_rejects_changed_hashes(self):
        self.generate();before={p:p.read_bytes() for p in self.run.manifest_path.parent.rglob('*') if p.is_file()}
        with patch.object(reporting,'_atomic_markdown',side_effect=AssertionError('preview write')):
            text=reporting.render_summary(self.run.manifest_path)
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertIn('Incomplete',text)
        path=self.run.path('primary');state=pt._read_checkpoint(path);state['note']='unregistered change';pt._write_checkpoint(path,state)
        with self.assertRaisesRegex(ValueError,'register/sync'):reporting.render_summary(self.run.manifest_path)

    def test_finalization_counts_manual_cases_and_relative_links(self):
        self.generate();self.evaluation(FakeOpenAI([accept_all]))
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            self.finalize_saved_fixture(['accepted_primary','preserved','manual_followup'])
        text=reporting.render_summary(self.run.manifest_path)
        self.assertIn('**Incomplete**',text);self.assertIn('reviewer reason',text)
        self.assertIn('| 3 | 1 | 0 | 1 | 0 | 0 | 0 | 1 |',text)
        self.assertIn('Unit CSV',text);self.assertIn('Manual/recovery report',text)
        for link in re.findall(r'\]\(([^)]+)\)',text):
            self.assertFalse(Path(link).is_absolute())
            self.assertTrue((self.run.manifest_path.parent/link).is_file(),link)

    def test_successful_versions_failed_latest_attempt_and_partial_coverage(self):
        self.generate();self.evaluation(FakeOpenAI([accept_all]))
        self.finalize_saved_fixture()
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            self.reconstruction_saved_fixture(version=1)
            self.reconstruction_saved_fixture(version=2)
        text=reporting.render_summary(self.run.manifest_path)
        self.assertIn('deliverable — v002',text);self.assertIn('Human visual review: **Not recorded**',text)
        self.assertIn('1/1 passed',text);self.assertIn('**Complete**',text)
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            self.reconstruction_saved_fixture(version=3,status='failed',register=False)
        text=reporting.render_summary(self.run.manifest_path)
        self.assertIn('deliverable — v002',text);self.assertIn('Latest reconstruction attempt: **failed**',text)
        self.assertIn('Preservation failed',text)

    def test_partial_passing_output_is_explicitly_incomplete(self):
        self.generate();self.evaluation(FakeOpenAI([accept_all]))
        self.finalize_saved_fixture(['accepted_primary','preserved','pending'])
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            self.reconstruction_saved_fixture()
        text=reporting.render_summary(self.run.manifest_path)
        self.assertIn('Translation: **Incomplete**',text)
        self.assertIn('**partial translation; unresolved source text remains.**',text)
        self.assertNotIn('Release coverage: complete translation.',text)

    def test_failed_attempt_is_not_a_successful_release(self):
        self.generate();self.evaluation(FakeOpenAI([accept_all]));self.finalize_saved_fixture()
        with patch.object(self.run,'_validate',side_effect=lambda stage,path:reporting.read_record(path)):
            self.reconstruction_saved_fixture(status='failed',register=False)
        text=reporting.render_summary(self.run.manifest_path)
        self.assertIn('No registered passing deliverable',text);self.assertNotIn('Current validated deliverable',text)
        self.assertIn('not a successful release',text)

    def test_printed_cases_are_bounded_without_changing_saved_data(self):
        stream=io.StringIO();cases=['x'*1000]*100
        with redirect_stdout(stream):reporting.print_cases(cases)
        self.assertLess(len(stream.getvalue()),1700);self.assertIn('95 more cases',stream.getvalue())
        self.assertEqual(len(cases[0]),1000)




if __name__=='__main__':unittest.main()
