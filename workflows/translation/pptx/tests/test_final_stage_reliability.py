"""Attribute preservation and final-stage reuse; injected clients, no live APIs."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pptx_translation import pptx_reconstruction as pr
from pptx_translation import primary_translation as pt
from pptx_translation import finalized_reconstruction as fr
from pptx_translation import pptx_helpers as ph
from pptx_translation.active_run import ActiveRun
from pptx_translation.workflow_config import WorkflowConfig
import test_finalized_reconstruction as fixtures
from checkpoint_fixtures import managed_run


class AttributeWhitespaceTests(unittest.TestCase):
    def test_general_attributes_preserve_character_references(self):
        raw=b'<root xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" descr="map&#xA;&#xA;description" custom="a&#13;b&#9;c&#10;d &amp; &quot;x&quot;"><child data="&#9;&#10;&#13;"/><a:t>Hello</a:t></root>'
        source=ET.fromstring(raw);patched=ET.fromstring(raw);patched.find('a:t',pr.NS).text='Hola'
        output=pr._serialize_patched(raw,patched);actual=ET.fromstring(output)
        self.assertEqual(actual.attrib,source.attrib)
        self.assertEqual(actual[0].attrib,source[0].attrib)
        self.assertEqual(actual.find('a:t',pr.NS).text,'Hola')
        self.assertIn(b'&#10;',output);self.assertIn(b'&#13;',output);self.assertIn(b'&#9;',output)

    def test_literal_attribute_whitespace_keeps_original_parsed_semantics(self):
        raw=b'<root descr="a\n\tb\rc"><child>Text</child><!-- untouched --></root>'
        original=ET.fromstring(raw)
        self.assertEqual(pr._signature(ET.fromstring(pr._serialize_patched(raw,original))),pr._signature(original))



@unittest.skipUnless(ph.check_python_pptx_available()[0], 'python-pptx required')
class FinalStageReliabilityTests(unittest.TestCase):
    def setUp(self):
        fixtures.FinalizedReconstructionTests.setUp(self)
        fixtures.FinalizedReconstructionTests.fixture(self)
        self.config=WorkflowConfig(input_path=self.source,output_root=self.folder/'managed')
        self.run=managed_run(self.config,self.folder/'primary.json',self.folder/'evaluation.json')

    def tearDown(self):fixtures.FinalizedReconstructionTests.tearDown(self)

    def test_finalization_reuse_does_not_create_directories_or_invalidate_reconstruction(self):
        first=self.run.finalize();before=Path(first['snapshot']).read_bytes()
        result=self.run.reconstruct();self.run.record('reconstruction',result['report_path'])
        second=self.run.finalize()
        self.assertEqual(first,second);self.assertEqual(before,Path(second['snapshot']).read_bytes())
        self.assertEqual(len(list((self.run.manifest_path.parent/'checkpoints').glob('finalization_*.json'))),1)
        self.assertEqual(self.run.path('reconstruction'),Path(result['report_path']))

    def test_finalization_registration_failure_reuses_persisted_attempt_after_restart(self):
        with patch.object(self.run,'record',side_effect=OSError('registration failed')):
            with self.assertRaises(OSError):self.run.finalize()
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        with patch('pptx_translation.finalization.write_finalization_artifacts',side_effect=AssertionError('duplicate export')):
            result=self.run.finalize()
        self.assertEqual(self.run.path('finalization'),Path(result['snapshot']))
        self.assertEqual(len(list((self.run.manifest_path.parent/'checkpoints').glob('finalization_*.json'))),1)

    def test_passing_reconstruction_reused_and_registered_without_regeneration(self):
        self.run.finalize();first=self.run.reconstruct()
        self.assertIsNone(self.run.path('reconstruction'))
        self.run=ActiveRun.open(self.run.manifest_path,self.config)
        with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=AssertionError('regenerated')):
            second=self.run.reconstruct();self.run.record('reconstruction',second['report_path'])
            third=self.run.reconstruct()
        self.assertEqual(first['output_path'],second['output_path']);self.assertEqual(second,third)
        self.assertEqual(len(list((self.run.manifest_path.parent/'reports').glob('reconstruction__a*'))),1)

    def test_failed_attempt_retained_not_registered_and_next_attempt_is_new(self):
        self.run.finalize();generate=fr.reconstruct_finalized_snapshot
        def failed(*args,**kwargs):
            result=generate(*args,**kwargs);r=result['report'];r['status']='failed';r['failures']=['specific preservation failure']
            pt._write_checkpoint(Path(result['report_path']),r);return result
        with patch.object(fr,'reconstruct_finalized_snapshot',side_effect=failed):first=self.run.reconstruct()
        protected={Path(first[k]):Path(first[k]).read_bytes() for k in ('report_path','output_path')}
        with self.assertRaises(ValueError):self.run.record('reconstruction',first['report_path'])
        self.assertIsNone(self.run.path('reconstruction'))
        second=self.run.reconstruct();self.assertNotEqual(first['output_path'],second['output_path'])
        self.run.record('reconstruction',second['report_path'])
        for p,data in protected.items():self.assertEqual(p.read_bytes(),data)

    def test_report_output_tampering_rejected(self):
        self.run.finalize();result=self.run.reconstruct()
        path=Path(result['output_path']);path.write_bytes(path.read_bytes()+b'tampered')
        with self.assertRaises(ValueError):self.run.record('reconstruction',result['report_path'])
        self.assertIsNone(self.run.path('reconstruction'))



if __name__=='__main__':unittest.main()
