"""Stage 3F-2: validated finalization adapter for maintained PPTX reconstruction.

Offline only. No provider clients, annotations, fitting or recovery orchestration.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
from . import artifact_paths

from . import primary_translation as pt
from . import finalization as fin
from . import pptx_helpers as ph
from .pptx_reconstruction import reconstruct, SUPPORTED

VERSION = 1


def _resolve_input(record, source_override=None):
    """Find only the explicitly recorded input with the exact saved identity."""
    return artifact_paths.resolve_input(record, source_override, module_file=__file__,
                                        path_factory=Path, hash_function=pt._file_hash)


def read_validated_snapshot(snapshot_path, source_path=None):
    """Revalidate ancestry and reproduce every selection, independently of export."""
    path = Path(snapshot_path).resolve()
    snapshot_hash = pt._file_hash(path)
    snapshot = json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity = snapshot.pop('integrity',None)
    if (integrity!=pt._digest(snapshot) or snapshot.get('schema_version')!=fin.VERSION
            or snapshot.get('stage')!='offline_finalization'):
        raise ValueError('Invalid finalization snapshot integrity/version/stage.')
    ancestry = snapshot['ancestry']
    if not {'source','primary','primary_evaluation'} <= set(ancestry) or set(ancestry)-{
            'source','primary','primary_evaluation','fallback','fallback_evaluation'}:
        raise ValueError('Invalid finalization ancestry.')
    resolved = {key:_resolve_input(record,source_path if key=='source' else None)
                for key,record in ancestry.items()}
    manual = {r['unit_key']:r['manual_followup_reason'] for r in snapshot['units'] if r['needs_manual_followup']}
    rebuilt = fin.build_finalization_snapshot(resolved['primary'],resolved['primary_evaluation'],resolved['source'],
                  fallback_checkpoint=resolved.get('fallback'),fallback_evaluation_checkpoint=resolved.get('fallback_evaluation'),
                  manual_followup=manual)
    if snapshot.get('primary_offline_recovery')!=rebuilt.get('primary_offline_recovery'):
        raise ValueError('Finalization offline recovery ancestry mismatch.')
    for field in ('fallback_offline_recovery','fallback_historical_batches'):
        if snapshot.get(field)!=rebuilt.get(field):raise ValueError('Finalization Claude recovery ancestry mismatch.')
    for field in ('status','source_language','target_language','policy','summary','units','checkpoint_histories'):
        if snapshot[field]!=rebuilt[field]:
            raise ValueError(f'Finalization snapshot no longer matches validated ancestry: {field}')
    if pt._file_hash(path)!=snapshot_hash:raise ValueError('Finalization snapshot changed while validating.')
    return dict(snapshot=snapshot,snapshot_path=str(path),snapshot_sha256=snapshot_hash,
                source_path=str(resolved['source']),resolved_inputs={k:str(v) for k,v in resolved.items()})


def build_reconstruction_plan(snapshot_path, source_path=None):
    """Map an ancestry-validated snapshot into maintained reconstruction inputs."""
    validated=read_validated_snapshot(snapshot_path,source_path)
    snapshot=validated['snapshot'];path=Path(validated['snapshot_path'])
    snapshot_hash=validated['snapshot_sha256']
    resolved=validated['resolved_inputs']
    units, paragraphs, spans, expected, policies = [], [], [], {}, {}
    for row in snapshot['units']:
        if row['status'] not in ('accepted_primary','accepted_fallback'):continue
        unit, selected = row['source_unit'],row['selected_candidate']
        if unit['source_type'] not in SUPPORTED:
            raise ValueError(f'Accepted target requires unsupported reconstruction: {row["unit_key"]}')
        policies[row['unit_key']]=selected.get('validation_policy_version',snapshot['policy'].get('validation_policy_version',2))
        pt.vp.require_policy(policies[row['unit_key']])
        units.append(deepcopy(unit))
        expected[row['unit_key']] = selected['text']
        if 'styled_spans' in unit:
            if selected['span_translations'] is None:
                raise ValueError('Styled paragraphs require matching selected span translations.')
            spans.append(dict(unit_key=row['unit_key'],translations=deepcopy(selected['span_translations'])))
        else:
            if selected['span_translations'] is not None:
                raise ValueError('Unexpected styled response for an ordinary paragraph.')
            paragraphs.append(dict(unit_key=row['unit_key'],text=selected['text']))
    if pt._file_hash(path)!=snapshot_hash:raise ValueError('Finalization snapshot changed while planning.')
    return dict(snapshot=snapshot,snapshot_path=str(path),snapshot_sha256=snapshot_hash,
                source_path=str(resolved['source']),resolved_inputs={k:str(v) for k,v in resolved.items()},
                units=units,paragraph_replacements=paragraphs,span_replacements=spans,
                expected_translations=expected,validation_policy_by_unit=policies,validation_policy_version=snapshot['policy'].get('validation_policy_version',2))


def _unchanged_inputs(plan):
    if pt._file_hash(plan['snapshot_path'])!=plan['snapshot_sha256']:
        raise ValueError('Finalization snapshot changed during reconstruction.')
    if any(pt._file_hash(path)!=plan['snapshot']['ancestry'][key]['sha256'] for key,path in plan['resolved_inputs'].items()):
        raise ValueError('An upstream input changed during reconstruction.')


def reconstruct_finalized_snapshot(snapshot_path, output_root, source_path=None, *, artifact_dir=None):
    """Write a uniquely named partial/complete translation plus validation report.

    Reuses the maintained XML/ZIP/run validation, then independently reopens all
    paragraphs and checks expected text, locator correspondence and coverage.
    A structurally passing report does not establish visual fit in PowerPoint.
    """
    plan = build_reconstruction_plan(snapshot_path,source_path)
    snapshot, source = plan['snapshot'],Path(plan['source_path'])
    root = Path(output_root).resolve()
    protected = [Path(plan['snapshot_path']),*map(Path,plan['resolved_inputs'].values())]
    if any(root==p or root in p.parents for p in protected):
        raise ValueError('Use a separate reconstruction output directory.')
    original, _ = ph.open_presentation(source)
    _unchanged_inputs(plan)
    root.mkdir(parents=True,exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='reconstruction_',dir=root)) if artifact_dir is None else Path(artifact_dir).resolve()
    if artifact_dir is not None:
        if root not in folder.parents:raise ValueError('Attempt outside report directory.')
        folder.mkdir(exist_ok=True)
    language = re.sub(r'[^\w-]+','_',snapshot['target_language'],flags=re.UNICODE).strip('_')
    coverage = 'complete' if snapshot['summary']['translation_complete'] else 'partial'
    output = folder / f'{source.stem}_{language}_{coverage}_translation.pptx'
    validation = reconstruct(source,output,plan['units'],plan['paragraph_replacements'],
                             span_replacements=plan['span_replacements'],validation_policy_version=plan['validation_policy_version'],validation_policy_by_unit=plan['validation_policy_by_unit'])
    manual_keys = {r['unit_key'] for r in validation['manual_review']}
    applied = set(validation['replaced']) | set(validation['unchanged'])
    failures = list(validation['failures'])
    if applied | manual_keys != set(plan['expected_translations']) or applied & manual_keys:
        failures.append('Reconstruction target accounting mismatch.')
    reopened, _ = ph.open_presentation(output)
    actual = ph.build_pptx_text_units(reopened,pptx_path=output,include_smartart=True,
        include_styled_spans=any(u['source_unit'].get('smartart_text_version') == 1 for u in plan['snapshot']['units']))
    indexed = {u['unit_key']:u for u in actual}
    expected_keys = {r['unit_key'] for r in snapshot['units']}
    if set(indexed)!=expected_keys or len(actual)!=len(expected_keys):
        failures.append('Reopened paragraph identity/coverage mismatch.')
    if len(reopened.slides)!=len(original.slides):
        failures.append('Reopened slide count changed.')
    verified_translations = verified_non_targets = 0
    locator_fields = ('slide_number','shape_path','shape_id','source_type','paragraph_index','row_index','col_index','run_count')
    for row in snapshot['units']:
        key, unit = row['unit_key'],row['source_unit']
        target = indexed.get(key)
        expected_text = plan['expected_translations'][key] if key in applied else unit['text']
        if (target is None or target['text']!=expected_text
                or any(target.get(k)!=unit.get(k) for k in locator_fields)):
            failures.append(f'Reopened text/source correspondence mismatch: {key}')
        elif key in applied:verified_translations += 1
        elif key not in plan['expected_translations']:verified_non_targets += 1
    _unchanged_inputs(plan)
    report = dict(schema_version=VERSION,stage='finalized_pptx_reconstruction',created_at=pt._now(),
        status='failed' if failures else 'manual_review' if manual_keys else 'passed',
        translation_coverage=coverage,output=str(output),output_sha256=pt._file_hash(output),
        finalization_snapshot=dict(path=plan['snapshot_path'],sha256=plan['snapshot_sha256']),
        source=dict(path=str(source),sha256=snapshot['ancestry']['source']['sha256']),
        source_language=snapshot['source_language'],target_language=snapshot['target_language'],
        completeness=deepcopy(snapshot['summary']),requested_translations=len(plan['units']),
        applied_translations=len(applied),changed_paragraphs=len(validation['replaced']),
        accepted_unchanged_paragraphs=len(validation['unchanged']),
        ordinary_targets=len(plan['paragraph_replacements']),styled_targets=len(plan['span_replacements']),
        applied_unit_keys=sorted(applied),manual_review=validation['manual_review'],
        reopened_slide_count=len(reopened.slides),reopened_unit_count=len(actual),
        verified_translations=verified_translations,verified_non_target_units=verified_non_targets,
        failures=failures,validation=validation,
        layout_concerns=['Inspect Spanish text expansion, clipping, font rendering and line wrapping in PowerPoint.',
                         'No automatic fitting, geometry changes, wrapping overrides or visible manual markers were applied.',
                         'Pending/preserved source text remains in English; this is a partial translation.' if coverage=='partial'
                         else 'Semantic completion does not guarantee visual layout quality.'])
    report_path = folder/'reconstruction_report.json'
    pt._write_checkpoint(report_path,report)
    return dict(output_path=str(output),report_path=str(report_path),report=report)


def validate_reconstruction_artifacts(report_path, snapshot_path, source_path=None):
    """Reopen existing outputs and replay preservation checks; never write artifacts."""
    from zipfile import ZipFile
    from . import pptx_reconstruction as pr
    path=Path(report_path).resolve()
    report_hash=pt._file_hash(path)
    report=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity=report.pop('integrity',None)
    plan=build_reconstruction_plan(snapshot_path,source_path)
    output=Path(report['output']);source=Path(plan['source_path'])
    if (integrity!=pt._digest(report) or report.get('schema_version')!=VERSION
            or report.get('stage')!='finalized_pptx_reconstruction'
            or report['status'] not in ('passed','manual_review') or report['failures']
            or report['finalization_snapshot']['sha256']!=plan['snapshot_sha256']
            or Path(report['finalization_snapshot']['path']).resolve()!=Path(snapshot_path).resolve()
            or report['source']['sha256']!=pt._file_hash(source)
            or not _valid_output_location(report, path, output) or pt._file_hash(output)!=report['output_sha256']):
        raise ValueError('Invalid reconstruction artifact/report.')
    changes,expected_runs,checks={},{},[]
    units={u['unit_key']:u for u in plan['units']}
    manual={r['unit_key'] for r in report['manual_review']}
    ordinary={r['unit_key'] for r in plan['paragraph_replacements']}
    if not manual<=ordinary:raise ValueError('Invalid reconstruction manual-review targets.')
    with ZipFile(source) as archive:
        records={u['unit_key']:(u,p) for u,p,_ in pr._records(archive)[1]}
        for answer in plan['span_replacements']:
            key=answer['unit_key'];unit,paragraph=records[key]
            original={i:n.text or '' for i,r in enumerate(paragraph.findall('a:r',pr.NS),1)
                      if (n:=r.find('a:t',pr.NS)) is not None}
            text,runs,span_checks=pr._apply_span_replacement(units[key],paragraph,answer,policy_version=plan['validation_policy_by_unit'][key])
            checks.extend(span_checks)
            if runs!=original:changes[key]=text;expected_runs[key]=runs
        for answer in plan['paragraph_replacements']:
            key=answer['unit_key']
            if key not in manual and answer['text']!=records[key][0]['text']:changes[key]=answer['text']
    checked=pr.validate_round_trip(source,output,changes,expected_run_texts=expected_runs,span_checks=checks)
    if checked['failures'] or checked['span_checks']!=report['validation']['span_checks']:
        raise ValueError('Existing PPTX fails preservation validation: '+ '; '.join(checked['failures']))
    reopened,_=ph.open_presentation(output)
    actual=ph.build_pptx_text_units(reopened,pptx_path=output,include_smartart=True,
        include_styled_spans=any(u['source_unit'].get('smartart_text_version') == 1 for u in plan['snapshot']['units']))
    indexed={u['unit_key']:u for u in actual}
    applied=set(plan['expected_translations'])-manual
    if (len(actual)!=len(plan['snapshot']['units']) or len(reopened.slides)!=report['reopened_slide_count']
            or report['applied_translations']!=len(applied) or set(report['applied_unit_keys'])!=applied
            or report['completeness']!=plan['snapshot']['summary']):
        raise ValueError('Existing PPTX/report accounting mismatch.')
    for row in plan['snapshot']['units']:
        key=row['unit_key'];expected=plan['expected_translations'][key] if key in applied else row['source_unit']['text']
        if key not in indexed or indexed[key]['text']!=expected:raise ValueError('Existing PPTX text mismatch: '+key)
    _unchanged_inputs(plan)
    if pt._file_hash(path)!=report_hash or pt._file_hash(output)!=report['output_sha256']:
        raise ValueError('Existing artifacts changed during validation.')
    return dict(output_path=str(output),report_path=str(path),report=report)


def _valid_output_location(report, report_path, output):
    """Legacy colocated outputs or a new-layout, versioned run deliverable."""
    if report.get('artifact_layout')!=1:
        return output.resolve().parent==report_path.parent
    root=report_path.parent.parent.parent
    return (report.get('status')=='passed'
            and report_path.parent.parent==root/'reports'
            and output.resolve().parent==root/'final'
            and report.get('deliverable_version',0)>0
            and output.name.endswith(f"__v{report['deliverable_version']:03d}.pptx"))
