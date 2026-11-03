"""Stage 2A package tests require only the standard library and synthetic PPTX."""
from pathlib import Path
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import pptx_reconstruction as reconstruction


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = ROOT / 'examples/stage1_sample.pptx'
        if not self.source.exists():
            try:
                from examples.create_sample import create_sample
                self.source = create_sample(self.root / 'sample.pptx')
            except ImportError:
                self.skipTest('Synthetic fixture absent and python-pptx unavailable')
        self.units = reconstruction.source_units(self.source)
        self.before = self.source.read_bytes()
        self.output = self.root / 'out.pptx'

    def tearDown(self):
        if hasattr(self, 'before'):
            self.assertEqual(self.source.read_bytes(), self.before)

    def test_identity_preserves_every_package_part_and_run(self):
        report = reconstruction.reconstruct(self.source, self.output, self.units,
                   [dict(unit_key=u['unit_key'], text=u['text']) for u in self.units])
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['unit_count'], 15)
        self.assertEqual(len(report['unchanged']), 15)
        with ZipFile(self.source) as a, ZipFile(self.output) as b:
            self.assertEqual({n: a.read(n) for n in a.namelist()}, {n: b.read(n) for n in b.namelist()})

    def test_controlled_placement_non_targets_and_mixed_run_refusal(self):
        reports = reconstruction.run_sample_round_trips(self.source, self.root, self.units)
        report = reports['controlled']
        self.assertEqual(report['status'], 'manual_review')
        self.assertEqual(len(report['replaced']), 3)
        self.assertEqual(len(report['manual_review']), 1)
        self.assertEqual(report['failures'], [])
        after = {u['unit_key']: u['text'] for u in reconstruction.source_units(report['output'])}
        mapping = {'A small extraction example': 'Un pequeño ejemplo de extracción',
                   'Grouped text': 'Texto agrupado', 'Label': 'Etiqueta'}
        for unit in self.units:
            self.assertEqual(after[unit['unit_key']], mapping.get(unit['text'], unit['text']))

    def test_invalid_identifiers_abort_without_output(self):
        unit = self.units[0]
        cases = [([unit, unit], []), ([dict(unit, unit_key=None)], []),
                 ([dict(unit, shape_path='999:999')], []),
                 ([dict(unit, paragraph_index=99)], []),
                 ([dict(unit, text='stale')], []),
                 ([dict(unit, source_type='smartart_data_point_text')], []),
                 (self.units, [dict(unit_key='missing', text='x')]),
                 (self.units, [dict(text='x')]),
                 (self.units, [dict(unit_key=unit['unit_key'], text=42)]),
                 (self.units, [dict(unit_key=unit['unit_key'], text='x')] * 2)]
        for units, requests in cases:
            with self.subTest(units=units[:1], requests=requests):
                with self.assertRaises(ValueError):
                    reconstruction.reconstruct(self.source, self.output, units, requests)
                self.assertFalse(self.output.exists())

    def test_source_and_existing_output_protected(self):
        with self.assertRaises(ValueError):
            reconstruction.reconstruct(self.source, self.source, self.units, [])
        self.output.write_bytes(b'existing')
        with self.assertRaises(FileExistsError):
            reconstruction.reconstruct(self.source, self.output, self.units, [])
        self.assertEqual(self.output.read_bytes(), b'existing')

    def test_hyperlink_refused_but_preserved(self):
        # Add a hyperlink to a single-run paragraph without changing source fixture.
        linked = self.root / 'linked.pptx'
        with ZipFile(self.source) as a, ZipFile(linked, 'w') as b:
            for info in a.infolist():
                data = a.read(info.filename)
                if info.filename == 'ppt/slides/slide1.xml':
                    root = ET.fromstring(data)
                    run = root.find('.//a:r', reconstruction.NS)
                    props = run.find('a:rPr', reconstruction.NS)
                    if props is None:
                        props = ET.Element('{%s}rPr' % reconstruction.NS['a'])
                        run.insert(0, props)
                    ET.SubElement(props, '{%s}hlinkClick' % reconstruction.NS['a'],
                                  {'action': 'ppaction://hlinkshowjump?jump=nextslide'})
                    data = ET.tostring(root)
                b.writestr(info, data)
        units = reconstruction.source_units(linked)
        report = reconstruction.reconstruct(linked, self.output, units,
                  [dict(unit_key=units[0]['unit_key'], text='Changed')])
        self.assertEqual(len(report['manual_review']), 1)
        self.assertEqual(report['failures'], [])

    def test_fields_breaks_and_empty_replacements_require_review(self):
        for tag in ('fld', 'br'):
            source = self.root / (tag + '.pptx')
            with ZipFile(self.source) as a, ZipFile(source, 'w') as b:
                for info in a.infolist():
                    data = a.read(info.filename)
                    if info.filename == 'ppt/slides/slide1.xml':
                        root = ET.fromstring(data)
                        paragraph = root.find('.//a:p', reconstruction.NS)
                        ET.SubElement(paragraph, '{%s}%s' % (reconstruction.NS['a'], tag))
                        data = ET.tostring(root)
                    b.writestr(info, data)
            units = reconstruction.source_units(source)
            report = reconstruction.reconstruct(source, self.root / ('out_' + tag + '.pptx'), units,
                     [dict(unit_key=units[0]['unit_key'], text='Changed')])
            self.assertEqual(len(report['manual_review']), 1)
            self.assertFalse(report['failures'])
        report = reconstruction.reconstruct(self.source, self.output, self.units,
                 [dict(unit_key=self.units[0]['unit_key'], text='')])
        self.assertEqual(len(report['manual_review']), 1)

    def test_namespace_declarations_survive_changed_slide(self):
        source = self.root / 'namespaces.pptx'
        with ZipFile(self.source) as a, ZipFile(source, 'w') as b:
            for info in a.infolist():
                data = a.read(info.filename)
                if info.filename == 'ppt/slides/slide1.xml':
                    data = data.replace(b'<p:sld ', b'<p:sld xmlns:custom="urn:custom" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" mc:Ignorable="custom" ', 1)
                b.writestr(info, data)
        units = reconstruction.source_units(source)
        report = reconstruction.reconstruct(source, self.output, units,
                 [dict(unit_key=units[0]['unit_key'], text='Changed')])
        self.assertEqual(report['status'], 'passed')
        with ZipFile(self.output) as archive:
            self.assertIn(b'xmlns:custom="urn:custom"', archive.read('ppt/slides/slide1.xml'))

    def test_validation_detects_non_target_damage(self):
        with ZipFile(self.source) as a, ZipFile(self.output, 'w') as b:
            for info in a.infolist():
                data = a.read(info.filename)
                if info.filename == 'ppt/slides/slide2.xml':
                    data = data.replace(b'b="1"', b'b="0"')
                b.writestr(info, data)
        self.assertEqual(reconstruction.validate_round_trip(self.source, self.output)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
