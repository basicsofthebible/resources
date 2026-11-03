"""Explicit offline recovery of one saved schema-1 Claude response.

The artifact is immutable. Future submissions use a separate, lazily created
schema-2 continuation whose seed is rederived from this exact artifact on read.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from . import primary_translation as pt
from . import fallback_translation as ft
from . import fallback_checkpoint as fc
from . import artifact_paths

STAGE='claude_offline_recovery'
VERSION=1


class RecoveryValidationError(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__('Claude offline recovery rejected: '+'; '.join(errors))


def resolve(record):
    return artifact_paths.resolve_input(record, module_file=__file__, hash_function=pt._file_hash)


def lineage(state, source_path=None):
    c=state['compatibility'];p=state['provenance']
    records={name:dict(path=p[field],sha256=c[hash_field]) for name,field,hash_field in
             [('primary','primary_checkpoint','primary_sha256'),('evaluation','evaluation_checkpoint','evaluation_sha256'),
              ('source','source_path','source_sha256')]}
    paths={name:resolve(r) for name,r in records.items()}
    if source_path is not None and pt._file_hash(source_path)!=c['source_sha256']:
        raise ValueError('Claude recovery source hash mismatch.')
    if p['model']!=c['model'] or p['provider']!='anthropic' or p.get('diagnostic'):
        raise ValueError('Claude recovery model/provider/provenance mismatch.')
    return records,paths


def derive(original_path,source_path=None):
    original_path=Path(original_path).resolve();digest=pt._file_hash(original_path)
    original=ft._read(original_path)
    if original['schema_version']!=1 or pt.vp.payload_policy(original['payload'])!=3 or original['stage']!='claude_fallback':
        raise ValueError('Claude recovery requires a schema-1 policy-3 checkpoint.')
    c=original['compatibility'];records,paths=lineage(original,source_path)
    expected=ft.build_fallback_payload(paths['primary'],paths['evaluation'],paths['source'],policy_version=3)
    if original['payload']!=expected or c['payload_sha256']!=pt._digest(expected):
        raise ValueError('Claude recovery upstream payload/settings mismatch.')
    plan=pt.batch_primary_payload(expected,c['max_units'],c['max_chars'])
    if len(plan)!=len(original['batches']):raise ValueError('Claude recovery batch plan mismatch.')
    submitted=[]
    for b,planned in zip(original['batches'],plan):
        keys=[u['unit_key'] for u in planned['units']];req,user=fc.request(expected,keys)
        if b['unit_keys']!=keys or b['batch_index']!=planned['batch_index'] or b['request_hash']!=pt._digest(user):
            raise ValueError('Claude recovery historical request identity mismatch.')
        if b['attempts']:submitted.append((b,req))
        elif b['status']!='pending' or b['candidates']:raise ValueError('Claude recovery pending history mismatch.')
    if len(submitted)!=1:raise ValueError('Claude recovery requires exactly one submitted historical batch.')
    batch,request=submitted[0]
    if len(batch['attempts'])!=1 or batch['status']!='error' or batch['candidates']:
        raise ValueError('Claude recovery requires the exact first failed batch attempt.')
    attempt=batch['attempts'][0]
    if (attempt['attempt']!=1 or attempt['status']!='error' or attempt['error_kind']!='validation'
            or attempt['stop_reason']!='end_turn' or not isinstance(attempt['raw_response'],str)):
        raise ValueError('Claude recovery requires a complete saved provider response.')
    parsed=pt.parse_primary_response(attempt['raw_response'])
    if parsed!=attempt['response_json']:raise ValueError('Claude recovery raw/parsed response mismatch.')
    old=ft.validate_fallback_response(parsed,request,policy_version=3)
    if old!=attempt['validation'] or old['ok']:
        raise ValueError('Claude recovery historical validation mismatch.')
    old_independent=pt.validate_primary_response(parsed,request,policy_version=3)
    if old_independent['response_errors']:raise RecoveryValidationError(old_independent['response_errors'])
    events=[];errors=[]
    for unit in request['units']:
        key=unit['unit_key'];prior=old_independent['errors_by_unit'].get(key,[])
        policy=3 if not prior else 4
        if prior and any(not e.endswith(':boundary_whitespace_changed') for e in prior):
            errors.extend(prior);continue
        rows=[(i,r) for i,r in enumerate(parsed['translations']) if r.get('unit_key')==key]
        if len(rows)!=1:
            errors.append(key+':missing_or_ambiguous_response');continue
        index,row=rows[0]
        checked=ft.validate_fallback_response(dict(translations=[row]),{'units':[unit]},policy_version=policy)
        if not checked['ok']:
            errors.extend(checked['errors']);continue
        candidate=checked['candidates'][0]
        reference=dict(original_sha256=digest,batch_index=batch['batch_index'],attempt=1,
            request_id=f"b{batch['batch_index']}-a1",request_hash=batch['request_hash'],
            system_instruction_sha256=pt._digest(c['instruction']),model=c['model'],
            raw_response_sha256=hashlib.sha256(attempt['raw_response'].encode('utf-8')).hexdigest(),
            response_sha256=pt._digest(parsed),response_row_index=index,response_row_sha256=pt._digest(row),
            validation_policy_version=policy)
        candidate['provenance']=dict(request_id=reference['request_id'],request_hash=batch['request_hash'],
            model=c['model'],source_sha256=c['source_sha256'],source_unit_sha256=pt._digest(unit),
            offline_revalidation=deepcopy(reference))
        events.append(dict(unit_key=key,event='offline_revalidation',reference=reference,
                           historical_errors=deepcopy(prior),candidate=candidate))
    if errors:raise RecoveryValidationError(errors)
    effective=ft.build_fallback_payload(paths['primary'],paths['evaluation'],paths['source'],policy_version=4)
    compatibility=dict(c,payload_sha256=pt._digest(effective),instruction=ft.DEFAULT_SYSTEM_INSTRUCTION,
                       contract_version=2,recovery_original_sha256=digest)
    if pt._file_hash(original_path)!=digest or any(pt._file_hash(paths[k])!=r['sha256'] for k,r in records.items()):
        raise ValueError('Claude recovery ancestry changed during validation.')
    return original,events,records,effective,compatibility,digest


def read(path,source_path=None):
    path=Path(path).resolve();digest=pt._file_hash(path)
    artifact=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity=artifact.pop('integrity',None)
    if integrity!=pt._digest(artifact) or artifact.get('stage')!=STAGE or artifact.get('schema_version')!=VERSION:
        raise ValueError('Invalid Claude recovery artifact integrity/version/stage.')
    base=resolve(artifact['original_checkpoint'])
    original,events,records,payload,c,original_hash=derive(base,source_path)
    if (artifact['events']!=events or artifact['lineage']!=records or artifact['compatibility']!=c
            or artifact['original_settings']!=original['compatibility']):
        raise ValueError('Claude recovery events/policies/candidate digests/settings mismatch.')
    results=fc.initial(payload)
    for event in events:
        results[event['unit_key']].update(status='validated',candidate=deepcopy(event['candidate']),
            attempt_count=1,request_ids=[event['reference']['request_id']])
    batches=[]
    for b in pt.batch_primary_payload(payload,c['max_units'],c['max_chars']):
        keys=[u['unit_key'] for u in b['units']];_,user=fc.request(payload,keys)
        batches.append(dict(batch_index=b['batch_index'],unit_keys=keys,request_hash=fc.request_hash(user,c),
                            status='pending',attempts=[],candidates=[]))
    view=dict(schema_version=2,stage='claude_fallback',created_at=artifact['created_at'],compatibility=c,
        payload=payload,batches=batches,unit_results=results,provenance=deepcopy(original['provenance']),
        historical_batches=deepcopy(original['batches']),
        fallback_offline_recovery=dict(artifact_path=str(path),artifact_sha256=digest,
            original_checkpoint=deepcopy(artifact['original_checkpoint']),events=deepcopy(events)))
    fc.refresh(view)
    if pt._file_hash(path)!=digest or pt._file_hash(base)!=original_hash:
        raise ValueError('Claude recovery artifact/ancestor changed during read.')
    return view


def recover(original_checkpoint,source_path):
    base=Path(original_checkpoint).resolve()
    original,events,records,payload,c,digest=derive(base,source_path)
    path=base.with_name(f'fallback_recovery_{digest[:20]}.json')
    def result():
        state=read(path,source_path)
        return dict(path=str(path),sha256=pt._file_hash(path),unit_keys=[e['unit_key'] for e in state['fallback_offline_recovery']['events']])
    if path.exists():return result()
    lock=path.with_name(path.name+'.lock')
    with lock.open('x',encoding='utf-8') as stream:stream.write('Claude offline revalidation\n')
    try:
        if not path.exists():
            if pt._file_hash(base)!=digest:raise ValueError('Original Claude checkpoint changed before publication.')
            artifact=dict(schema_version=VERSION,stage=STAGE,created_at=pt._now(),
                original_checkpoint=dict(path=str(base),sha256=digest),original_settings=deepcopy(original['compatibility']),
                lineage=records,compatibility=c,events=events)
            pt._write_checkpoint(path,artifact)
        return result()
    finally:lock.unlink()


def seed(state):
    """Verify a continuation's immutable seed, including effective settings/payload."""
    reference=state.get('offline_recovery_parent')
    if reference is None:return fc.initial(state['payload']),None
    path=resolve(reference);parent=read(path)
    c=deepcopy(parent['compatibility']);c['recovery_parent_sha256']=reference['sha256']
    if state['compatibility']!=c or state['payload']!=parent['payload']:
        raise ValueError('Claude continuation recovery/source/settings mismatch.')
    return deepcopy(parent['unit_results']),parent


def continuation(artifact_path):
    """Create/reuse a mutable schema-2 child only on explicit later Claude resume."""
    artifact_path=Path(artifact_path).resolve();parent=read(artifact_path);digest=pt._file_hash(artifact_path)
    path=artifact_path.with_name(f'fallback_continuation_{digest[:20]}.json')
    if path.exists():fc.read(path);return path
    lock=path.with_name(path.name+'.lock')
    with lock.open('x',encoding='utf-8') as stream:stream.write('Claude continuation initialization\n')
    try:
        if not path.exists():
            c=dict(parent['compatibility'],recovery_parent_sha256=digest)
            state=dict(schema_version=2,stage='claude_fallback',created_at=pt._now(),compatibility=c,
                payload=deepcopy(parent['payload']),batches=deepcopy(parent['batches']),
                unit_results=deepcopy(parent['unit_results']),provenance=deepcopy(parent['provenance']),
                offline_recovery_parent=dict(path=str(artifact_path),sha256=digest))
            fc.refresh(state);fc.save(path,state)
        fc.read(path);return path
    finally:lock.unlink()


def history_batches(state):
    """Effective batches with historical attempts attached only for per-unit history."""
    batches=deepcopy(state['batches'])
    for batch in batches:
        historical=[]
        for old in state.get('historical_batches',[]):
            if set(old['unit_keys']) & set(batch['unit_keys']):
                historical.extend(dict(deepcopy(a),unit_keys=deepcopy(old['unit_keys'])) for a in old['attempts'])
        batch['attempts']=historical+batch['attempts']
    return batches
