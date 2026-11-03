"""Claude schema-2 per-paragraph history replay and unresolved-only execution.

Schema 1 stays in fallback_translation unchanged. No automatic retries: at most
one request per unfinished batch per invocation, using the existing invocation cap.
"""
from copy import deepcopy
import json
from pathlib import Path
from . import primary_translation as pt

VERSION=2


def save(path,state):
    """Keep derived historical views out of the mutable continuation file."""
    record=deepcopy(state)
    record.pop('historical_batches',None);record.pop('fallback_offline_recovery',None)
    pt._write_checkpoint(path,record)


def seeded(state):
    if state.get('offline_recovery_parent'):
        from .fallback_recovery import seed
        return seed(state)
    return initial(state['payload']),None


def attempt_id(state,batch_index,number):
    prefix='continuation-' if state.get('offline_recovery_parent') else ''
    return f'{prefix}b{batch_index}-a{number}'


def request(payload, keys):
    selected={u['unit_key']:u for u in payload['units']}
    value=dict(metadata=dict(payload['metadata'],unit_count=len(keys)),units=[deepcopy(selected[k]) for k in keys])
    user='Correct the rejected PPTX translations. Return only the required JSON.\n\n'+json.dumps(value,ensure_ascii=False,indent=2)
    return value,user


def request_hash(user, c):
    return pt._digest(dict(user=user,system=c['instruction'],model=c['model'],max_tokens=c['max_tokens']))


def initial(payload):
    return {u['unit_key']:dict(status='pending',candidate=None,attempt_count=0,request_ids=[],errors=[],error_kind=None)
            for u in payload['units']}


def apply(results, attempt, c):
    checked=attempt['validation']
    candidates={u['unit_key']:u for u in checked['candidates']} if checked else {}
    for key in attempt['unit_keys']:
        r=results[key];r['attempt_count']+=1;r['request_ids'].append(attempt['request_id'])
        if attempt['status'] in ('running','response_saved'):
            r.update(status='running',errors=[],error_kind=None)
        elif key in candidates:
            candidate=deepcopy(candidates[key])
            unit=next(u for u in attempt['request']['units'] if u['unit_key']==key)
            candidate['provenance']=dict(request_id=attempt['request_id'],request_hash=attempt['request_hash'],
                source_sha256=c['source_sha256'],source_unit_sha256=pt._digest(unit),model=c['model'])
            r.update(status='validated',candidate=candidate,errors=[],error_kind=None)
        elif checked:
            errors=checked['errors_by_unit'].get(key,checked['response_errors'])
            missing=any(e.endswith(':missing_unit_id') for e in errors)
            r.update(status='missing' if missing else 'invalid',errors=deepcopy(errors),error_kind='validation')
        else:
            r.update(status='provider_error' if attempt['error_kind']=='provider' else 'invalid',
                     errors=[attempt['error']],error_kind=attempt['error_kind'])


def refresh(state):
    for batch in state['batches']:
        results=[state['unit_results'][k] for k in batch['unit_keys']]
        batch['candidates']=[deepcopy(r['candidate']) for r in results if r['status']=='validated']
        batch['status']=('completed' if all(r['status']=='validated' for r in results) else
                         'running' if any(r['status']=='running' for r in results) else
                         'partial' if batch['candidates'] else 'error' if batch['attempts'] else 'pending')
    state['status']='completed' if all(b['status']=='completed' for b in state['batches']) else 'partial'


def finish(state, attempt):
    from .fallback_translation import validate_fallback_response
    try:
        if attempt['stop_reason']!='end_turn':raise ValueError('Claude response did not complete: '+str(attempt['stop_reason']))
        parsed=pt.parse_primary_response(attempt['raw_response'])
    except (ValueError,TypeError) as exc:
        attempt.update(status='error',error=f'{type(exc).__name__}: {exc}',error_kind='validation')
    else:
        checked=validate_fallback_response(parsed,attempt['request'],policy_version=4)
        attempt.update(response_json=parsed,validation=checked,
                       status='success' if checked['ok'] else 'partial',
                       error=None if checked['ok'] else '; '.join(checked['errors']),
                       error_kind=None if checked['ok'] else 'validation')
    attempt['finished_at']=pt._now()
    apply(state['unit_results'],attempt,state['compatibility']);refresh(state)


def read(path):
    from .fallback_translation import validate_fallback_response
    state=json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity=state.pop('integrity',None)
    if integrity!=pt._digest(state) or state.get('schema_version')!=VERSION or state.get('stage')!='claude_fallback':
        raise ValueError('Invalid Claude schema-2 integrity/version/stage.')
    c=state['compatibility'];payload=state['payload']
    if (c['contract_version']!=VERSION or pt.vp.payload_policy(payload)!=4
            or c['payload_sha256']!=pt._digest(payload)):
        raise ValueError('Claude policy/payload compatibility mismatch.')
    results,parent=seeded(state)
    if len(results)!=len(payload['units']):raise ValueError('Duplicate Claude source units.')
    plan=pt.batch_primary_payload(payload,c['max_units'],c['max_chars'])
    if len(plan)!=len(state['batches']):raise ValueError('Claude batch plan mismatch.')
    for expected,batch in zip(plan,state['batches']):
        keys=[u['unit_key'] for u in expected['units']]
        _,full_user=request(payload,keys)
        if (batch['unit_keys']!=keys or batch['batch_index']!=expected['batch_index']
                or batch['request_hash']!=request_hash(full_user,c)):
            raise ValueError('Claude original request plan mismatch.')
        for number,a in enumerate(batch['attempts'],1):
            pending=[k for k in keys if results[k]['status']!='validated']
            req,user=request(payload,pending)
            if (not pending or a['attempt']!=number or a['request_id']!=attempt_id(state,batch['batch_index'],number)
                    or a['unit_keys']!=pending or a['request']!=req or a['request_hash']!=request_hash(user,c)):
                raise ValueError('Claude retry subset/request identity mismatch.')
            if a['status'] in ('running','response_saved'):
                if (number!=len(batch['attempts']) or a['response_json'] is not None or a['validation'] is not None
                        or (a['status']=='running' and a['raw_response'] is not None)
                        or (a['status']=='response_saved' and not isinstance(a['raw_response'],str))):
                    raise ValueError('Invalid Claude in-flight response.')
            elif a['response_json'] is not None:
                if a['stop_reason']!='end_turn' or pt.parse_primary_response(a['raw_response'])!=a['response_json']:
                    raise ValueError('Claude raw/parsed response mismatch.')
                checked=validate_fallback_response(a['response_json'],req,policy_version=4)
                if (checked!=a['validation'] or a['status']!=('success' if checked['ok'] else 'partial')
                        or a['error_kind']!=(None if checked['ok'] else 'validation')
                        or a['error']!=(None if checked['ok'] else '; '.join(checked['errors']))):
                    raise ValueError('Claude saved per-paragraph validation mismatch.')
            else:
                if (a['status'] not in ('error','interrupted') or a['validation'] is not None
                        or not a['error'] or a['error_kind'] not in ('provider','validation')):
                    raise ValueError('Invalid Claude failure record.')
                if a['error_kind']=='validation' and a['stop_reason']=='end_turn':
                    try:pt.parse_primary_response(a['raw_response'])
                    except (ValueError,TypeError):pass
                    else:raise ValueError('Claude parse failure contradicts saved response.')
            apply(results,a,c)
    if results!=state['unit_results']:raise ValueError('Claude candidate/provenance/per-unit history mismatch.')
    replay=deepcopy(state);refresh(replay)
    if replay['batches']!=state['batches'] or replay['status']!=state['status']:
        raise ValueError('Claude progress accounting mismatch.')
    if parent is not None:
        state['historical_batches']=deepcopy(parent['historical_batches'])
        state['fallback_offline_recovery']=deepcopy(parent['fallback_offline_recovery'])
    return state


def run(client, primary_checkpoint, evaluation_checkpoint, source_path, checkpoint_path,
        model_name, system_instruction=None, *, resume=False, max_batches=None,
        max_units=5,max_chars=8000,timeout=60,max_tokens=8192):
    from . import fallback_translation as ft
    instruction=ft.DEFAULT_SYSTEM_INSTRUCTION if system_instruction is None else system_instruction
    if not all(isinstance(v,str) and v.strip() for v in (model_name,instruction)):raise ValueError('Model and instruction are required.')
    if max_batches is not None and (type(max_batches) is not int or max_batches<0):raise ValueError('max_batches must be nonnegative or None.')
    if type(max_tokens) is not int or max_tokens<1 or type(timeout) not in (int,float) or not 0<timeout<float('inf'):
        raise ValueError('Positive token limit and finite timeout required.')
    primary,evaluator,source,path=map(lambda p:Path(p).resolve(),(primary_checkpoint,evaluation_checkpoint,source_path,checkpoint_path))
    if path in (primary,evaluator,source) or path.suffix.lower()!='.json':raise ValueError('Select a separate .json fallback checkpoint.')
    inputs=[primary,evaluator,source];hashes=[pt._file_hash(p) for p in inputs]
    payload=ft.build_fallback_payload(primary,evaluator,source,policy_version=4)
    plan=pt.batch_primary_payload(payload,max_units,max_chars)
    c=dict(primary_sha256=hashes[0],evaluation_sha256=hashes[1],source_sha256=hashes[2],
           payload_sha256=pt._digest(payload),model=model_name,instruction=instruction,
           max_units=max_units,max_chars=max_chars,timeout=timeout,max_tokens=max_tokens,contract_version=VERSION)
    def unchanged():
        if [pt._file_hash(p) for p in inputs]!=hashes:raise ValueError('Upstream checkpoint or source changed during fallback.')
    unchanged();path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_name(path.name+'.lock')
    with lock.open('x',encoding='utf-8') as stream:stream.write('Claude schema-2 writer\n')
    try:
        if resume:
            state=read(path)
            if state.get('offline_recovery_parent'):
                c.update({k:state['compatibility'][k] for k in ('recovery_original_sha256','recovery_parent_sha256')})
            if state['compatibility']!=c or state['payload']!=payload:raise ValueError('Fallback checkpoint/request compatibility mismatch.')
        else:
            if path.exists():raise FileExistsError(path)
            batches=[]
            for b in plan:
                keys=[u['unit_key'] for u in b['units']];_,user=request(payload,keys)
                batches.append(dict(batch_index=b['batch_index'],unit_keys=keys,request_hash=request_hash(user,c),
                                    status='pending',attempts=[],candidates=[]))
            state=dict(schema_version=VERSION,stage='claude_fallback',created_at=pt._now(),compatibility=c,payload=payload,
                batches=batches,unit_results=initial(payload),status='partial',
                provenance=dict(primary_checkpoint=str(primary),evaluation_checkpoint=str(evaluator),source_path=str(source),
                    provider='anthropic',model=model_name,candidate_status='not_evaluated',diagnostic=payload['diagnostic']))
            refresh(state);save(path,state)
        submitted=0
        for batch in state['batches']:
            if batch['attempts'] and batch['attempts'][-1]['status'] in ('running','response_saved'):
                a=batch['attempts'][-1]
                # Derive results before the interrupted request; do not count it twice.
                replay,_=seeded(state)
                for prior in state['batches']:
                    for old in prior['attempts']:
                        if old is not a:apply(replay,old,c)
                state['unit_results']=replay
                if a['status']=='response_saved':finish(state,a)
                else:
                    a.update(status='interrupted',error='Previous process ended before durable response.',error_kind='provider',finished_at=pt._now())
                    apply(state['unit_results'],a,c);refresh(state)
                unchanged();save(path,state)
            keys=[k for k in batch['unit_keys'] if state['unit_results'][k]['status']!='validated']
            if not keys or (max_batches is not None and submitted>=max_batches):continue
            unchanged();req,user=request(payload,keys);number=len(batch['attempts'])+1
            a=dict(attempt=number,request_id=attempt_id(state,batch['batch_index'],number),unit_keys=keys,
                request=req,request_hash=request_hash(user,c),status='running',started_at=pt._now(),
                raw_response=None,response_json=None,validation=None,error=None,error_kind=None,stop_reason=None)
            batch['attempts'].append(a)
            # Persist submission before invoking the provider; all retry subsets are replayable.
            apply(state['unit_results'],a,c);refresh(state);save(path,state)
            submitted+=1;print(f"Claude batch {batch['batch_index']}/{len(plan)} | units={len(keys)} | request={number}")
            try:
                active=client.with_options(timeout=timeout,max_retries=0) if hasattr(client,'with_options') else client
                response=active.messages.create(model=model_name,system=instruction,messages=[dict(role='user',content=user)],max_tokens=max_tokens)
                raw=''.join(b.text for b in response.content if getattr(b,'type',None)=='text')
                stop=response.stop_reason
            except BaseException as exc:
                # Rebuild only this attempt's outcomes; the persisted request count remains one.
                for k in keys:
                    state['unit_results'][k]['attempt_count']-=1;state['unit_results'][k]['request_ids'].pop()
                a.update(status='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error',
                         error=f'{type(exc).__name__}: {exc}',error_kind='provider',finished_at=pt._now())
                apply(state['unit_results'],a,c);refresh(state);save(path,state)
                if not isinstance(exc,Exception):raise
                raise ft.FallbackStageError(f'Claude generation failed; inspect {path}: {exc}') from exc
            a.update(raw_response=raw,stop_reason=stop,status='response_saved')
            refresh(state);save(path,state);unchanged()
            for k in keys:
                state['unit_results'][k]['attempt_count']-=1;state['unit_results'][k]['request_ids'].pop()
            finish(state,a);save(path,state)
            # Retain valid paragraphs and continue later batches. No same-invocation retry.
            if not a['validation'] or not a['validation']['candidates']:
                raise ft.FallbackStageError(f'Claude generation failed; inspect {path}: {a["error"]}')
        unchanged();return ft.inspect_fallback_checkpoint(path)
    finally:lock.unlink()
