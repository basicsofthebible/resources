"""Stage 3F-1: offline, ancestry-checked selection and completeness artifacts.

No provider clients, recovery calls, PowerPoint annotations or reconstruction.
"""
from collections import Counter
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
from xml.etree import ElementTree as ET  # Compatibility: finalization.ET.ParseError is used by tests.

from . import primary_translation as pt
from . import evaluation as ev
from . import fallback_translation as ft
from . import fallback_evaluation as fe
from . import pptx_helpers as ph

VERSION = 1
STATUSES = ('accepted_primary', 'accepted_fallback', 'preserved', 'pending',
            'recovery_eligible', 'failed', 'manual_followup')
MANUAL_LABEL = 'FLAGGED FOR MANUAL FOLLOWUP:'
MAX_SEMANTIC_ATTEMPTS = 2


def _source_units(source, primary):
    """Re-extract the entire supported source scope, including preserved units."""
    presentation, _ = ph.open_presentation(source)
    return ph.classify_pptx_text_units(ph.build_pptx_text_units(
        presentation, pptx_path=source, include_smartart=True,
        include_styled_spans=any('styled_spans' in u for u in primary['units']),
        smartart_styled_spans=any(u.get('smartart_text_version') == 1 for u in primary['units'])))


def _validate_primary(path, source):
    state = pt.read_primary_state(path)
    if state.get('diagnostic'):
        raise ValueError('Diagnostic primary ancestry is not allowed in production finalization.')
    c = state['compatibility']
    if pt._file_hash(source) != c['source_sha256']:
        raise ValueError('Finalization source identity mismatch.')
    original = _source_units(source, state)
    # ElementTree namespace registration elsewhere in the process can change
    # XML prefixes without changing any formatting. Match XML structurally before
    # restoring the exact saved serialization for the original-unit hash check.
    saved_units = {u['unit_key']:u for u in state['units']}
    for unit in original:
        saved = saved_units.get(unit['unit_key'], {})
        ph.reconcile_span_formats(unit, saved, coverage_error='Source span formatting coverage mismatch.',
                                  formatting_error='Source span formatting changed.')
    if pt._digest(original) != c['units_sha256']:
        raise ValueError('Complete source units/classification/formatting do not match the primary snapshot.')
    # Primary reader replays every request/subset and validates each accepted
    # paragraph against its original response and digest, independent of batches.
    return state, original


def _validate_evaluation(path, upstream, source, payload, *, fallback=False):
    state = fe._read(path) if fallback else ev._read(path)
    c = state['compatibility']
    hash_key = 'fallback_sha256' if fallback else 'primary_sha256'
    if (c[hash_key] != pt._file_hash(upstream) or c['source_sha256'] != pt._file_hash(source)
            or c['payload_sha256'] != pt._digest(payload) or state['payload'] != payload):
        raise ValueError('Evaluation ancestry/candidate payload mismatch.')
    plan = ev.batch_evaluation_payload(payload, c['max_units'], c['max_chars'])
    if len(plan) != len(state['batches']):
        raise ValueError('Incomplete evaluation plan.')
    for expected, batch in zip(plan, state['batches']):
        messages = ev.build_evaluation_messages(payload['metadata'], expected['units'], expected['unit_count'],
                                                c['instruction'], fallback=fallback)
        if (batch['unit_keys'] != [u['unit_key'] for u in expected['units']]
                or batch['batch_index'] != expected['batch_index'] or batch['request_hash'] != pt._digest(messages)):
            raise ValueError('Evaluation batch/request identity mismatch.')
    return state


def _index_batches(state):
    return {k:b for b in state['batches'] for k in b['unit_keys']} if state else {}


def _decisions(state):
    return {r['unit_key']:r for b in state['batches'] for r in b.get('evaluations', [])} if state else {}


def _candidate(unit, provider, model, generation_hash, evaluation_hash):
    if provider == 'gemini':
        pair = dict(unit_key=unit['unit_key'], primary_translation=unit['primary_translation'])
        if unit.get('primary_span_translations') is not None:
            pair['primary_span_translations'] = unit['primary_span_translations']
        text, spans, digest = unit['primary_translation'], unit.get('primary_span_translations'), pt._digest(pair)
    else:
        text, spans, digest = unit['fallback_translation'], unit.get('fallback_span_translations'), unit['candidate_sha256']
    selected=dict(provider=provider, model=model, text=text, span_translations=deepcopy(spans),
                  candidate_sha256=digest, generation_checkpoint_sha256=generation_hash,
                  evaluation_checkpoint_sha256=evaluation_hash)
    validation=unit.get('validation',{})
    if validation.get('policy_version',2)>=3:
        selected['validation_warnings']=deepcopy(validation['warnings'])
    return selected


def build_finalization_snapshot(primary_checkpoint, evaluation_checkpoint, source_path, *,
                                fallback_checkpoint=None, fallback_evaluation_checkpoint=None,
                                manual_followup=None):
    """Select accepted pairs and account for every source unit, without writing.

    manual_followup optionally maps failed/recovery-eligible unit keys to explicit
    reviewer reasons. Pending work is never implicitly declared exhausted. This
    stage recognizes the initial Claude round only, and reserves a separate second
    semantic round for future recovery. Operational submissions do not spend rounds.
    """
    source, primary_path, evaluation_path = map(lambda p:Path(p).resolve(),
                                              (source_path, primary_checkpoint, evaluation_checkpoint))
    if fallback_evaluation_checkpoint is not None and fallback_checkpoint is None:
        raise ValueError('A fallback evaluation requires its exact Claude generation checkpoint.')
    paths = dict(source=source, primary=primary_path, primary_evaluation=evaluation_path)
    if fallback_checkpoint is not None:paths['fallback'] = Path(fallback_checkpoint).resolve()
    if fallback_evaluation_checkpoint is not None:paths['fallback_evaluation'] = Path(fallback_evaluation_checkpoint).resolve()
    hashes = {k:pt._file_hash(p) for k,p in paths.items()}
    primary, original = _validate_primary(primary_path, source)
    baseline = ev.build_evaluation_payload(primary_path, source)
    evaluation = _validate_evaluation(evaluation_path, primary_path, source, baseline)
    fallback = fallback_evaluation = None
    if fallback_checkpoint is not None:
        fallback = ft._read(paths['fallback'])
        c = fallback['compatibility']
        if (c['primary_sha256'] != hashes['primary'] or c['evaluation_sha256'] != hashes['primary_evaluation']
                or c['source_sha256'] != hashes['source']):
            raise ValueError('Claude checkpoint belongs to an incompatible primary/evaluation branch.')
        expected = ft.build_fallback_payload(primary_path, evaluation_path, source,policy_version=pt.vp.payload_policy(fallback['payload'],default=2))
        if expected != fallback['payload'] or c['payload_sha256'] != pt._digest(expected) or expected.get('diagnostic'):
            raise ValueError('Diagnostic or incompatible Claude input ancestry.')
        # Revalidate ordered generation requests and completed span/text pairs.
        fallback_payload = fe.build_fallback_evaluation_payload(paths['fallback'], source)
        if fallback_evaluation_checkpoint is not None:
            fallback_evaluation = _validate_evaluation(paths['fallback_evaluation'], paths['fallback'],
                                                       source, fallback_payload, fallback=True)
    p_batches, e_batches = _index_batches(primary), _index_batches(evaluation)
    primary_results = primary['unit_results']
    f_batches, fe_batches = _index_batches(fallback), _index_batches(fallback_evaluation)
    if fallback and fallback.get('fallback_offline_recovery'):
        from .fallback_recovery import history_batches
        f_batches={k:b for b in history_batches(fallback) for k in b['unit_keys']}
    decisions, f_decisions = _decisions(evaluation), _decisions(fallback_evaluation)
    primary_units = {u['unit_key']:dict(u,**(primary_results[u['unit_key']]['candidate'] or {})) if u['unit_key'] in primary_results else u for u in primary['units']}
    f_candidates = {r['unit_key']:r for b in fallback['batches'] for r in b['candidates']} if fallback else {}
    manual_followup = dict(manual_followup or {})
    unknown = set(manual_followup) - set(primary_units)
    if unknown:raise ValueError(f'Unknown manual-follow-up unit keys: {sorted(unknown)}')
    records = []
    for unit in original:
        key = unit['unit_key']
        selected, semantic_attempts = None, int(key in f_candidates)
        if unit.get('action') != 'translate' or unit.get('preserve_source'):
            status, reason = 'preserved', unit.get('preserve_reason') or 'classification_preserve'
        elif primary_results[key]['status'] != 'validated':
            status = 'failed' if primary_results[key]['status']=='exhausted' else 'pending'
            reason = 'primary_generation_'+primary_results[key]['status']
        elif key not in decisions:
            status = 'failed' if e_batches[key]['status']=='error' else 'pending'
            reason = 'primary_evaluation_'+e_batches[key]['status']
        elif decisions[key]['passed']:
            status, reason = 'accepted_primary', 'primary_evaluation_accepted'
            selected = _candidate(primary_units[key], 'gemini', primary['compatibility']['model'],
                                  hashes['primary'], hashes['primary_evaluation'])
            selected['candidate_sha256'] = primary_results[key]['candidate']['candidate_sha256']
            selected['generation_provenance'] = deepcopy(primary_results[key]['candidate']['provenance'])
            if primary.get('offline_recovery'):
                selected['validation_warnings']=pt.candidate_advisories(primary_units[key],primary_results[key]['candidate'])
        elif key not in f_batches:
            status, reason = 'recovery_eligible', 'primary_semantic_rejection'
        elif key not in f_candidates:
            status = ('failed' if fallback.get('unit_results',{}).get(key,{}).get('status') in ('invalid','missing','provider_error') or f_batches[key]['status']=='error' else 'pending')
            reason = 'fallback_generation_'+f_batches[key]['status']
        elif key not in f_decisions:
            status = 'failed' if key in fe_batches and fe_batches[key]['status']=='error' else 'pending'
            reason = 'fallback_evaluation_error' if status=='failed' else 'fallback_evaluation_pending'
        elif f_decisions[key]['passed']:
            status, reason = 'accepted_fallback', 'fallback_evaluation_accepted'
            selected = _candidate(f_candidates[key], 'anthropic', fallback['compatibility']['model'],
                                  hashes['fallback'], hashes['fallback_evaluation'])
        else:
            status, reason = 'recovery_eligible', 'fallback_semantic_rejection'
        if selected is not None and (primary['compatibility']['policy_version']>=4 or (fallback and fallback['schema_version']==2)):
            selected['validation_policy_version']=(f_candidates[key]['validation']['policy_version'] if status=='accepted_fallback' else primary['compatibility']['policy_version'])
        prior_status = status
        manual_reason = manual_followup.get(key)
        if key in manual_followup:
            if status not in ('failed','recovery_eligible') or not isinstance(manual_reason,str) or not manual_reason.strip():
                raise ValueError('Manual follow-up requires a failed/recovery-eligible unit and a nonempty reviewer reason.')
            status, reason = 'manual_followup', manual_reason.strip()
        history = {}
        for stage, batches in [('primary',p_batches),('primary_evaluation',e_batches),
                                ('fallback',f_batches),('fallback_evaluation',fe_batches)]:
            if key in batches:
                batch = batches[key]
                attempts = []
                for attempt in batch['attempts']:
                    if stage in ('primary','fallback') and 'unit_keys' in attempt and key not in attempt['unit_keys']:continue
                    compact = {k:deepcopy(v) for k,v in attempt.items() if k not in ('raw_response','response_json','validation')}
                    response = attempt.get('response_json') or {}
                    compact['unit_responses'] = [deepcopy(r) for field in ('translations','evaluations')
                        for r in response.get(field, []) if isinstance(r,dict) and r.get('unit_key')==key] if isinstance(response,dict) else []
                    attempts.append(compact)
                history[stage] = dict(batch_index=batch['batch_index'],status=batch['status'],attempts=attempts)
        local_source = {k:deepcopy(v) for k,v in unit.items() if k not in ph.TRANSLATION_SCHEMA_DEFAULTS}
        records.append(dict(unit_key=key, status=status, reason=reason, source_unit=local_source,
            selected_candidate=selected, final_text=selected['text'] if selected else None,
            output_text=selected['text'] if selected else unit['text'],
            output_source='accepted_translation' if selected else 'preserved_source' if status=='preserved' else 'unresolved_source',
            needs_manual_followup=status=='manual_followup', manual_followup_reason=manual_reason,
            review_label=MANUAL_LABEL if status=='manual_followup' else None,
            status_before_manual=prior_status if status=='manual_followup' else None,
            semantic_recovery=dict(max_attempts=MAX_SEMANTIC_ATTEMPTS, completed_attempts=semantic_attempts,
                                   remaining_attempts=MAX_SEMANTIC_ATTEMPTS-semantic_attempts,
                                   supported_round=1, exhausted=False),
            operational_attempt_counts={stage:len(b['attempts']) for stage,b in history.items()},
            operational_retry_counts={stage:max(0,len(b['attempts'])-1) for stage,b in history.items()},
            primary_candidate=deepcopy({k:v for k,v in primary_units[key].items() if k in
                 ('primary_translation','primary_span_translations','primary_translation_model','primary_error')}),
            fallback_candidate=deepcopy(f_candidates.get(key)),
            primary_decision=deepcopy(decisions.get(key)), fallback_decision=deepcopy(f_decisions.get(key)),
            history=history))
    counts = {s:0 for s in STATUSES}
    counts.update(Counter(r['status'] for r in records))
    if sum(counts.values()) != len(original):raise ValueError('Incomplete finalization accounting.')
    summary = dict(total_units=len(records), counts=counts,
                   accepted=counts['accepted_primary']+counts['accepted_fallback'],
                   unresolved=sum(counts[s] for s in ('pending','recovery_eligible','failed','manual_followup')),
                   translation_complete=all(r['status'] in ('accepted_primary','accepted_fallback','preserved') for r in records))
    snapshot = dict(schema_version=VERSION, stage='offline_finalization', created_at=pt._now(),
                    status='complete' if summary['translation_complete'] else 'incomplete',
                    source_language=primary['compatibility']['source_language'],
                    target_language=primary['compatibility']['target_language'],
                    ancestry={k:dict(path=str(paths[k]),sha256=v) for k,v in hashes.items()},
                    policy=dict(max_semantic_attempts=MAX_SEMANTIC_ATTEMPTS,
                                supported_claude_rounds=1, unresolved_output='retain_source',
                                manual_label=MANUAL_LABEL, diagnostic_ancestry_allowed=False),
                    checkpoint_histories={k:deepcopy(v['batches']) for k,v in
                        [('primary',primary),('primary_evaluation',evaluation),('fallback',fallback),
                         ('fallback_evaluation',fallback_evaluation)] if v is not None},
                    summary=summary, units=records)
    if fallback and fallback.get('fallback_offline_recovery'):
        snapshot['fallback_offline_recovery']={k:deepcopy(v) for k,v in fallback['fallback_offline_recovery'].items() if k!='events'}
        snapshot['fallback_historical_batches']=deepcopy(fallback['historical_batches'])
    if primary.get('offline_recovery'):
        snapshot['primary_offline_recovery']={k:deepcopy(v) for k,v in primary['offline_recovery'].items() if k!='events'}
    if primary['compatibility']['policy_version']>=3:
        snapshot['policy']['validation_policy_version']=primary['compatibility']['policy_version']
    if any(pt._file_hash(paths[k])!=v for k,v in hashes.items()):
        raise ValueError('An upstream snapshot changed during finalization.')
    return snapshot


def _review_markdown(snapshot):
    summary = snapshot['summary']
    lines = ['# Finalization review', '', f"Accounting: {json.dumps(summary, ensure_ascii=False)}", '',
             'Pending work is incomplete processing, not exhausted recovery.',
             'Source text is retained for every unresolved unit; it is not an accepted translation.', '']
    review = [r for r in snapshot['units'] if r['status'] in ('manual_followup','failed','recovery_eligible')]
    if not review:lines.extend(['No units currently require manual follow-up or recovery review.', ''])
    for r in review:
        u = r['source_unit']
        heading = MANUAL_LABEL+' ' if r['needs_manual_followup'] else 'Recovery review: '
        lines.extend([f"## {heading}{r['unit_key']}", '', f"Status: {r['status']} — {r['reason']}",
                      f"Locator: slide {u.get('slide_number')}, shape {u.get('shape_path')}, paragraph {u.get('paragraph_index')}, row {u.get('row_index')}, column {u.get('col_index')}", '',
                      '### Original source', '', u['text'], '', '### Candidates, decisions and failure history', '',
                      '```json', json.dumps({k:r[k] for k in ('semantic_recovery','operational_attempt_counts',
                          'primary_candidate','fallback_candidate','primary_decision','fallback_decision','history')},ensure_ascii=False,indent=2), '```', ''])
    return '\n'.join(lines)


def write_finalization_artifacts(snapshot, output_root, *, artifact_paths=None):
    """Publish three artifacts in a new directory or explicitly reserved run paths."""
    if snapshot.get('schema_version')!=VERSION or snapshot.get('stage')!='offline_finalization':
        raise ValueError('Unsupported finalization snapshot.')
    for item in snapshot['ancestry'].values():
        if pt._file_hash(Path(item['path'])) != item['sha256']:
            raise ValueError('Upstream snapshot changed before artifact export.')
    root = Path(output_root).resolve()
    if artifact_paths is None and any(root==Path(v['path']) or root in Path(v['path']).parents for v in snapshot['ancestry'].values()):
        raise ValueError('Use a separate finalization output directory.')
    root.mkdir(parents=True,exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='finalization_',dir=root)) if artifact_paths is None else None
    paths = ({k:folder/name for k,name in [('snapshot','finalization.json'),('summary','summary.csv'),('manual_review','manual_followup.md')]}
             if artifact_paths is None else {k:Path(v) for k,v in artifact_paths.items()})
    if artifact_paths is not None:
        for path in paths.values():
            if root not in path.resolve().parents:raise ValueError('Export outside run directory.')
            if path.resolve() in {Path(v['path']).resolve() for v in snapshot['ancestry'].values()}:
                raise ValueError('Export would overwrite an upstream input.')
            if path.exists():raise FileExistsError(path)
    try:
        columns = ['unit_key','status','reason','slide_number','shape_path','paragraph_index','row_index','col_index',
                   'source_text','output_text','provider','model','candidate_sha256','needs_manual_followup',
                   'review_label','semantic_attempts','semantic_attempts_remaining']
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader()
        for r in snapshot['units']:
            selected = r['selected_candidate'] or {}
            writer.writerow(dict(unit_key=r['unit_key'],status=r['status'],reason=r['reason'],
                **{k:r['source_unit'].get(k) for k in columns[3:8]},source_text=r['source_unit']['text'],
                output_text=r['output_text'],provider=selected.get('provider'),model=selected.get('model'),
                candidate_sha256=selected.get('candidate_sha256'),needs_manual_followup=r['needs_manual_followup'],
                review_label=r['review_label'],semantic_attempts=r['semantic_recovery']['completed_attempts'],
                semantic_attempts_remaining=r['semantic_recovery']['remaining_attempts']))
        paths['summary'].write_text(stream.getvalue(),encoding='utf-8-sig')
        paths['manual_review'].write_text(_review_markdown(snapshot),encoding='utf-8')
        pt._write_checkpoint(paths['snapshot'],snapshot)
    except BaseException:
        if folder is not None:shutil.rmtree(folder)
        raise
    return {k:str(p) for k,p in paths.items()}
