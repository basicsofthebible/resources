"""Stage 2B synthetic Spanish mock coverage; no clients, checkpoints or requests."""
from collections import Counter
import json
from pathlib import Path
import tempfile

from pptx_translation.pptx_helpers import classify_pptx_text_units
from pptx_translation.pptx_reconstruction import reconstruct, source_units

MOCK_SPANISH = {
    'A small extraction example': 'Un ejemplo pequeño de extracción con una explicación considerablemente más extensa para comprobar el espacio disponible',
    'Second paragraph': 'Segundo párrafo con información adicional para comprobar la expansión del texto traducido',
    'Mixed formatting': 'Texto con formato mixto que requiere una revisión manual',
    'Label': 'Etiqueta descriptiva',
    'Value': 'Valor correspondiente',
    'Count': 'Cantidad total',
    'Grouped text': 'Texto dentro de una forma agrupada con una descripción más extensa',
    'John 3:16': 'Juan 3:16',
    'See Revelation 1:1 for context': 'Consulte Apocalipsis 1:1 para obtener una explicación más amplia del contexto',
    '1. Numbered label': '1. Etiqueta numerada con una descripción adicional',
    'Repeated label': 'Etiqueta repetida con información adicional',
}
PROTECTED_SAMPLE = {'42', '2026', 'https://example.org'}


def run_comprehensive_mock(source, output_root, units=None, translations=None, *, wrap_slide1=False):
    """Attempt every eligible sample paragraph and account for every source unit.

    Classification is recomputed using the existing rules. Protected units never
    receive replacements. Missing/blank mock values are unresolved, not silently
    treated as preserved. Unsafe runs stay unchanged with manual_review status.
    translations optionally replaces the complete mock lookup for failure tests.
    wrap_slide1 explicitly enables wrapping only for slide 1 textbox 1:2, when
    it has an eligible mock request. Default reconstruction remains unchanged.
    This fixture harness accepts only the unchanged six-slide synthetic content.
    """
    units = list(units) if units is not None else source_units(source)
    actual = source_units(source)
    if (len(actual) != 15 or {u['slide_number'] for u in actual} != set(range(1, 7))
            or {u['text'] for u in actual} != set(MOCK_SPANISH) | PROTECTED_SAMPLE):
        raise ValueError('Stage 2B requires the unchanged six-slide synthetic sample.')
    if len(units) != len(actual) or {u.get('unit_key') for u in units} != {u['unit_key'] for u in actual}:
        raise ValueError('Stage 2B requires complete source-unit coverage.')
    classified = classify_pptx_text_units(units)
    translations = MOCK_SPANISH if translations is None else translations
    requests, rows = [], []
    for unit in classified:
        row = {key: unit[key] for key in ('unit_key', 'slide_number', 'shape_path', 'source_type',
                                          'paragraph_index', 'row_index', 'col_index', 'text_kind')}
        row.update(source_text=unit['text'], requested_text=None)
        if unit['preserve_source']:
            row.update(status='preserved', reason=unit['preserve_reason'])
        else:
            target = translations.get(unit['text'])
            if not isinstance(target, str) or not target.strip() or target == unit['text']:
                row.update(status='unresolved', reason='Missing, blank or unchanged mock translation.')
            else:
                requests.append(dict(unit_key=unit['unit_key'], text=target))
                row.update(status='pending', reason='', requested_text=target)
        rows.append(row)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='stage2b_', dir=output_root))
    overrides = [dict(slide_number=1, shape_path='1:2')] if wrap_slide1 else []
    validation = reconstruct(source, run_dir / 'comprehensive_mock_spanish.pptx', classified, requests,
                             wrap_overrides=overrides)
    manual = {item['unit_key']: item['reason'] for item in validation['manual_review']}
    applied = set(validation['replaced'])
    reopened = {u['unit_key']: u['text'] for u in source_units(validation['output'])}
    for row in rows:
        key = row['unit_key']
        if row['status'] == 'pending':
            if key in manual:
                row.update(status='manual_review', reason=manual[key])
            elif key in applied:
                row.update(status='applied', reason='Mock translation applied and round-trip checked.')
            else:
                row.update(status='unresolved', reason='Reconstruction did not apply requested translation.')
        row['output_text'] = reopened.get(key)
        row['expansion_ratio'] = round(len(row['output_text'] or '') / len(row['source_text']), 2)
        expected = row['requested_text'] if row['status'] == 'applied' else row['source_text']
        row['verified'] = row['output_text'] == expected
        if not row['verified']:
            validation['failures'].append(f'Per-unit output mismatch: {key}')
    counts = {status: 0 for status in ('applied', 'preserved', 'unresolved', 'manual_review')}
    counts.update(Counter(row['status'] for row in rows))
    status = ('failed' if validation['failures'] else 'unresolved' if counts['unresolved']
              else 'manual_review' if counts['manual_review'] else 'passed')
    report = dict(status=status, counts=counts, units=rows, validation=validation,
                  output=validation['output'], report=str(run_dir / 'per_unit_report.json'),
                  limitations=['Mock text is deterministic, not evaluated translation.',
                               'Open in PowerPoint to check expansion, clipping and overflow; no automatic fitting.',
                               'Mixed runs/hyperlinks remain protected by Stage 2A; SmartArt excluded.'])
    (run_dir / 'per_unit_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
