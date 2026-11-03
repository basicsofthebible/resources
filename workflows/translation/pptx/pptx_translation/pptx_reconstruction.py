"""Offline reconstruction. Patch explicitly selected paragraph or styled-span text.

Unchanged paragraphs are never rewritten. Changed paragraphs must contain exactly
one a:r with one a:t by default. Explicit span responses support styled runs,
hyperlinks and structural tokens without changing their XML properties.
This deliberately conservative policy preserves run/paragraph properties and
never guesses how a translation should inherit mixed formatting. Identity-mapped IconCircleLabelList SmartArt uses synchronized data/cache text. ZIP members outside changed slides retain their original bytes.
"""
import hashlib
from pathlib import Path
import posixpath
import re
from . import validation_policy as vp
from xml.etree import ElementTree as ET
from xml.dom import minidom
from xml.sax.saxutils import quoteattr
from io import StringIO
from zipfile import ZipFile
from .pptx_helpers import describe_styled_spans, _span_source_hash, _span_xml_signature

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
for _prefix, _uri in NS.items():
    ET.register_namespace(_prefix, _uri)
SUPPORTED = {'text_frame_paragraph', 'grouped_shape_text_frame_paragraph', 'table_cell_paragraph', 'smartart_data_point_text'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _slides(archive):
    rels = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
    targets = {r.get('Id'): posixpath.normpath(posixpath.join('ppt', r.get('Target')))
               if not r.get('Target', '').startswith('/') else r.get('Target').lstrip('/')
               for r in rels if r.get('TargetMode') != 'External'}
    presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
    return [targets[s.get('{%s}id' % NS['r'])] for s in presentation.findall('p:sldIdLst/p:sldId', NS)]


def _shapes(parent, prefix=''):
    shapes = [s for s in parent if s.tag in {f"{{{NS['p']}}}{name}" for name in
              ('sp', 'grpSp', 'graphicFrame', 'cxnSp', 'pic', 'contentPart')}]
    for index, shape in enumerate(shapes, 1):
        props = shape.find('.//p:cNvPr', NS)
        shape_id = int(props.get('id'))
        path = f'{prefix}/{index}:{shape_id}' if prefix else f'{index}:{shape_id}'
        yield shape, path, shape_id
        if shape.tag == f"{{{NS['p']}}}grpSp":
            yield from _shapes(shape, path)


def _records(archive):
    """Enumerate supported paragraphs using Stage 1's one-based locators."""
    roots, records = {}, []
    for slide_number, part in enumerate(_slides(archive), 1):
        root = roots[part] = ET.fromstring(archive.read(part))
        unit_index = 0
        for shape, path, shape_id in _shapes(root.find('p:cSld/p:spTree', NS)):
            bodies = []
            body = shape.find('p:txBody', NS)
            if body is not None:
                bodies.append((body, None, None, 'grouped_shape_text_frame_paragraph' if '/' in path else 'text_frame_paragraph'))
            table = shape.find('a:graphic/a:graphicData/a:tbl', NS)
            if table is not None:
                for row_index, row in enumerate(table.findall('a:tr', NS), 1):
                    for col_index, cell in enumerate(row.findall('a:tc', NS), 1):
                        bodies.append((cell.find('a:txBody', NS), row_index, col_index, 'table_cell_paragraph'))
            for body, row, col, source_type in bodies:
                for paragraph_index, paragraph in enumerate(body.findall('a:p', NS), 1):
                    text = ''.join('\v' if child.tag == f"{{{NS['a']}}}br" else ''.join(child.itertext())
                                   for child in paragraph if child.tag in {f"{{{NS['a']}}}{n}" for n in ('r', 'fld', 'br')})
                    if not text.strip():
                        continue
                    unit_index += 1
                    unit = dict(unit_key=f's{slide_number:03d}_u{unit_index:04d}', slide_number=slide_number,
                                shape_path=path, shape_id=shape_id, source_type=source_type,
                                paragraph_index=paragraph_index, row_index=row, col_index=col, text=text)
                    records.append((unit, paragraph, part))
    # Append supported native SmartArt targets using the existing extraction IDs.
    from .pptx_helpers import extract_smartart_text_units, _max_unit_index_by_slide
    from .smartart_xml import paragraphs
    for extracted in extract_smartart_text_units(archive.filename, _max_unit_index_by_slide(u for u, _, _ in records)):
        try:
            targets, label = paragraphs(archive, extracted, roots)
        except ValueError:
            continue  # Unsupported diagrams remain non-target package parts.
        paragraph, part = targets[0]
        text = ''.join(n.text or '' for n in paragraph.findall('a:r/a:t', NS))
        fields = ('unit_key', 'slide_number', 'shape_path', 'shape_id', 'source_type',
                  'paragraph_index', 'row_index', 'col_index', 'smartart_data_part',
                  'smartart_drawing_part', 'smartart_model_id', 'smartart_graphic_frame_index', 'smartart_point_index')
        unit = {f: extracted[f] for f in fields}
        unit.update(text=text, smartart_text_version=1, smartart_cached_shape_id=label['cached_shape_id'],
                    smartart_drawing_part=targets[1][1])
        records.append((unit, paragraph, part))
    return roots, records


def source_units(path):
    """Read supported source locators for offline fixtures (not classification)."""
    with ZipFile(path) as archive:
        return [u for u, _, _ in _records(archive)[1]]


def _signature(element):
    return (element.tag, tuple(sorted(element.attrib.items())), element.text or '',
            tuple(_signature(child) for child in element))


def _serialize_patched(original_bytes, root):
    # DOM keeps every original namespace declaration, including prefixes referenced
    # only inside mc:Ignorable attribute values. ElementTree alone drops those.
    document = minidom.parseString(original_bytes)
    nodes = document.getElementsByTagNameNS(NS['a'], 't')
    texts = list(root.iter('{%s}t' % NS['a']))
    if len(nodes) != len(texts):
        raise ValueError('Unexpected text-node structure.')
    for node, element in zip(nodes, texts):
        old = ''.join(c.data for c in node.childNodes if c.nodeType in (c.TEXT_NODE, c.CDATA_SECTION_NODE))
        if old != (element.text or ''):
            for child in list(node.childNodes):
                node.removeChild(child)
            node.appendChild(document.createTextNode(element.text or ''))
    bodies = document.getElementsByTagNameNS(NS['a'], 'bodyPr')
    for node, element in zip(bodies, root.iter('{%s}bodyPr' % NS['a'])):
        # Only wrap is eligible for an explicit property override.
        if element.get('wrap') is not None:
            node.setAttribute('wrap', element.get('wrap'))
    # minidom's default writer emits literal attribute LF/CR/TAB characters.
    # XML reparsing normalizes those to spaces; quoteattr retains character refs.
    stream = StringIO()
    stream.write('<?xml version="1.0" encoding="utf-8"?>')
    def write_node(node):
        if node.nodeType != node.ELEMENT_NODE:
            node.writexml(stream)
            return
        stream.write('<' + node.tagName)
        for name, value in node.attributes.items():
            stream.write(' ' + name + '=' + quoteattr(value))
        if not node.childNodes:
            stream.write('/>')
            return
        stream.write('>')
        for child in node.childNodes:write_node(child)
        stream.write('</' + node.tagName + '>')
    for child in document.childNodes:write_node(child)
    return stream.getvalue().encode('utf-8')


def _wrap_body(roots, slide_number, shape_path):
    if type(slide_number) is not int or not 1 <= slide_number <= len(roots):
        raise ValueError('Invalid wrapping override slide.')
    part = list(roots)[slide_number - 1]
    shapes = _shapes(roots[part].find('p:cSld/p:spTree', NS))
    shape = next((shape for shape, path, _ in shapes if path == shape_path), None)
    body = shape.find('p:txBody/a:bodyPr', NS) if shape is not None else None
    if body is None:
        raise ValueError('Wrapping override must identify a textbox text frame.')
    return body, part


def _apply_span_replacement(unit, paragraph, answer, *, policy_version=vp.CURRENT):
    key = unit['unit_key']
    spans, templates = describe_styled_spans(paragraph, key)
    saved_formats = unit.get('span_formats', {})
    formats_match = saved_formats.keys() == templates.keys() and all(
        _span_xml_signature(ET.fromstring(xml) if xml else None) ==
        _span_xml_signature(ET.fromstring(saved_formats[ref]) if saved_formats[ref] else None)
        for ref, xml in templates.items())
    if (spans != unit.get('styled_spans') or not formats_match
            or _span_source_hash(paragraph) != unit.get('span_source_hash')):
        raise ValueError(f'Stale or missing span metadata: {key}')
    span_checks, expected_text, run_texts = [], {}, {}
    translations = answer.get('translations', [])
    text_ids = [s['span_id'] for s in spans if s['kind'] == 'text']
    if not isinstance(translations, list) or any(not isinstance(t, dict) for t in translations) or [t.get('span_id') for t in translations] != text_ids:
        raise ValueError(f'Missing, duplicate, reordered or structural span IDs: {key}')
    translated = {t['span_id']: t.get('text') for t in translations}
    chunks = {}
    paragraph_result = []
    for span in spans:
        value = translated[span['span_id']] if span['kind'] == 'text' else span['text']
        if span['kind'] == 'text':
            issue=vp.text_error(span['text'],value,policy_version=policy_version,check_tokens=False)
            if issue in ('missing_or_blank_text','invalid_control_character'):
                raise ValueError(f'Blank or control-containing span: {key}/{span["span_id"]}')
            if issue=='significant_unicode_changed':raise ValueError('Significant Unicode characters must be retained.')
            if issue=='boundary_whitespace_changed':raise ValueError('Boundary whitespace must be retained.')
            if issue=='numeric_markers_changed':raise ValueError('Reference digits and numeric markers must be retained.')
        paragraph_result.append(value)
        check = dict(unit_key=key, span_id=span['span_id'], kind=span['kind'], format_ref=span['format_ref'],
                     source_text=span['text'], expected_text=value, output_fragments=[])
        if span['kind'] == 'soft_break':
            check['source_xml_child_index'] = span['source_xml_child_index']
        else:
            for i, fragment in enumerate(span['source_fragments']):
                source_text = paragraph.findall('a:r', NS)[fragment['run_index']-1].find('a:t', NS).text or ''
                chunk = (source_text[fragment['start']:fragment['end']] if value == span['text']
                         else value if i == 0 else '')
                chunks.setdefault(fragment['run_index'], []).append((fragment, chunk, check))
        span_checks.append(check)
    expected_text[key] = ''.join(paragraph_result)
    runs = paragraph.findall('a:r', NS)
    for index, run in enumerate(runs, 1):
        node = run.find('a:t', NS)
        if node is None:
            continue  # No text node to alter in an empty run.
        old, cursor, text = node.text or '', 0, ''
        for fragment, chunk, check in chunks.get(index, []):
            if fragment['start'] != cursor or not cursor < fragment['end'] <= len(old):
                raise ValueError('Invalid or overlapping source fragments.')
            check['output_fragments'].append(dict(run_index=index, start=len(text), end=len(text)+len(chunk)))
            text += chunk
            cursor = fragment['end']
        if cursor != len(old):
            raise ValueError('Incomplete run coverage.')
        run_texts[index] = text
        node.text = text
    return expected_text[key], run_texts, span_checks


def reconstruct(source, output, units, replacements, *, wrap_overrides=(), span_replacements=(),
                validation_policy_version=vp.CURRENT, validation_policy_by_unit=None):
    """Write a new PPTX and return a round-trip report.

    replacements is a sequence of {unit_key, text} records, allowing detection of
    duplicate requests. Invalid/stale identifiers abort before any output is
    created. Unsafe formatting targets stay unchanged and require manual review.
    Existing output paths are refused using exclusive creation.
    Optional wrap_overrides records {slide_number, shape_path} enable wrapping
    only on explicitly named textboxes with successfully changed text. No auto-fit
    or geometry changes are made; the default empty tuple preserves all settings.
    span_replacements optionally supplies {unit_key, translations: [{span_id,
    text}]} records for units enriched by include_styled_spans=True. Text-span
    IDs must occur exactly once in source order; structural items are immutable.
    """
    vp.require_policy(validation_policy_version)
    policy_by_unit=dict(validation_policy_by_unit or {})
    for version in policy_by_unit.values():vp.require_policy(version)
    if set(policy_by_unit)-{u['unit_key'] for u in units}:raise ValueError('Unknown per-unit validation policy target.')
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError('Output must differ from source.')
    if output.suffix.lower() != '.pptx':
        raise ValueError('Output must be a .pptx file.')
    if output.exists():
        raise FileExistsError(output)
    before = sha256(source)
    units = list(units)
    keys = [u.get('unit_key') for u in units]
    if any(not isinstance(k, str) or not k for k in keys) or len(keys) != len(set(keys)):
        raise ValueError('Missing or duplicate unit identifiers.')
    supplied = dict(zip(keys, units))
    requests = list(replacements)
    requested_keys = [r.get('unit_key') for r in requests]
    if any(not isinstance(k, str) or not k for k in requested_keys) or len(requested_keys) != len(set(requested_keys)):
        raise ValueError('Missing or duplicate replacement identifiers.')
    span_requests = list(span_replacements)
    span_keys = [r.get('unit_key') for r in span_requests]
    if (any(not isinstance(k, str) or not k for k in span_keys) or len(span_keys) != len(set(span_keys))
            or set(span_keys) & set(requested_keys)):
        raise ValueError('Missing, duplicate or conflicting paragraph/span targets.')
    with ZipFile(source) as archive:
        roots, records = _records(archive)
        actual = {u['unit_key']: (u, p, part) for u, p, part in records}
        destinations = set()
        for key, unit in supplied.items():
            if unit.get('source_type') not in SUPPORTED:
                raise ValueError(f'Unsupported target type: {key}')
            if key not in actual:
                raise ValueError(f'Unknown target: {key}')
            original = actual[key][0]
            if unit['source_type'] == 'smartart_data_point_text' and ('styled_spans' not in unit or key not in span_keys):
                raise ValueError('SmartArt requires identity-mapped styled-span replacement: ' + key)
            if any(unit.get(field) != value for field, value in original.items()):
                raise ValueError(f'Invalid or stale locator/source: {key}')
            destination = tuple(original[f] for f in ('slide_number', 'shape_path', 'row_index', 'col_index', 'paragraph_index'))
            if unit['source_type'] == 'smartart_data_point_text':
                destination = (original['smartart_data_part'], original['smartart_model_id'])
            if destination in destinations:
                raise ValueError(f'Duplicate target destination: {key}')
            destinations.add(destination)
        changed, manual, unchanged, parts = {}, [], [], set()
        for request in requests:
            key, text = request['unit_key'], request.get('text')
            if key not in supplied or not isinstance(text, str):
                raise ValueError(f'Unknown target or invalid replacement text: {key}')
            unit, paragraph, part = actual[key]
            if text == unit['text']:
                unchanged.append(key)
                continue
            children = [c for c in paragraph if c.tag not in {f"{{{NS['a']}}}pPr", f"{{{NS['a']}}}endParaRPr"}]
            runs = paragraph.findall('a:r', NS)
            safe = (len(children) == len(runs) == 1 and len(runs[0].findall('a:t', NS)) == 1
                    and not any('hlink' in e.tag for e in paragraph.iter())
                    and bool(text.strip())
                    and not re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', text))
            if not safe:
                manual.append(dict(unit_key=key, slide_number=unit['slide_number'], shape_path=unit['shape_path'],
                                   reason='Replacement requires one plain run, no hyperlinks/fields/breaks/control characters.'))
                continue
            runs[0].find('a:t', NS).text = text
            changed[key] = text
            parts.add(part)
        expected_run_texts, span_checks = {}, []
        for answer in span_requests:
            key = answer['unit_key']
            if key not in supplied:
                raise ValueError(f'Unknown span target: {key}')
            unit, paragraph, part = actual[key]
            original_run_texts = {i: node.text or '' for i, run in enumerate(paragraph.findall('a:r', NS), 1)
                                  if (node := run.find('a:t', NS)) is not None}
            text, run_texts, checks = _apply_span_replacement(supplied[key], paragraph, answer,policy_version=policy_by_unit.get(key,validation_policy_version))
            span_checks.extend(checks)
            if run_texts == original_run_texts:
                unchanged.append(key)
            else:
                changed[key] = text
                expected_run_texts[key] = run_texts
                parts.add(part)
            if unit['source_type'] == 'smartart_data_point_text':
                from .smartart_xml import paragraphs
                targets, _ = paragraphs(archive, supplied[key], roots)
                cached, cache_part = targets[1]
                for index, run in enumerate(cached.findall('a:r', NS), 1):
                    run.find('a:t', NS).text = run_texts[index]
                if key in changed: parts.add(cache_part)
        intentional_changes, wrap_targets = [], set()
        for override in wrap_overrides:
            target = (override.get('slide_number'), override.get('shape_path'))
            if target in wrap_targets:
                raise ValueError('Duplicate wrapping override target.')
            wrap_targets.add(target)
            body, part = _wrap_body(roots, *target)
            if not any((actual[key][0]['slide_number'], actual[key][0]['shape_path']) == target
                       for key in changed):
                raise ValueError('Wrapping override requires successfully translated textbox content.')
            previous = body.get('wrap')
            body.set('wrap', 'square')
            parts.add(part)
            intentional_changes.append(dict(slide_number=target[0], shape_path=target[1],
                                            property='word_wrap', before=previous, after='square'))
        payloads = {part: _serialize_patched(archive.read(part), roots[part]) for part in parts}
        if sha256(source) != before:
            raise RuntimeError('Source changed during reconstruction.')
        # Exclusive creation also protects against a competing writer after exists().
        with output.open('xb') as stream:
            try:
                with ZipFile(stream, 'w') as result:
                    for info in archive.infolist():
                        result.writestr(info, payloads.get(info.filename, archive.read(info.filename)))
            except Exception:
                output.unlink(missing_ok=True)
                raise
    report = validate_round_trip(source, output, changed, intentional_changes=intentional_changes,
                                 expected_run_texts=expected_run_texts, span_checks=span_checks)
    report.update(output=str(output), replaced=list(changed), unchanged=unchanged, manual_review=manual,
                  source_unchanged=sha256(source) == before,
                  changed_text_spans=sum(c['kind'] == 'text' and c['source_text'] != c['expected_text'] for c in span_checks))
    if not report['source_unchanged']:
        report['failures'].append('Source hash changed.')
    report['status'] = 'failed' if report['failures'] else 'manual_review' if manual else 'passed'
    return report


def validate_round_trip(source, output, expected_changes=None, *, intentional_changes=(),
                        expected_run_texts=None, span_checks=()):
    """Verify text/locators plus package structure and all XML formatting properties.

    Only expected a:t values and explicitly reported wrapping overrides may differ. Untouched ZIP parts must be byte-identical;
    changed slides must be semantically identical after restoring expected text.
    Visual layout/overflow still requires PowerPoint review.
    """
    expected_changes = expected_changes or {}
    expected_run_texts = expected_run_texts or {}
    failures, successes, smartart_checks = [], [], []
    with ZipFile(source) as original, ZipFile(output) as rebuilt:
        before_roots, before = _records(original)
        after_roots, after = _records(rebuilt)
        b = {u['unit_key']: (u, p, part) for u, p, part in before}
        a = {u['unit_key']: (u, p, part) for u, p, part in after}
        if b.keys() != a.keys():
            failures.append('Extracted target identifiers differ.')
        for key, (unit, _, _) in b.items():
            expected = dict(unit, text=expected_changes.get(key, unit['text']))
            if key not in a or a[key][0] != expected:
                failures.append(f'Text or locator mismatch: {key}')
        span_results = []
        for check in span_checks:
            check = dict(check)
            try:
                paragraph = a[check['unit_key']][1]
                if check['kind'] == 'soft_break':
                    node = list(paragraph)[check['source_xml_child_index'] - 1]
                    value = '\v' if node.tag == f"{{{NS['a']}}}br" else None
                else:
                    runs = paragraph.findall('a:r', NS)
                    value = ''.join((runs[f['run_index'] - 1].find('a:t', NS).text or '')[f['start']:f['end']]
                                    for f in check['output_fragments'])
            except (KeyError, IndexError, AttributeError):
                value = None
            check.update(actual_text=value, passed=value == check['expected_text'])
            span_results.append(check)
            if not check['passed']:
                failures.append(f"Span boundary/text mismatch: {check['unit_key']}/{check['span_id']}")
        changed_parts = set()
        for key in expected_changes:
            if key not in a or key not in b:
                failures.append(f'Missing expected target: {key}')
                continue
            paragraph, part = a[key][1:]
            if key in expected_run_texts:
                old_runs = b[key][1].findall('a:r', NS)
                new_runs = paragraph.findall('a:r', NS)
                if len(old_runs) != len(new_runs):
                    failures.append(f'Run count mismatch: {key}')
                for index, (old_run, new_run) in enumerate(zip(old_runs, new_runs), 1):
                    old_node, new_node = old_run.find('a:t', NS), new_run.find('a:t', NS)
                    if old_node is None or new_node is None:
                        if (old_node is None) != (new_node is None):
                            failures.append(f'Text-node structure mismatch: {key}/{index}')
                        continue
                    if (new_node.text or '') != expected_run_texts[key].get(index):
                        failures.append(f'Run text mismatch: {key}/{index}')
                    new_node.text = old_node.text
                if b[key][0]['source_type'] == 'smartart_data_point_text':
                    from .smartart_xml import paragraphs
                    old_targets, _ = paragraphs(original, b[key][0], before_roots)
                    new_targets, _ = paragraphs(rebuilt, a[key][0], after_roots)
                    old_cache = old_targets[1][0].findall('a:r/a:t', NS)
                    new_cache = new_targets[1][0].findall('a:r/a:t', NS)
                    if len(old_cache) != len(new_cache): failures.append('SmartArt cache run count mismatch: ' + key)
                    for i, (old_node, new_node) in enumerate(zip(old_cache, new_cache), 1):
                        if (new_node.text or '') != expected_run_texts[key].get(i):
                            failures.append('SmartArt cache text mismatch: ' + key)
                        new_node.text = old_node.text
                    changed_parts.add(new_targets[1][1])
                    smartart_checks.append(dict(unit_key=key, data_point_id=b[key][0]['smartart_model_id'],
                        cached_shape_id=b[key][0]['smartart_cached_shape_id'], data_part=part,
                        drawing_part=new_targets[1][1], synchronized=True))
                changed_parts.add(part)
                continue
            text_nodes = paragraph.findall('a:r/a:t', NS)
            if len(text_nodes) != 1:
                failures.append(f'Run structure mismatch: {key}')
                continue
            text_nodes[0].text = b[key][0]['text']
            changed_parts.add(part)
        for change in intentional_changes:
            target = (change['slide_number'], change['shape_path'])
            source_body, _ = _wrap_body(before_roots, *target)
            output_body, part = _wrap_body(after_roots, *target)
            if (change.get('property') != 'word_wrap' or change.get('after') != 'square'
                    or change.get('before') != source_body.get('wrap')
                    or output_body.get('wrap') != 'square'):
                failures.append(f'Wrapping override mismatch: {target}')
            previous = source_body.get('wrap')
            if previous is None:
                output_body.attrib.pop('wrap', None)
            else:
                output_body.set('wrap', previous)
            changed_parts.add(part)
        if rebuilt.testzip() is not None:
            failures.append('ZIP integrity check failed.')
        if original.namelist() != rebuilt.namelist():
            failures.append('Package member order/names differ.')
        for name in original.namelist():
            if name not in rebuilt.namelist():
                continue
            if name in changed_parts:
                if _signature(ET.fromstring(original.read(name))) != _signature(after_roots[name]):
                    failures.append(f'Structure or formatting mismatch: {name}')
            elif original.read(name) != rebuilt.read(name):
                failures.append(f'Non-target package part changed: {name}')
        if not failures:
            successes = ['Text and locators match expected values.',
                         'Slide/shape structure, run/paragraph formatting and relationships preserved except reported overrides.',
                         'Non-target package parts unchanged.']
    return dict(status='failed' if failures else 'passed', unit_count=len(before),
                successes=successes, failures=failures, intentional_changes=list(intentional_changes),
                span_checks=span_results, smartart_checks=smartart_checks,
                visual_review='Open in PowerPoint to check appearance and text overflow; Supported SmartArt data/cache text synchronized; visual review required.')


def run_sample_round_trips(source, output_root, units=None):
    """Run the two Stage 2A scenarios only on the known synthetic fixture."""
    import tempfile
    units = list(units) if units is not None else source_units(source)
    mock = {'A small extraction example': 'Un pequeño ejemplo de extracción',
            'Grouped text': 'Texto agrupado', 'Label': 'Etiqueta'}
    expected_texts = {'A small extraction example', 'Second paragraph', 'Mixed formatting',
                     'Label', 'Value', 'Count', '42', 'Grouped text', 'John 3:16',
                     'See Revelation 1:1 for context', '1. Numbered label',
                     'https://example.org', '2026', 'Repeated label'}
    if len(units) != 15 or {u['text'] for u in units} != expected_texts:
        raise ValueError('Stage 2A demonstration requires the unchanged six-slide synthetic sample.')
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='stage2a_', dir=output_root))
    identity = reconstruct(source, run_dir / 'identity.pptx', units,
                           [dict(unit_key=u['unit_key'], text=u['text']) for u in units])
    requests = [dict(unit_key=u['unit_key'], text=mock[u['text']]) for u in units if u['text'] in mock]
    # Exercise the conservative refusal policy as part of the visible report.
    requests += [dict(unit_key=u['unit_key'], text='Formato mixto') for u in units if u['text'] == 'Mixed formatting']
    controlled = reconstruct(source, run_dir / 'controlled_replacements.pptx', units, requests)
    reports = dict(identity=identity, controlled=controlled)
    import json
    (run_dir / 'validation.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    return reports
