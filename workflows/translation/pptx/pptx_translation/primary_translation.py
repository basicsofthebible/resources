"""Primary Gemini stage only: injected client, strict validation, explicit resume.

No SDK imports, environment/credential access, evaluation, fallback or PPTX writes.
Callers provide classified paragraph units and a Gemini-compatible client.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
from . import checkpoint_io
from .checkpoint_io import tempfile as tempfile  # Compatibility fault-injection namespace.
from . import validation_policy as vp

VERSION = 2
MAX_ATTEMPTS = 3
POLICY_VERSION = vp.CURRENT


def _now():
    return checkpoint_io.now()


def _digest(value):
    return checkpoint_io.digest(value)


def _file_hash(path):
    return checkpoint_io.file_hash(path)


def build_primary_payload(units, source_language, target_language, *, policy_version=POLICY_VERSION):
    """Full paragraph context with optional ordered span descriptors."""
    vp.require_policy(policy_version)
    rows, seen = [], set()
    for unit in units:
        if not isinstance(unit, dict):
            raise ValueError('Source units must be dictionaries.')
        key = unit.get('unit_key')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Missing or duplicate source unit IDs.')
        seen.add(key)
        if unit.get('action') not in ('translate', 'preserve'):
            raise ValueError('Primary stage requires classified units.')
        if unit.get('action') != 'translate' or unit.get('preserve_source'):
            continue
        if not isinstance(unit.get('text'), str) or not unit['text'].strip():
            raise ValueError(f'Missing source text: {key}')
        row = dict(unit_key=key, source_text=unit['text'],
                   translatable_text=unit.get('translatable_text') or unit['text'],
                   number_prefix=unit.get('number_prefix') or '', number_suffix=unit.get('number_suffix') or '',
                   text_kind=unit.get('text_kind'), translation_note=unit.get('translation_note') or '',
                   slide_number=unit.get('slide_number'), source_type=unit.get('source_type'))
        if 'styled_spans' in unit:
            spans = unit['styled_spans']
            ids = set()
            if not isinstance(spans, list) or not spans:
                raise ValueError(f'Invalid source spans: {key}')
            for span in spans:
                if not isinstance(span, dict):
                    raise ValueError(f'Invalid source span: {key}')
                sid = span.get('span_id')
                kind, text = span.get('kind'), span.get('text')
                if not isinstance(sid, str) or not sid or sid in ids or not isinstance(text, str):
                    raise ValueError(f'Missing, duplicate or invalid source span IDs: {key}')
                ids.add(sid)
                if kind not in ('text', 'soft_break', 'tab', 'fixed_whitespace'):
                    raise ValueError(f'Unsupported source span: {key}/{sid}')
                if ((kind == 'soft_break' and text != '\v') or (kind == 'tab' and text != '\t')
                        or (kind == 'fixed_whitespace' and (not text or not text.isspace()))
                        or (kind == 'text' and _text_error(text, text))):
                    raise ValueError(f'Invalid source structural token: {key}/{sid}')
            if ''.join(s['text'] for s in spans) != unit['text']:
                raise ValueError(f'Source spans do not reproduce paragraph: {key}')
            row['styled_spans'] = [{k:s[k] for k in ('span_id','kind','text')} for s in spans]
        if policy_version>=3:
            row['translation_note']=row['translation_note'].replace(
                'preserve chapter, verse, range, comma, and version numbers.',
                'preserve Bible book identities, chapter/verse and range meaning, and version identifiers.').replace(
                'preserving numeric values and sequence markers.',
                'preserving numerical meaning and explicitly locked sequence markers.')
        rows.append(row)
    payload = dict(metadata=dict(payload_type='pptx_primary_translation', source_stage='pptx_units_classified',
                              source_language=source_language, target_language=target_language,
                              unit_count=len(rows), response_contract_version=policy_version,
                              response_contract={
                                  'root':'translations',
                                  'ordinary':'Each row has literal JSON keys unit_key, source_text, translated_text (complete paragraph). Copy source_text exactly; never use exact_source_text as a key.',
                                  'styled':'Each row has literal JSON keys unit_key, source_text, translated_spans [{span_id,text}]. Copy source_text exactly. Return ONLY IDs whose input kind is text, each once in original order. Never return soft_break, tab or fixed_whitespace IDs, even unchanged. No translated_text field.',
                                  'structure':'Keep full paragraph context. Structural spans, soft breaks and tabs are maintained locally, never response items. Preserve digits, numeric markers and exact leading/trailing whitespace of every text span. Choose natural wording within these boundaries; do not replace a leading space with punctuation. For example, a span beginning and ending with one space must still begin and end with one space.',
                                  'examples':{'translations':[
                                      {'unit_key':'ordinary_example','source_text':'Hello','translated_text':'Hola'},
                                      {'unit_key':'styled_example','source_text':'Hello\vworld',
                                       'translated_spans':[{'span_id':'p01','text':'Hola'}, {'span_id':'p03','text':'mundo'}]}]},
                                  'example_note':'The styled example assumes p01 and p03 are text and p02 is a soft_break. p02 is intentionally absent from the response. Use actual input IDs and source_text, not example values.'}),
                units=rows)
    if policy_version>=3:
        payload['metadata']['validation_policy_version']=policy_version
        payload['metadata']['response_contract']['structure']=(
            'Translate each complete paragraph in context across styled spans. Structural spans, '
            'soft breaks, tabs and fixed whitespace are maintained locally; never return them. '
            'Keep every text-span ID in source order, nonblank text, exact leading/trailing '
            'whitespace, and declared number_prefix/number_suffix markers. Preserve numerical '
            'meaning, Bible book/reference identity, ranges, and literal identifiers. Natural '
            'number renderings and translated book names may change digit spelling or presence; '
            'generic digit differences are advisory evidence for semantic evaluation, not permission '
            'to change numerical meaning. Never insert controls into text spans.')
    if policy_version>=4:
        payload['metadata']['response_contract']['structure']=(
            'Translate complete paragraphs in context across styled spans. Keep every text-span ID '
            'exactly once in source order and nonblank. Structural spans (fixed_whitespace, tabs, '
            'soft breaks) are copied locally; never return them. Ordinary U+0020 spacing within '
            'text spans, including their boundaries, may follow target-language conventions. '
            'Do not change significant Unicode whitespace, directional/format controls, or declared '
            'number_prefix/number_suffix markers. Preserve numerical and reference meaning; digit '
            'differences are advisory, not permission to change meaning. Do not insert unsafe controls. '
            'Keep translated wording associated with its intended style; do not merge or drop spans.')
    return payload


def primary_validation_policy(checkpoint_path=None):
    """Read-only policy for a fresh plan or an exact saved primary checkpoint."""
    return POLICY_VERSION if checkpoint_path is None else read_primary_state(checkpoint_path)['compatibility']['policy_version']


def batch_primary_payload(payload, max_units=80, max_chars=12000):
    """v1 ordered count/JSON-character batching; an oversized unit stays intact."""
    if type(max_units) is not int or max_units < 1 or type(max_chars) is not int or max_chars < 1:
        raise ValueError('Batch limits must be positive integers.')
    batches, current, size = [], [], 0
    for row in payload['units']:
        chars = len(json.dumps(row, ensure_ascii=False))
        if current and (len(current) >= max_units or size + chars > max_chars):
            batches.append(dict(units=current, approx_chars=size))
            current, size = [], 0
        current.append(row)
        size += chars
    if current:
        batches.append(dict(units=current, approx_chars=size))
    for index, batch in enumerate(batches, 1):
        batch.update(batch_index=index, unit_count=len(batch['units']), total_batches=len(batches))
    return batches


def primary_batch_manifest(batches):
    """Compact view of the complete plan; never selects or submits work."""
    return [dict(batch_index=b['batch_index'], unit_count=b['unit_count'],
                 ordinary_units=sum('styled_spans' not in u for u in b['units']),
                 styled_units=sum('styled_spans' in u for u in b['units']),
                 multi_span_units=sum(sum(s['kind']=='text' for s in u.get('styled_spans', [])) > 1 for u in b['units']),
                 text_spans=sum(s['kind']=='text' for u in b['units'] for s in u.get('styled_spans', [])),
                 structural_items=sum(s['kind']!='text' for u in b['units'] for s in u.get('styled_spans', [])),
                 approx_chars=b['approx_chars'],
                 first_unit=b['units'][0]['unit_key'], last_unit=b['units'][-1]['unit_key'])
            for b in batches]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON property: {key}')
        result[key] = value
    return result


def parse_primary_response(text):
    """Parse JSON first, normalize translation arrays, and support wrapped JSON."""
    if not isinstance(text, str):
        raise ValueError('Model response text must be a string.')
    def reject_constant(value):
        raise ValueError(f'Invalid JSON constant: {value}')
    def loads(value):
        return json.loads(value, object_pairs_hook=_unique_object, parse_constant=reject_constant)
    try:
        parsed = loads(text)
    except json.JSONDecodeError:
        # Preserve object extraction from fences/prose without stripping an
        # array's brackets or salvaging objects from malformed array JSON.
        starts = [i for i in (text.find('{'), text.find('[')) if i >= 0]
        if not starts:
            raise
        start = min(starts)
        closing = ']' if text[start] == '[' else '}'
        end = text.rfind(closing)
        if end <= start:
            raise
        parsed = loads(text[start:end+1])
    if isinstance(parsed, list):
        if any(not isinstance(row, dict) or not isinstance(row.get('unit_key'), str) for row in parsed):
            raise ValueError('Model response array must contain translation objects with unit IDs.')
        parsed = {'translations': parsed}
    # Evaluation also uses this parser for its own object envelope. Leave object
    # contracts to the existing stage-specific validators.
    if not isinstance(parsed, dict):
        raise ValueError('Model response must be a JSON object or translation array.')
    return parsed


def _text_error(source, translated, allow_structure=False, *, policy_version=POLICY_VERSION, check_tokens=True):
    return vp.text_error(source,translated,allow_structure=allow_structure,policy_version=policy_version,check_tokens=check_tokens)


def _validate_paragraph_response(parsed, payload, *, policy_version=POLICY_VERSION):
    """Strict atomic validation of a single paragraph and all its spans."""
    errors, accepted, seen = [], [], set()
    requested = {u['unit_key']:u for u in payload['units']}
    rows = parsed.get('translations') if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return dict(ok=False, errors=['translations_must_be_a_list'], translations=[])
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('unit_key'), str):
            errors.append('invalid_unit_row')
            continue
        key = row['unit_key']
        if key in seen:
            errors.append(f'{key}:duplicate_unit_id')
            continue
        seen.add(key)
        if key not in requested:
            errors.append(f'{key}:unexpected_unit_id')
            continue
        unit = requested[key]
        if row.get('source_text') != unit['source_text']:
            errors.append(f'{key}:source_text_mismatch')
            continue
        result = dict(unit_key=key)
        if 'styled_spans' in unit:
            spans = unit['styled_spans']
            translations = row.get('translated_spans')
            expected_ids = [s['span_id'] for s in spans if s['kind']=='text']
            if ('translated_text' in row or not isinstance(translations, list)
                    or any(not isinstance(t, dict) for t in translations)
                    or [t.get('span_id') for t in translations] != expected_ids):
                errors.append(f'{key}:invalid_or_incomplete_span_ids')
                continue
            values = {t['span_id']:t.get('text') for t in translations}
            text_parts, bad = [], False
            for span in spans:
                value = values[span['span_id']] if span['kind']=='text' else span['text']
                if span['kind']=='text':
                    issue = _text_error(span['text'], value,policy_version=policy_version,check_tokens=policy_version<4)
                    if issue:
                        errors.append(f'{key}/{span["span_id"]}:{issue}')
                        bad = True
                text_parts.append(value)
            if bad:
                continue
            translated = ''.join(text_parts)
            result['primary_span_translations'] = deepcopy(translations)
        else:
            translated = row.get('translated_text')
            if 'translated_spans' in row:
                errors.append(f'{key}:unexpected_span_response')
                continue
        issue = _text_error(unit['source_text'], translated, allow_structure=True,policy_version=policy_version,
                            check_tokens=policy_version<4 or 'styled_spans' not in unit)
        if issue:
            errors.append(f'{key}:{issue}')
            continue
        if ((unit['number_prefix'] and not translated.startswith(unit['number_prefix']))
                or (unit['number_suffix'] and not translated.endswith(unit['number_suffix']))):
            errors.append(f'{key}:number_prefix_or_suffix_changed')
            continue
        result['primary_translation'] = translated
        accepted.append(result)
    errors += [f'{key}:missing_unit_id' for key in requested if key not in seen]
    return dict(ok=not errors, errors=errors, translations=accepted if not errors else [])


def validate_primary_response(parsed, payload, *, policy_version=None):
    """Accept unique valid paragraphs independently; every styled paragraph is atomic."""
    policy_version=vp.payload_policy(payload) if policy_version is None else vp.require_policy(policy_version)
    rows = parsed.get('translations') if isinstance(parsed,dict) else None
    requested = {u['unit_key']:u for u in payload['units']}
    by_key, response_errors = {}, []
    if not isinstance(rows,list):
        response_errors.append('translations_must_be_a_list')
    else:
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('unit_key'),str):
                response_errors.append('invalid_unit_row')
            elif row['unit_key'] not in requested:
                response_errors.append(row['unit_key']+':unexpected_unit_id')
            else:
                by_key.setdefault(row['unit_key'],[]).append(row)
    accepted, failures = [], {}
    for key, unit in requested.items():
        matches = by_key.get(key,[])
        if len(matches)!=1:
            failures[key] = [key+(':duplicate_unit_id' if matches else ':missing_unit_id')]
        else:
            checked = _validate_paragraph_response({'translations':matches},{'units':[unit]},policy_version=policy_version)
            if checked['ok']:accepted.extend(checked['translations'])
            else:failures[key] = checked['errors']
    errors = response_errors+[error for values in failures.values() for error in values]
    checked=dict(ok=not errors,translations=accepted,errors=errors,
                 errors_by_unit=failures,response_errors=response_errors)
    if policy_version>=3:
        checked['warnings_by_unit']={r['unit_key']:vp.numeric_advisories(requested[r['unit_key']],r,policy_version=policy_version) for r in accepted}
    return checked


def _write_checkpoint(path, state):
    """Compatibility entry point, retaining the late-bound primary digest hook."""
    return checkpoint_io.write_checkpoint(path, state, digest_function=_digest)


def _initial_results(payload):
    return {u['unit_key']:dict(status='pending',attempt_count=0,retry_count=0,request_ids=[],candidate=None,error=None,error_kind=None)
            for u in payload['units']}


def _request(state, batch, keys, results):
    c = state['compatibility']
    payload = build_primary_payload(state['units'],c['source_language'],c['target_language'],policy_version=c['policy_version'])
    indexed = {u['unit_key']:u for u in payload['units']}
    rows = []
    for key in keys:
        row = deepcopy(indexed[key])
        if results[key]['attempt_count']:
            row['validation_feedback'] = list(results[key]['error'] or ['Previous request was interrupted; return the complete paragraph response.'])
        rows.append(row)
    request = dict(metadata=dict(payload['metadata'],unit_count=len(rows),batch_index=batch['batch_index']),units=rows)
    prompt = c['instruction']+'\n\nINPUT JSON:\n'+json.dumps(request,ensure_ascii=False,indent=2)
    request_hash = _digest(dict(prompt=prompt,model=c['model'],config=c['generation']))
    return request,prompt,request_hash


def _candidate(result, request, attempt, c):
    value = deepcopy(result)
    value['candidate_sha256'] = _digest(result)
    value['provenance'] = dict(request_id=attempt['request_id'],request_hash=attempt['request_hash'],
                               source_sha256=c['source_sha256'],source_unit_sha256=_digest(request),model=c['model'])
    value['validation'] = dict(ok=True,errors=[])
    if c['policy_version']>=3:
        value['validation'].update(policy_version=c['policy_version'],warnings=vp.numeric_advisories(request,result,policy_version=c['policy_version']))
    return value


def _apply_attempt(results, attempt, c):
    if attempt['status'] in ('running','response_saved'):return
    checked = attempt['validation']
    accepted = {u['unit_key']:u for u in checked['translations']} if checked else {}
    requested = {u['unit_key']:u for u in attempt['request']['units']}
    for key in attempt['unit_keys']:
        record = results[key]
        if key in accepted:
            record.update(status='validated',candidate=_candidate(accepted[key],requested[key],attempt,c),error=None,error_kind=None)
        else:
            errors = checked['errors_by_unit'][key] if checked else [attempt['error']]
            record.update(status='exhausted' if record['attempt_count']>=MAX_ATTEMPTS else 'retry_pending',
                          error=errors,error_kind=attempt['error_kind'] or 'validation')


def _refresh(state):
    results = state['unit_results']
    for batch in state['batches']:
        statuses = {results[k]['status'] for k in batch['unit_keys']}
        batch['status'] = ('completed' if statuses <= {'validated'} else 'exhausted' if statuses <= {'validated','exhausted'}
                           else 'pending' if statuses=={'pending'} else 'partial')
    state['status'] = ('completed' if all(r['status']=='validated' for r in results.values()) else 'partial')


def _read_checkpoint(path):
    state = json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=_unique_object)
    if not isinstance(state,dict):raise ValueError('Primary checkpoint must be an object.')
    integrity = state.pop('integrity',None)
    if integrity!=_digest(state) or state.get('schema_version')!=VERSION or state.get('stage')!='primary_gemini':
        raise ValueError('Unsupported/corrupt primary checkpoint. A fresh run is required; historical schemas are retired.')
    c = state['compatibility']
    if type(c['policy_version']) is not int or c['policy_version'] not in vp.SUPPORTED or c['max_attempts']!=MAX_ATTEMPTS or c['units_sha256']!=_digest(state['units']):
        raise ValueError('Primary source/policy compatibility mismatch.')
    if c['generation'].get('http_options')!={'retry_options':{'attempts':1}}:
        raise ValueError('Primary SDK retry policy mismatch.')
    payload = build_primary_payload(state['units'],c['source_language'],c['target_language'],policy_version=c['policy_version'])
    plan = batch_primary_payload(payload,c['max_units'],c['max_chars'])
    if len(plan)!=len(state['batches']):raise ValueError('Primary batch plan mismatch.')
    results = _initial_results(payload)
    for expected, batch in zip(plan,state['batches']):
        if batch['batch_index']!=expected['batch_index'] or batch['unit_keys']!=[u['unit_key'] for u in expected['units']]:
            raise ValueError('Primary batch identities changed.')
        for number,attempt in enumerate(batch['attempts'],1):
            keys = [k for k in batch['unit_keys'] if results[k]['status']!='validated' and results[k]['attempt_count']<MAX_ATTEMPTS]
            request,_,request_hash = _request(state,batch,keys,results)
            if (not keys or attempt['attempt']!=number or attempt['unit_keys']!=keys
                    or attempt['request']!=request or attempt['request_hash']!=request_hash
                    or attempt['request_id']!=f"b{batch['batch_index']}-a{number}"):
                raise ValueError('Primary request/subset/history mismatch.')
            for key in keys:
                results[key]['attempt_count']+=1
                results[key]['retry_count']=max(0,results[key]['attempt_count']-1)
                results[key]['request_ids'].append(attempt['request_id'])
            if attempt['status'] not in ('running','response_saved','success','partial','error','interrupted'):
                raise ValueError('Invalid primary attempt status.')
            if attempt['status'] in ('running','response_saved'):
                if number!=len(batch['attempts']):raise ValueError('Unfinished request precedes another request.')
                for key in keys:results[key]['status']='running'
            elif attempt['response_json'] is not None:
                if parse_primary_response(attempt['raw_response'])!=attempt['response_json']:
                    raise ValueError('Primary raw/parsed response mismatch.')
                checked = validate_primary_response(attempt['response_json'],request,policy_version=c['policy_version'])
                if checked!=attempt['validation'] or attempt['status']!=('success' if checked['ok'] else 'partial'):
                    raise ValueError('Primary response validation mismatch.')
            elif attempt['validation'] is not None or not attempt['error']:
                raise ValueError('Invalid primary error record.')
            if attempt['error_kind']=='parse':
                try:parse_primary_response(attempt['raw_response'])
                except (ValueError,TypeError):pass
                else:
                    # Historical array responses failed after the old parser
                    # stripped their brackets. Verify that exact saved failure;
                    # retain its status and attempt budget rather than recover it.
                    raw = attempt['raw_response']
                    if not isinstance(json.loads(raw),list):
                        raise ValueError('Primary parse error contradicts saved raw response.')
                    try:json.loads(raw[raw.find('{'):raw.rfind('}')+1],object_pairs_hook=_unique_object)
                    except json.JSONDecodeError as exc:
                        if attempt['error']!=f'{type(exc).__name__}: {exc}':
                            raise ValueError('Primary historical parse error mismatch.') from exc
                    else:raise ValueError('Primary parse error contradicts saved raw response.')
            _apply_attempt(results,attempt,c)
    if results!=state['unit_results']:raise ValueError('Primary per-unit candidate/provenance/history mismatch.')
    replay = dict(state,unit_results=results,batches=deepcopy(state['batches']))
    _refresh(replay)
    if replay['status']!=state['status'] or replay['batches']!=state['batches']:
        raise ValueError('Primary progress accounting mismatch.')
    return state


def read_primary_state(path, source_path=None):
    """Authoritative primary candidate path: historical replay or verified overlay."""
    header=json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=_unique_object)
    if isinstance(header,dict) and header.get('stage')=='primary_offline_recovery':
        from .primary_recovery import read
        return read(path,source_path)
    return _read_checkpoint(path)


def candidate_advisories(unit, candidate):
    # Original accepted candidates retain their exact policy-2 metadata/digests.
    # Derive advisory evidence for the effective policy-3 evaluator input.
    return vp.numeric_advisories(dict(unit,source_text=unit.get('source_text',unit.get('text'))),candidate,
                                 policy_version=candidate.get('validation',{}).get('policy_version',3))


def read_validated_candidates(path, source_path):
    """Single authoritative reader for all downstream primary consumers."""
    state = read_primary_state(path,source_path)
    if _file_hash(source_path)!=state['compatibility']['source_sha256']:
        raise ValueError('Primary source hash mismatch.')
    return state,{k:deepcopy(r['candidate']) for k,r in state['unit_results'].items() if r['status']=='validated'}


def _primary_result(state, path, submitted):
    units = deepcopy(state['units'])
    for unit in units:
        record = state['unit_results'].get(unit['unit_key'])
        if record is None:continue
        candidate = record['candidate'] or {}
        unit.update(primary_translation=candidate.get('primary_translation'),
                    primary_span_translations=deepcopy(candidate.get('primary_span_translations')),
                    primary_translation_model=state['compatibility']['model'],primary_error=record['error'],
                    primary_attempt_count=record['attempt_count'],primary_history=list(record['request_ids']))
    groups = {s:[k for k,r in state['unit_results'].items() if r['status']==s]
              for s in ('validated','pending','retry_pending','exhausted','running')}
    return dict(status=state['status'],units=units,checkpoint_path=str(Path(path).resolve()),
                completed_unit_keys=groups['validated'],pending_unit_keys=groups['pending'],
                retry_pending_unit_keys=groups['retry_pending']+groups['running'],
                exhausted_unit_keys=groups['exhausted'],error_unit_keys=groups['retry_pending']+groups['exhausted'],
                submitted_batch_indices=submitted,batch_results=deepcopy(state['batches']))


def inspect_primary_checkpoint(path):
    return _primary_result(read_primary_state(path),path,[])


def _finish_response(state, attempt):
    try:
        parsed = parse_primary_response(attempt['raw_response'])
    except (ValueError,TypeError) as exc:
        attempt.update(status='error',error=f'{type(exc).__name__}: {exc}',error_kind='parse')
    else:
        checked = validate_primary_response(parsed,attempt['request'],policy_version=state['compatibility']['policy_version'])
        attempt.update(response_json=parsed,validation=checked,status='success' if checked['ok'] else 'partial',
                       error='; '.join(checked['errors']) if not checked['ok'] else None,
                       error_kind='validation' if not checked['ok'] else None)
    attempt['finished_at']=_now()
    _apply_attempt(state['unit_results'],attempt,state['compatibility'])
    _refresh(state)


def run_primary_translation(client, units, source_path, source_language, target_language,
                            model_name, system_instruction, checkpoint_path, *, resume=False,
                            max_units=80, max_chars=12000, temperature=0.2, max_batches=None,
                            domain_context=None):
    """Per-paragraph completion, continuing errors, and at most three submissions/unit.

    max_batches caps actual requests including retries. Resume never resets budgets.
    Source/configuration/persistence failures propagate; provider/validation errors
    remain durable unit outcomes. Hidden SDK retries are disabled per request.
    """
    if max_batches is not None and (type(max_batches) is not int or max_batches<0):
        raise ValueError('max_batches must be a nonnegative integer or None.')
    if not all(isinstance(v,str) and v.strip() for v in (source_language,target_language,model_name,system_instruction)):
        raise ValueError('Languages, model and instruction are required.')
    if domain_context is not None and not isinstance(domain_context,str):
        raise ValueError('Domain context must be a string or None.')
    if domain_context and domain_context.strip():
        # The effective instruction is already bound into compatibility and
        # request hashes. Empty context keeps historical identities unchanged.
        system_instruction = (system_instruction.strip()+'\n\nSUPPLEMENTARY DOMAIN CONTEXT:\n'
                              +domain_context.strip())
    if type(temperature) not in (int,float) or not math.isfinite(temperature):raise ValueError('Temperature must be finite.')
    source,path = Path(source_path).resolve(),Path(checkpoint_path).resolve()
    if source.suffix.lower()!='.pptx' or source.name.startswith('~$') or path==source or path.suffix.lower()!='.json':
        raise ValueError('Select separate source PPTX and JSON checkpoint paths.')
    original = deepcopy(list(units))
    if resume:
        selected=read_primary_state(path,source)
        if selected.get('offline_recovery'):
            c=selected['compatibility']
            expected=dict(source_path=str(source),source_sha256=_file_hash(source),source_language=source_language,
                target_language=target_language,model=model_name,instruction=system_instruction.strip(),
                generation=dict(response_mime_type='application/json',temperature=temperature,http_options={'retry_options':{'attempts':1}}),
                units_sha256=_digest(original),max_units=max_units,max_chars=max_chars,max_attempts=MAX_ATTEMPTS)
            if original!=selected['units'] or any(c[k]!=v for k,v in expected.items()):
                raise ValueError('Recovered primary source/settings mismatch.')
            if any(r['status']!='validated' for r in selected['unit_results'].values()):
                raise ValueError('Recovered primary view is immutable; unfinished work needs an explicit original branch.')
            return _primary_result(selected,path,[])
    policy_version=_read_checkpoint(path)['compatibility']['policy_version'] if resume else POLICY_VERSION
    payload = build_primary_payload(original,source_language,target_language,policy_version=policy_version)
    plan = batch_primary_payload(payload,max_units,max_chars)
    compatibility = dict(source_path=str(source),source_sha256=_file_hash(source),source_language=source_language,
        target_language=target_language,model=model_name,instruction=system_instruction.strip(),
        generation=dict(response_mime_type='application/json',temperature=temperature,http_options={'retry_options':{'attempts':1}}),
        units_sha256=_digest(original),max_units=max_units,max_chars=max_chars,max_attempts=MAX_ATTEMPTS,policy_version=policy_version)
    def unchanged():
        if _file_hash(source)!=compatibility['source_sha256']:raise ValueError('Fatal: source changed during primary translation.')
    path.parent.mkdir(parents=True,exist_ok=True)
    lock=path.with_name(path.name+'.lock')
    with lock.open('x',encoding='utf-8') as f:f.write(f'pid={os.getpid()} started={_now()}\n')
    try:
        if resume:
            state=_read_checkpoint(path)
            if state['compatibility']!=compatibility or state['units']!=original:
                raise ValueError('Primary checkpoint/source/request compatibility mismatch.')
        else:
            if path.exists():raise FileExistsError(path)
            state=dict(schema_version=VERSION,stage='primary_gemini',created_at=_now(),compatibility=compatibility,
                       units=original,unit_results=_initial_results(payload),batches=[dict(batch_index=b['batch_index'],
                       unit_keys=[u['unit_key'] for u in b['units']],status='pending',attempts=[]) for b in plan])
            _refresh(state);_write_checkpoint(path,state)
        submitted=[]
        for batch in state['batches']:
            if batch['attempts'] and batch['attempts'][-1]['status'] in ('running','response_saved'):
                previous=batch['attempts'][-1]
                unchanged()
                if previous['status']=='response_saved':_finish_response(state,previous)
                else:
                    previous.update(status='interrupted',error='Previous request ended before durable response.',error_kind='interrupted',finished_at=_now())
                    _apply_attempt(state['unit_results'],previous,compatibility);_refresh(state)
                _write_checkpoint(path,state)
            while True:
                keys=[k for k in batch['unit_keys'] if state['unit_results'][k]['status']!='validated'
                      and state['unit_results'][k]['attempt_count']<MAX_ATTEMPTS]
                if not keys or (max_batches is not None and len(submitted)>=max_batches):break
                unchanged()
                if not callable(getattr(getattr(client,'models',None),'generate_content',None)):
                    raise ValueError('A Gemini client with callable models.generate_content is required.')
                request,prompt,request_hash=_request(state,batch,keys,state['unit_results'])
                number=len(batch['attempts'])+1
                attempt=dict(attempt=number,request_id=f"b{batch['batch_index']}-a{number}",unit_keys=keys,
                             request=request,request_hash=request_hash,status='running',started_at=_now(),
                             raw_response=None,response_json=None,validation=None,error=None,error_kind=None)
                batch['attempts'].append(attempt)
                for key in keys:
                    r=state['unit_results'][key];r['attempt_count']+=1;r['retry_count']=max(0,r['attempt_count']-1);r['request_ids'].append(attempt['request_id']);r['status']='running'
                _refresh(state);_write_checkpoint(path,state)
                submitted.append(batch['batch_index'])
                print(f"Primary batch {batch['batch_index']}/{len(plan)} | units={len(keys)} | request={number}")
                try:
                    response=client.models.generate_content(model=model_name,contents=prompt,config=deepcopy(compatibility['generation']))
                    raw_response=getattr(response,'text','') or ''
                except BaseException as exc:
                    attempt.update(status='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'error',
                                   error=f'{type(exc).__name__}: {exc}',
                                   error_kind='configuration' if isinstance(exc,(TypeError,AttributeError)) else 'provider',finished_at=_now())
                    _apply_attempt(state['unit_results'],attempt,compatibility);_refresh(state);_write_checkpoint(path,state)
                    if not isinstance(exc,Exception) or isinstance(exc,(TypeError,AttributeError)):raise
                    unchanged()
                    continue
                # Persistence and source integrity checks are OUTSIDE provider recovery.
                attempt.update(raw_response=raw_response,status='response_saved')
                _write_checkpoint(path,state)
                unchanged()
                _finish_response(state,attempt)
                _write_checkpoint(path,state)
        unchanged()
        return _primary_result(state,path,submitted)
    finally:
        lock.unlink()
