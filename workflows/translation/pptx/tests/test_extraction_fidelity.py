"""Soft-break fidelity: read-only extraction, not reconstruction or translation."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import pptx_helpers as ph


class ParagraphTextTests(unittest.TestCase):
    def test_paragraph_text_keeps_breaks_omitted_by_runs(self):
        paragraph = NS(text='First\vSecond\vThird',
                       runs=[NS(text='First'), NS(text='Second'), NS(text='Third')])
        self.assertEqual(ph._paragraph_text(paragraph), 'First\vSecond\vThird')

    def test_paragraph_boundaries_and_empty_paragraphs_remain_separate(self):
        frame = NS(paragraphs=[NS(text='First\vSecond', runs=[NS(text='First'), NS(text='Second')]),
                               NS(text='Third', runs=[NS(text='Third')]), NS(text='', runs=[])])
        records = ph.extract_text_from_text_frame(frame)
        self.assertEqual([r['text'] for r in records], ['First\vSecond', 'Third', ''])
        self.assertEqual([r['paragraph_index'] for r in records], [1, 2, 3])
        self.assertEqual([r['run_count'] for r in records], [2, 1, 0])
        self.assertTrue(records[2]['is_empty'])


@unittest.skipUnless(ph.check_python_pptx_available()[0], 'python-pptx is not installed')
class PythonPptxFidelityTests(unittest.TestCase):
    def setUp(self):
        from pptx import Presentation
        from pptx.util import Inches
        self.prs = Presentation()
        self.slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
        self.box = self.slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(2))

    def test_soft_break_vs_paragraph_in_real_xml_preserves_formatting_and_link(self):
        self.box.text_frame.text = 'First\vSecond\nThird'
        paragraph = self.box.text_frame.paragraphs[0]
        paragraph.runs[0].font.bold = True
        paragraph.runs[1].hyperlink.address = 'https://example.org'
        xml_before = self.box._element.xml
        rels_before = [(r.rId, r.target_ref) for r in self.slide.part.rels.values()]
        units = ph.build_pptx_text_units(self.prs)
        self.assertEqual([u['text'] for u in units], ['First\vSecond', 'Third'])
        self.assertEqual([u['paragraph_index'] for u in units], [1, 2])
        self.assertEqual([u['unit_key'] for u in units], ['s001_u0001', 's001_u0002'])
        self.assertEqual(len(paragraph.runs), 2)
        self.assertEqual(len(paragraph._p.findall('{http://schemas.openxmlformats.org/drawingml/2006/main}br')), 1)
        classified = ph.classify_pptx_text_units(units)
        self.assertEqual(classified[0]['text'], 'First\vSecond')
        self.assertEqual(self.box._element.xml, xml_before)
        self.assertEqual([(r.rId, r.target_ref) for r in self.slide.part.rels.values()], rels_before)
        inspection = ph.inspect_presentation_structure(self.prs)
        self.assertEqual(ph.reconcile_extraction_counts(inspection, units)['difference'], 0)
        self.assertTrue(ph.inspect_text_unit_paragraph_structure(self.prs, units)[0]['contains_line_break'])
        self.assertTrue(paragraph.runs[0].font.bold)
        self.assertEqual(paragraph.runs[1].hyperlink.address, 'https://example.org')

    def test_table_and_group_keep_soft_breaks_without_extra_units(self):
        from pptx.util import Inches
        self.box.text_frame.text = 'First\vSecond\nThird'
        table = self.slide.shapes.add_table(1, 1, Inches(1), Inches(3), Inches(4), Inches(1)).table
        table.cell(0, 0).text = 'Cell first\vCell second\nNext cell paragraph'
        group = self.slide.shapes.add_group_shape()
        child = group.shapes.add_textbox(Inches(1), Inches(4), Inches(4), Inches(1))
        child.text = 'Grouped first\vGrouped second'
        xml_before = self.slide._element.xml
        units = ph.build_pptx_text_units(self.prs)
        self.assertEqual(len(units), 5)
        self.assertEqual([u['text'] for u in units],
                         ['First\vSecond', 'Third', 'Cell first\vCell second',
                          'Next cell paragraph', 'Grouped first\vGrouped second'])
        self.assertEqual([u['source_type'] for u in units],
                         ['text_frame_paragraph'] * 2 + ['table_cell_paragraph'] * 2 +
                         ['grouped_shape_text_frame_paragraph'])
        self.assertEqual([u['paragraph_index'] for u in units], [1, 2, 1, 2, 1])
        self.assertEqual(len({u['unit_key'] for u in units}), 5)
        self.assertEqual(self.slide._element.xml, xml_before)


if __name__ == '__main__':
    unittest.main()
