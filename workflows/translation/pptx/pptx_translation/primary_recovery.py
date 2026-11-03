"""One offline policy-2 -> policy-3 candidate overlay, never a history migration.

Only exhausted numeric-validation failures with a saved third response qualify.
Every read replays the original checkpoint and reproduces every recovery event.
"""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
from . import primary_translation as pt
from . import artifact_paths

VERSION=1
STAGE='primary_offline_recovery'


class RecoveryValidationError(ValueError):
    def __init__(self, errors):
        self.errors=errors
        super().__init__('Offline recovery rejected: '+'; '.join(errors))


def _base(record):
    # Resolve only the exact recorded file identity; no latest-file discovery.
    return artifact_paths.resolve_input(record, module_file=__file__, hash_function=pt._file_hash)


def _derive(original_path, source_path):
    original_path=Path(original_path).resolve()
    original_hash=pt._file_hash(original_path)
    state=pt._read_checkpoint(original_path)  # Frozen historical replay, not the overlay reader.
    c=state['compatibility']
    if c['policy_version']!=2 or state.get('diagnostic'):
        raise ValueError('Offline recovery requires a production policy-2 primary checkpoint.')
    if pt._file_hash(source_path)!=c['source_sha256']:
        raise ValueError('Offline recovery source hash mismatch.')
    exhausted=[k for k,r in state['unit_results'].items() if r['status']=='exhausted']
    if not exhausted:raise ValueError('No exhausted primary units to recover.')
    events=[];errors=[]
    for key in exhausted:
        record=state['unit_results'][key]
        matches=[(b,a) for b in state['batches'] for a in b['attempts'] if key in a['unit_keys']]
        if not matches:
            errors.append(key+':missing_saved_attempt');continue
        batch,attempt=matches[-1]
        if (record['attempt_count']!=3 or len(matches)!=3 or attempt['attempt']!=3
                or record['request_ids'][-1]!=attempt['request_id'] or attempt['response_json'] is None
                or record['error_kind']!='validation'):
            errors.append(key+':requires_exact_saved_third_validation_attempt');continue
        old_errors=attempt['validation']['errors_by_unit'].get(key,[])
        if not old_errors or any(not e.endswith(':numeric_markers_changed') for e in old_errors):
            errors.append(key+':not_exclusively_numeric_rejection: '+', '.join(old_errors));continue
        checked=pt.validate_primary_response(attempt['response_json'],attempt['request'],policy_version=3)
        if checked['response_errors'] or key in checked['errors_by_unit']:
            errors.extend(checked['response_errors']+checked['errors_by_unit'].get(key,[]));continue
        results=[r for r in checked['translations'] if r['unit_key']==key]
        if len(results)!=1:
            errors.append(key+':missing_revalidated_candidate');continue
        request=next(u for u in attempt['request']['units'] if u['unit_key']==key)
        rows=attempt['response_json']['translations']
        index=next(i for i,r in enumerate(rows) if r['unit_key']==key)
        reference=dict(original_sha256=original_hash,batch_index=batch['batch_index'],
            attempt=attempt['attempt'],request_id=attempt['request_id'],request_hash=attempt['request_hash'],
            raw_response_sha256=hashlib.sha256(attempt['raw_response'].encode('utf-8')).hexdigest(),
            response_sha256=pt._digest(attempt['response_json']),response_row_index=index,
            response_row_sha256=pt._digest(rows[index]),validation_policy_version=3)
        candidate=pt._candidate(results[0],request,attempt,dict(c,policy_version=3))
        candidate['provenance']['offline_revalidation']=deepcopy(reference)
        events.append(dict(unit_key=key,event='offline_revalidation',reference=reference,
                           historical_errors=deepcopy(old_errors),candidate=candidate))
    if errors:raise RecoveryValidationError(errors)
    if pt._file_hash(original_path)!=original_hash:raise ValueError('Original primary changed during recovery.')
    return state,events,original_hash


def read(path, source_path=None):
    """Verify immutable recovery references and return a derived candidate view.

    Batches, request histories and original candidates are never rewritten. The
    view's effective eligibility/policy must not be replayed as historical success.
    """
    path=Path(path).resolve();digest=pt._file_hash(path)
    artifact=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity=artifact.pop('integrity',None)
    if (integrity!=pt._digest(artifact) or artifact.get('schema_version')!=VERSION
            or artifact.get('stage')!=STAGE or artifact.get('validation_policy_version')!=3):
        raise ValueError('Invalid primary recovery artifact integrity/version/policy.')
    base=_base(artifact['original_checkpoint'])
    source=_base(dict(path=artifact['original_settings']['source_path'],
                      sha256=artifact['original_settings']['source_sha256'])) if source_path is None else Path(source_path)
    original,events,original_hash=_derive(base,source)
    expected_compatibility=dict(original['compatibility'],policy_version=3,
        recovery_original_sha256=original_hash,recovery_policy_version=3)
    if (artifact['original_settings']!=original['compatibility'] or artifact['events']!=events
            or artifact['compatibility']!=expected_compatibility):
        raise ValueError('Recovery events/candidate digests/settings do not match exact saved attempts.')
    view=deepcopy(original)
    view['stage']=STAGE;view['compatibility']=expected_compatibility
    view['offline_recovery']=dict(artifact_path=str(path),artifact_sha256=digest,
        original_checkpoint=deepcopy(artifact['original_checkpoint']),validation_policy_version=3,
        events=deepcopy(events))
    for event in events:
        record=view['unit_results'][event['unit_key']]
        record.update(status='validated',candidate=deepcopy(event['candidate']))
    view['status']='completed' if all(r['status']=='validated' for r in view['unit_results'].values()) else 'partial'
    if pt._file_hash(path)!=digest or pt._file_hash(base)!=original_hash:
        raise ValueError('Recovery ancestry changed during validation.')
    return view


def recover(original_checkpoint, source_path):
    """Atomically create/reuse a single immutable artifact; no provider submission."""
    base=Path(original_checkpoint).resolve();source=Path(source_path).resolve()
    original,events,digest=_derive(base,source)
    path=base.with_name(f'primary_recovery_policy3_{digest[:20]}.json')
    def result():
        state=read(path,source)
        if state['offline_recovery']['original_checkpoint']['sha256']!=digest:
            raise ValueError('Recovery artifact has different ancestry.')
        return dict(path=str(path),sha256=pt._file_hash(path),unit_keys=[e['unit_key'] for e in events])
    if path.exists():return result()
    lock=path.with_name(path.name+'.lock')
    with lock.open('x',encoding='utf-8') as stream:stream.write('offline recovery writer\n')
    try:
        if not path.exists():
            artifact=dict(schema_version=VERSION,stage=STAGE,created_at=pt._now(),validation_policy_version=3,
                original_checkpoint=dict(path=str(base),sha256=digest),original_settings=deepcopy(original['compatibility']),
                compatibility=dict(original['compatibility'],policy_version=3,
                    recovery_original_sha256=digest,recovery_policy_version=3),events=events)
            if pt._file_hash(base)!=digest:raise ValueError('Original checkpoint changed before publication.')
            pt._write_checkpoint(path,artifact)
        return result()
    finally:lock.unlink()
