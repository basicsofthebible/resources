"""Stage 3E candidate adapter for the unchanged OpenAI evaluator policy.

No SDK imports or upstream writes. Decisions identify exact Claude paragraph/span
pairs. This stage does not generate corrections, finalize, or reconstruct.
"""
from copy import deepcopy
from pathlib import Path, PureWindowsPath
import json
from . import artifact_paths
from . import primary_translation as pt
from . import fallback_translation as ft
from . import fallback_checkpoint as fc
from . import evaluation as ev

VERSION = 1
STAGE = 'openai_fallback_evaluation'
# Same failure semantics and exception handling as primary evaluation.
EvaluationStageError = ev.EvaluationStageError


def resolve_evaluation_model(fallback_checkpoint, model_name=None):
    """Default to the exact upstream evaluator snapshot; explicit model overrides it."""
    if model_name is not None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError('Provide a nonempty model override, or None to inherit the primary evaluator.')
        return model_name.strip()
    fallback = Path(fallback_checkpoint).resolve()
    state = ft._read(fallback)
    recorded = state.get('provenance', {}).get('evaluation_checkpoint')
    if not recorded:
        raise ValueError('Fallback has no primary evaluation checkpoint link; set a model override.')
    linked = PureWindowsPath(recorded) if '\\' in recorded else Path(recorded)
    candidates = [Path(recorded)]
    # Preserve recorded project-relative links when moving between Windows/WSL.
    # Never discover a different evaluation by filename or modification time.
    if 'outputs' in linked.parts:
        relative = linked.parts[linked.parts.index('outputs'):]
        candidates.append(artifact_paths.project_root(__file__, path_factory=Path).joinpath(*relative))
    if not linked.is_absolute():
        candidates.extend([fallback.parent / recorded, artifact_paths.project_root(__file__, path_factory=Path) / recorded])
    for candidate in candidates:
        if candidate.is_file() and pt._file_hash(candidate) == state['compatibility']['evaluation_sha256']:
            evaluator = ev._read(candidate)
            if (evaluator['compatibility']['primary_sha256'] != state['compatibility']['primary_sha256']
                    or evaluator['compatibility']['source_sha256'] != state['compatibility']['source_sha256']):
                raise ValueError('Linked primary evaluator does not match the fallback provenance.')
            model = evaluator['compatibility']['model']
            if not isinstance(model, str) or not model.strip():
                raise ValueError('Linked primary evaluation has no model name.')
            return model
    raise ValueError('Cannot read the exact linked primary evaluation checkpoint; restore it or set a model override.')


def build_fallback_evaluation_payload(fallback_checkpoint, source_path):
    """Revalidate saved Claude responses, preserving complete paragraph context."""
    state = ft._read(Path(fallback_checkpoint))
    compatibility, payload = state['compatibility'], state['payload']
    if pt._file_hash(Path(source_path)) != compatibility['source_sha256']:
        raise ValueError('Fallback source hash mismatch.')
    if pt._digest(payload) != compatibility['payload_sha256']:
        raise ValueError('Fallback input payload hash mismatch.')
    requested = {u['unit_key']:u for u in payload['units']}
    if len(requested) != len(payload['units']):
        raise ValueError('Duplicate fallback input unit IDs.')
    plan = pt.batch_primary_payload(payload, compatibility['max_units'], compatibility['max_chars'])
    if len(plan) != len(state['batches']):
        raise ValueError('Incomplete fallback batch plan.')
    rows, pending, errors = [], [], []
    for expected, batch in zip(plan, state['batches']):
        request = dict(metadata=dict(payload['metadata'], unit_count=expected['unit_count']), units=expected['units'])
        user = 'Correct the rejected PPTX translations. Return only the required JSON.\n\n'+json.dumps(request,ensure_ascii=False,indent=2)
        if (batch['batch_index'] != expected['batch_index']
                or batch['unit_keys'] != [u['unit_key'] for u in expected['units']]
                or batch['request_hash'] != (pt._digest(user) if state['schema_version']==1 else fc.request_hash(user,compatibility))):
            raise ValueError('Fallback batch/request mismatch.')
        if state['schema_version']==2:
            for key in batch['unit_keys']:
                outcome=state['unit_results'][key]['status']
                if outcome!='validated':(pending if outcome in ('pending','running') else errors).append(key)
        elif batch['status'] != 'completed':
            (errors if batch['status']=='error' else pending).extend(batch['unit_keys'])
            continue
        # ft._read checks raw response -> structural validation -> stored candidate
        # equality, including assembled text, ordered spans and candidate digest.
        for candidate in batch['candidates']:
            unit = requested[candidate['unit_key']]
            row = {k:deepcopy(unit[k]) for k in ('unit_key','source_text','slide_number',
                   'source_type','text_kind','number_prefix','number_suffix','translation_note')}
            # Existing evaluator contract calls the candidate primary_translation.
            # Explicit provenance below distinguishes Claude without changing policy.
            row.update(primary_translation=candidate['fallback_translation'],
                       candidate_source='fallback_translation', candidate_sha256=candidate['candidate_sha256'])
            if 'styled_spans' in unit:
                row['styled_spans'] = deepcopy(unit['styled_spans'])
                row['translated_spans'] = deepcopy(candidate['fallback_span_translations'])
            if pt.vp.payload_policy(payload,default=pt.vp.LEGACY)>=3:
                row['validation_warnings']=deepcopy(candidate['validation']['warnings'])
            if state['schema_version']==2:row['generation_provenance']=deepcopy(candidate['provenance'])
            if state.get('fallback_offline_recovery'):row['validation_policy_version']=candidate['validation']['policy_version']
            rows.append(row)
    evaluation_payload=dict(metadata=dict(payload_type='pptx_fallback_translation_evaluation',
                              source_language=payload['metadata']['source_language'],
                              target_language=payload['metadata']['target_language'],unit_count=len(rows),
                              candidate_source='fallback_translation',
                              fallback_pending_unit_keys=pending, fallback_error_unit_keys=errors),
                units=rows,
                provenance=dict(fallback_checkpoint=str(Path(fallback_checkpoint).resolve()),
                                fallback_model=compatibility['model'],
                                generation=deepcopy(state.get('provenance')),
                                diagnostic=deepcopy(payload.get('diagnostic'))))
    policy_version=pt.vp.payload_policy(payload,default=pt.vp.LEGACY)
    if policy_version>=3:evaluation_payload['metadata']['validation_policy_version']=policy_version
    return evaluation_payload


def _read(path):
    state = ev._read(path, STAGE)
    if state['compatibility']['payload_sha256'] != pt._digest(state['payload']):
        raise ValueError('Fallback evaluation payload hash mismatch.')
    keys = [u['unit_key'] for u in state['payload']['units']]
    if len(keys) != len(set(keys)) or [k for b in state['batches'] for k in b['unit_keys']] != keys:
        raise ValueError('Fallback evaluation candidate plan mismatch.')
    for unit in state['payload']['units']:
        candidate = dict(unit_key=unit['unit_key'], fallback_translation=unit['primary_translation'])
        if 'translated_spans' in unit:
            candidate['fallback_span_translations'] = unit['translated_spans']
        if unit['candidate_sha256'] != pt._digest(candidate):
            raise ValueError('Fallback evaluation candidate digest mismatch.')
    return state


def inspect_fallback_evaluation_checkpoint(path):
    return ev._result(_read(Path(path)), Path(path).resolve())


def run_fallback_evaluation(client, fallback_checkpoint, source_path, checkpoint_path,
                            model_name=None, *, resume=False, max_batches=None):
    """Use shared policy, batching, strict validation and one attempt per invocation."""
    model_name = resolve_evaluation_model(fallback_checkpoint, model_name)
    if resume:
        _read(Path(checkpoint_path))
    return ev._run_evaluation(client, fallback_checkpoint, source_path, checkpoint_path,
                             model_name, None, resume=resume, max_batches=max_batches,
                             max_units=30, max_chars=8000, timeout=60,
                             payload_builder=build_fallback_evaluation_payload,
                             stage=STAGE, input_hash_key='fallback_sha256')
