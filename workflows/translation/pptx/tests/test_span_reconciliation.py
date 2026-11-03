"""Pre-extraction caller contracts and real recorded-response format replay.

Synthetic caller tests isolate formatting from extraction and checkpoint I/O.
The recorded-response integration test uses real readers, extraction and run
validation; historical source/checkpoint bytes are never changed.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pptx_translation import active_run as ar
from pptx_translation import evaluation as ev
from pptx_translation import finalization as fin
from pptx_translation import pptx_helpers as ph
from pptx_translation import primary_translation as pt
from pptx_translation.workflow_config import WorkflowConfig
import checkpoint_fixtures as recorded
import test_evaluation as evaluation_fixtures

A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
SAVED_XML = ('<a:rPr xmlns:a="' + A + '" b="1" sz="1800">'
             '<a:solidFill><a:srgbClr val="FF0000"/></a:solidFill>'
             '<a:latin typeface="Aptos"/></a:rPr>')
CURRENT_XML = ('<q:rPr xmlns:q="' + A + '" sz="1800" b="1">\n  '
               '<q:solidFill><q:srgbClr val="FF0000" /></q:solidFill>\n  '
               '<q:latin typeface="Aptos" />\n</q:rPr>')


def source_units():
    return [dict(unit_key='u1', text='Hello\vworld', source_type='shape', slide_number=1,
                 styled_spans=[dict(span_id='u1.s1', kind='text', text='Hello', format_ref='u1.f1'),
                               dict(span_id='u1.s2', kind='soft_break', text='\v', format_ref='u1.f2'),
                               dict(span_id='u1.s3', kind='text', text='world', format_ref='u1.f1')],
                 span_formats={'u1.f1': SAVED_XML, 'u1.f2': None}),
            dict(unit_key='u2', text='Next', source_type='shape', slide_number=2,
                 styled_spans=[dict(span_id='u2.s1', kind='text', text='Next', format_ref='u2.f1')],
                 span_formats={'u2.f1': None})]


class SpanReconciliationCallerTests(unittest.TestCase):
    def setUp(self):
        self.saved = source_units()

    def invoke(self, caller, current, *, saved=None):
        saved = self.saved if saved is None else saved
        state = dict(units=saved, compatibility=dict(source_sha256='exact-source', units_sha256=pt._digest(saved)))
        with patch.object(pt, 'read_primary_state', return_value=state):
            if caller == 'active':
                run = object.__new__(ar.ActiveRun)
                run.state = dict(units_sha256=pt._digest(saved))
                run.sync = Mock()
                run._path = Mock(return_value=Path('primary.json'))
                result = run.primary_units(current)
                run.sync.assert_called_once_with()
                return result
            with patch.object(pt, '_file_hash', return_value='exact-source'), \
                 patch.object(fin, '_source_units', return_value=current):
                checked, original = fin._validate_primary('primary.json', 'source.pptx')
                self.assertIs(checked, state)
                self.assertIs(original, current)
                return original

    def test_equivalent_prefix_attribute_order_and_indentation_restore_saved_xml(self):
        for caller in ('active', 'finalization'):
            with self.subTest(caller=caller):
                current = deepcopy(self.saved)
                current[0]['span_formats']['u1.f1'] = CURRENT_XML
                before = deepcopy(current)
                saved_before = deepcopy(self.saved)
                result = self.invoke(caller, current)
                self.assertEqual(result, self.saved)
                self.assertEqual(pt._digest(result), pt._digest(self.saved))
                self.assertEqual(result[0]['span_formats']['u1.f1'], SAVED_XML)
                self.assertEqual(self.saved, saved_before)
                if caller == 'active':
                    self.assertIs(result, self.saved)
                    self.assertEqual(current, before)
                else:
                    self.assertIsNot(current[0]['span_formats'], self.saved[0]['span_formats'])

    def test_genuine_formatting_namespace_and_child_order_differences_fail(self):
        variants = [SAVED_XML.replace('b="1"', 'b="0"'),
                    SAVED_XML.replace('1800', '1900'), SAVED_XML.replace('Aptos', 'Arial'),
                    SAVED_XML.replace('FF0000', '000000'), SAVED_XML.replace(A, 'urn:other'),
                    SAVED_XML.replace('<a:solidFill><a:srgbClr val="FF0000"/></a:solidFill><a:latin typeface="Aptos"/>',
                                      '<a:latin typeface="Aptos"/><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill>'),
                    SAVED_XML.replace(' b="1"', ' dirty="1" b="1"')]
        for caller in ('active', 'finalization'):
            message = 'Run source formatting changed.' if caller == 'active' else 'Source span formatting changed.'
            for xml in variants:
                with self.subTest(caller=caller, xml=xml):
                    current = deepcopy(self.saved)
                    current[0]['span_formats']['u1.f1'] = xml
                    with self.assertRaisesRegex(ValueError, message):
                        self.invoke(caller, current)

    def test_missing_extra_and_non_mapping_format_coverage_fails(self):
        for caller in ('active', 'finalization'):
            message = 'Run formatting coverage changed.' if caller == 'active' else 'Source span formatting coverage mismatch.'
            for formats in (None, [], {}, {'u1.f1': SAVED_XML},
                            dict(self.saved[0]['span_formats'], extra=None)):
                with self.subTest(caller=caller, formats=formats):
                    current = deepcopy(self.saved)
                    current[0]['span_formats'] = formats
                    with self.assertRaisesRegex(ValueError, message):
                        self.invoke(caller, current)

    def test_different_malformed_xml_keeps_native_parse_errors(self):
        for caller in ('active', 'finalization'):
            for invalid_saved in (False, True):
                with self.subTest(caller=caller, invalid_saved=invalid_saved):
                    saved, current = deepcopy(self.saved), deepcopy(self.saved)
                    (saved if invalid_saved else current)[0]['span_formats']['u1.f1'] = '<broken'
                    with self.assertRaises(ET.ParseError):
                        self.invoke(caller, current, saved=saved)

    def test_equal_formats_skip_xml_parsing_including_identical_invalid_xml(self):
        for caller in ('active', 'finalization'):
            for formats in (None, {}, {'u1.f1': '<identical-invalid'}):
                saved = deepcopy(self.saved)
                saved[0]['span_formats'] = formats
                current = deepcopy(saved)
                with patch.object(ph, '_span_xml_signature', side_effect=AssertionError('Equal map must not be parsed')):
                    self.assertEqual(self.invoke(caller, current, saved=saved), saved)

    def test_missing_extra_reordered_spans_and_classification_remain_fatal(self):
        for caller in ('active', 'finalization'):
            for change in ('missing', 'extra', 'reordered', 'id', 'format_ref', 'text', 'classification'):
                with self.subTest(caller=caller, change=change):
                    current = deepcopy(self.saved)
                    spans = current[0]['styled_spans']
                    if change == 'missing': spans.pop()
                    elif change == 'extra': spans.append(deepcopy(spans[0]))
                    elif change == 'reordered': spans.reverse()
                    elif change == 'id': spans[0]['span_id'] = 'different'
                    elif change == 'format_ref': spans[0]['format_ref'] = 'u1.f2'
                    elif change == 'text': current[0]['text'] = 'Changed'
                    else: current[0]['source_type'] = 'different'
                    with self.assertRaisesRegex(ValueError, 'units/classification|Complete source units'):
                        self.invoke(caller, current)

    def test_unit_coverage_order_and_unit_key_checks_remain_distinct(self):
        for change in ('missing', 'extra', 'reordered', 'key'):
            current = deepcopy(self.saved)
            if change == 'missing': current.pop()
            elif change == 'extra': current.append(deepcopy(current[1]))
            elif change == 'reordered': current.reverse()
            else: current[0]['unit_key'] = 'unknown'
            active_error = ('Run source unit coverage changed.' if change in ('missing', 'extra')
                            else 'Run formatting coverage changed.' if change == 'reordered'
                            else 'Run source units/classification changed.')
            final_error = ('Source span formatting coverage mismatch.' if change == 'key'
                           else 'Complete source units/classification/formatting')
            with self.subTest(change=change, caller='active'):
                with self.assertRaisesRegex(ValueError, active_error):
                    self.invoke('active', deepcopy(current))
            with self.subTest(change=change, caller='finalization'):
                with self.assertRaisesRegex(ValueError, final_error):
                    self.invoke('finalization', deepcopy(current))

    def test_empty_format_values_and_absent_maps_preserve_existing_semantics(self):
        for caller in ('active', 'finalization'):
            current = deepcopy(self.saved)
            current[1]['span_formats']['u2.f1'] = ''
            self.assertEqual(self.invoke(caller, current), self.saved)
            saved = deepcopy(self.saved)
            del saved[0]['span_formats']
            self.assertEqual(self.invoke(caller, deepcopy(saved), saved=saved), saved)

    def test_active_without_primary_retains_original_list_and_exact_digest_gate(self):
        run = object.__new__(ar.ActiveRun)
        run.state = dict(units_sha256=pt._digest(self.saved))
        run.sync = Mock()
        run._path = Mock(return_value=None)
        with patch.object(pt, 'read_primary_state', side_effect=AssertionError('No checkpoint exists')):
            self.assertIs(run.primary_units(self.saved), self.saved)
            changed = deepcopy(self.saved)
            changed[0]['span_formats']['u1.f1'] = CURRENT_XML
            with self.assertRaisesRegex(ValueError, 'Run extraction/classification changed'):
                run.primary_units(changed)

    def test_finalization_rejects_source_hash_and_diagnostic_before_extraction(self):
        for diagnostic in (False, True):
            state = dict(units=self.saved, diagnostic=diagnostic,
                         compatibility=dict(source_sha256='exact-source', units_sha256=pt._digest(self.saved)))
            with patch.object(pt, 'read_primary_state', return_value=state), \
                 patch.object(pt, '_file_hash', return_value='different-source'), \
                 patch.object(fin, '_source_units', side_effect=AssertionError('Must fail before extraction')):
                message = 'Diagnostic primary ancestry' if diagnostic else 'Finalization source identity mismatch'
                with self.assertRaisesRegex(ValueError, message):
                    fin._validate_primary('primary.json', 'source.pptx')


class SpanReconciliationHelperTests(unittest.TestCase):
    def test_helper_changes_only_format_map_and_does_not_alias_saved_mapping(self):
        saved = source_units()[0]
        current = deepcopy(saved)
        current['span_formats']['u1.f1'] = CURRENT_XML
        current['unit_key'] = 'caller-owned-identity'
        current['styled_spans'].reverse()
        spans = current['styled_spans']
        before = deepcopy(current)
        saved_before = deepcopy(saved)
        result = ph.reconcile_span_formats(current, saved, coverage_error='coverage', formatting_error='formatting')
        self.assertIsNone(result)
        self.assertEqual(current, dict(before, span_formats=saved['span_formats']))
        self.assertIs(current['styled_spans'], spans)
        self.assertIsNot(current['span_formats'], saved['span_formats'])
        current['span_formats']['u1.f1'] = 'later change'
        self.assertEqual(saved, saved_before)

    def test_helper_retains_signature_hook_default_mode_and_caller_errors(self):
        saved = source_units()[0]
        current = deepcopy(saved)
        current['span_formats']['u1.f1'] = CURRENT_XML
        before = deepcopy(current)
        with patch.object(ph, '_span_xml_signature', side_effect=['current', 'saved']) as signature:
            with self.assertRaisesRegex(ValueError, '^caller formatting error$'):
                ph.reconcile_span_formats(current, saved, coverage_error='caller coverage error',
                                           formatting_error='caller formatting error')
        self.assertEqual(len(signature.call_args_list), 2)
        self.assertTrue(all(len(call.args) == 1 and not call.kwargs for call in signature.call_args_list))
        self.assertEqual(current, before)
        current['span_formats'] = {}
        with self.assertRaisesRegex(ValueError, '^caller coverage error$'):
            ph.reconcile_span_formats(current, saved, coverage_error='caller coverage error',
                                       formatting_error='caller formatting error')
        self.assertIs(fin.ET.ParseError, ph.ET.ParseError)

    def test_helper_requires_all_entries_before_restore_and_keeps_text_whitespace_strict(self):
        saved = source_units()[0]
        current = deepcopy(saved)
        current['span_formats']['u1.f1'] = CURRENT_XML
        current['span_formats']['u1.f2'] = '<a:rPr xmlns:a="' + A + '" i="1"/>'
        before = deepcopy(current)
        with self.assertRaisesRegex(ValueError, '^formatting$'):
            ph.reconcile_span_formats(current, saved, coverage_error='coverage', formatting_error='formatting')
        self.assertEqual(current, before)
        text_xml = '<a:rPr xmlns:a="' + A + '"><a:t> A </a:t></a:rPr>'
        saved = dict(span_formats={'ref': text_xml})
        current = dict(span_formats={'ref': text_xml.replace('> A <', '>A<')})
        with self.assertRaisesRegex(ValueError, '^formatting$'):
            ph.reconcile_span_formats(current, saved, coverage_error='coverage', formatting_error='formatting')




if __name__ == '__main__':
    unittest.main()
