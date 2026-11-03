"""Derived, offline reporting. Reads saved records; never runs a workflow stage."""
import argparse
from collections import Counter
from contextlib import nullcontext
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
from urllib.parse import quote

from . import primary_translation as pt
from . import artifact_paths
from .active_run import STAGES, DEPENDENCIES

COUNTS=('accepted_primary','accepted_fallback','preserved','pending','failed','recovery_eligible','manual_followup')
LABELS=dict(primary='Gemini',evaluation='OpenAI primary evaluation',fallback='Claude',
            fallback_evaluation='OpenAI Claude evaluation',finalization='Finalization',reconstruction='Reconstruction')


def read_record(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity=data.pop('integrity',None)
    if integrity!=pt._digest(data):raise ValueError(f'Invalid saved record integrity: {path}')
    return data


def _linked(root, recorded):
    """Resolve an exact run-local record, including Windows/WSL saved paths."""
    parts=PureWindowsPath(recorded).parts if '\\' in recorded else Path(recorded).parts
    path=Path(recorded)
    if not path.is_file():
        if root.name not in parts:raise ValueError(f'Record is outside the run: {recorded}')
        path=root.joinpath(*parts[parts.index(root.name)+1:])
    path=path.resolve()
    if root not in path.parents:raise ValueError('Report input is outside the selected run.')
    return path


def _decisions(state):
    return {r['unit_key']:r for b in (state or {}).get('batches',[]) for r in b.get('evaluations',[])}


def unit_counts(records):
    """Use finalized accounting when present; otherwise derive provisional coverage."""
    if 'finalization' in records:return dict(records['finalization']['summary']['counts'])
    primary=records.get('primary')
    if primary is None:return None
    decisions=_decisions(records.get('evaluation'));fallback_decisions=_decisions(records.get('fallback_evaluation'))
    evaluation_batches={k:b for b in records.get('evaluation',{}).get('batches',[]) for k in b['unit_keys']}
    fallback_batches={k:b for b in records.get('fallback',{}).get('batches',[]) for k in b['unit_keys']}
    fallback_eval_batches={k:b for b in records.get('fallback_evaluation',{}).get('batches',[]) for k in b['unit_keys']}
    fallback_candidates={r['unit_key'] for b in records.get('fallback',{}).get('batches',[]) for r in b.get('candidates',[])}
    fallback_units=records.get('fallback',{}).get('unit_results',{})
    counts=Counter({s:0 for s in COUNTS})
    for unit in primary['units']:
        key=unit['unit_key'];generation=primary['unit_results'].get(key,{})
        if unit.get('action')!='translate' or unit.get('preserve_source'):status='preserved'
        elif generation.get('status')!='validated':status='failed' if generation.get('status')=='exhausted' else 'pending'
        elif key not in decisions:status='failed' if evaluation_batches.get(key,{}).get('status')=='error' else 'pending'
        elif decisions[key]['passed']:status='accepted_primary'
        elif key not in fallback_batches:status='recovery_eligible'
        elif key not in fallback_candidates:status='failed' if fallback_units.get(key,{}).get('status') in ('invalid','missing','provider_error') or fallback_batches[key]['status']=='error' else 'pending'
        elif key not in fallback_decisions:status='failed' if fallback_eval_batches.get(key,{}).get('status')=='error' else 'pending'
        else:status='accepted_fallback' if fallback_decisions[key]['passed'] else 'recovery_eligible'
        counts[status]+=1
    return dict(counts)


def stage_metrics(state):
    batches=state.get('historical_batches',[])+state.get('batches',[]);attempts=[a for b in batches for a in b['attempts']]
    decisions=list(_decisions(state).values())
    if 'unit_results' in state:
        groups=Counter(r['status'] for r in state['unit_results'].values())
        completed=groups['validated'];failed=sum(groups[s] for s in ('exhausted','invalid','missing','provider_error'))
        pending=sum(groups[s] for s in ('pending','retry_pending','running'))
    else:
        completed=sum(len(b.get('candidates',b.get('evaluations',[]))) for b in batches if b['status']=='completed')
        failed=sum(len(b['unit_keys']) for b in batches if b['status']=='error')
        pending=sum(len(b['unit_keys']) for b in batches if b['status'] not in ('completed','error'))
    return dict(status=state['status'],model=state.get('compatibility',{}).get('model','—'),
                submissions=len(attempts),retries=sum(max(0,len(b['attempts'])-1) for b in batches),
                operational_failures=sum(a['status'] in ('error','interrupted','partial') for a in attempts),
                running=sum(a['status'] in ('running','response_saved') for a in attempts),
                completed=completed,failed=failed,pending=pending,
                accepted=sum(r['passed'] for r in decisions),rejected=sum(not r['passed'] for r in decisions))


def load_run(manifest_path):
    """Read a consistent, hash-bound saved lineage without clients or extraction."""
    manifest_path=Path(manifest_path).resolve();root=manifest_path.parent
    hashes={manifest_path:pt._file_hash(manifest_path)};manifest=read_record(manifest_path)
    if manifest.get('stage')!='pptx_active_run' or manifest.get('schema_version')!=1:raise ValueError('Invalid run manifest.')
    source=artifact_paths.resolve_input(dict(path=manifest['identity']['source_path'],sha256=manifest['identity']['source_sha256']),
                                       module_file=__file__,path_factory=Path,hash_function=pt._file_hash)
    hashes[source]=manifest['identity']['source_sha256']
    records={};paths={};stages=manifest['stages']
    if set(stages)-set(STAGES):raise ValueError('Unknown saved stage.')
    for stage in STAGES:
        if stage not in stages:continue
        entry=stages[stage];deps=list(DEPENDENCIES[stage])
        if stage=='finalization':deps += [s for s in ('fallback','fallback_evaluation') if s in stages]
        if any(s not in stages for s in deps) or entry['upstream']!={s:stages[s]['sha256'] for s in deps}:
            raise ValueError('Saved manifest ancestry mismatch.')
        path=_linked(root,entry['path']);digest=pt._file_hash(path)
        if digest!=entry['sha256']:raise ValueError(f'Saved {stage} changed; register/sync the run before reporting.')
        hashes[path]=digest;paths[stage]=path;records[stage]=read_record(path)
        if stage=='primary' and records[stage].get('stage')=='primary_offline_recovery':
            records[stage]=pt.read_primary_state(path,source)
            from .primary_recovery import _base
            original=_base(records[stage]['offline_recovery']['original_checkpoint'])
            hashes[original]=records[stage]['offline_recovery']['original_checkpoint']['sha256']
            paths['original_primary']=original
        if stage=='fallback' and (records[stage].get('stage')=='claude_offline_recovery' or records[stage].get('offline_recovery_parent')):
            from . import fallback_translation as ft
            records[stage]=ft._read(path)
            from .fallback_recovery import resolve
            recovery=records[stage]['fallback_offline_recovery']
            for name,ref in [('original_fallback',recovery['original_checkpoint']),('fallback_recovery',dict(path=recovery['artifact_path'],sha256=recovery['artifact_sha256']))]:
                linked=resolve(ref);hashes[linked]=ref['sha256'];paths[name]=linked
        if records[stage].get('compatibility',{})!=entry['settings']:raise ValueError('Saved checkpoint settings mismatch.')
    final=records.get('finalization')
    if final:
        ancestry=final['ancestry'];names=dict(primary='primary',evaluation='primary_evaluation',fallback='fallback',fallback_evaluation='fallback_evaluation')
        if ancestry['source']['sha256']!=manifest['identity']['source_sha256']:raise ValueError('Finalization source mismatch.')
        for stage,name in names.items():
            if stage in stages and (name not in ancestry or ancestry[name]['sha256']!=stages[stage]['sha256']):
                raise ValueError('Finalization ancestry mismatch.')
    reconstruction=records.get('reconstruction')
    if reconstruction:
        if reconstruction['finalization_snapshot']['sha256']!=stages['finalization']['sha256']:raise ValueError('Reconstruction ancestry mismatch.')
        output=_linked(root,reconstruction['output']);hashes[output]=pt._file_hash(output)
        if hashes[output]!=reconstruction['output_sha256']:raise ValueError('Validated deliverable changed.')
        paths['output']=output
    # Latest attempt is explicitly linked to this finalization, never chosen by mtime.
    latest=None
    if final and manifest.get('artifact_layout')==1:
        token=pt._digest({'finalization':stages['finalization']['sha256']})[:20]
        journal_path=root/'checkpoints'/f'reconstruction_publication_{token}.json'
        if journal_path.exists():
            hashes[journal_path]=pt._file_hash(journal_path);journal=read_record(journal_path)
            if journal['upstream']!={'finalization':stages['finalization']['sha256']}:raise ValueError('Attempt lineage mismatch.')
            path=_linked(root,journal['report_path'])
            if path.is_file():
                hashes[path]=pt._file_hash(path);latest=read_record(path);paths['latest_attempt']=path
                if latest['finalization_snapshot']['sha256']!=stages['finalization']['sha256']:raise ValueError('Attempt report lineage mismatch.')
            else:latest=dict(status='interrupted or running',failures=['No completed validation report saved.'])
    for path,digest in hashes.items():
        if pt._file_hash(path)!=digest:raise ValueError('Saved records changed during reporting; retry.')
    return dict(manifest=manifest,records=records,paths=paths,latest=latest,hashes=hashes,root=root)


def _text(value, limit=None):
    value=' '.join(str(value).split()).replace('|','\\|').replace('<','&lt;').replace('>','&gt;')
    return value if limit is None or len(value)<=limit else value[:limit]+'…'


def _link(root,path,label):
    relative=Path(path).relative_to(root).as_posix()
    return f'[{label}]({quote(relative,safe="/._-")})'


def _final_reports(data):
    path=data['paths'].get('finalization')
    if path is None:return {}
    if data['manifest'].get('artifact_layout')==1:
        token=path.stem[len('finalization_'):]
        return dict(CSV=data['root']/'reports'/f'unit_summary_{token}.csv',
                    manual=data['root']/'reports'/f'manual_followup_{token}.md')
    return dict(CSV=path.parent/'summary.csv',manual=path.parent/'manual_followup.md')


def render_summary(manifest_path):
    data=load_run(manifest_path);m=data['manifest'];records=data['records'];root=data['root'];paths=data['paths']
    identity=m['identity'];source=PureWindowsPath(identity['source_path']).name
    counts=unit_counts(records);final=records.get('finalization');reconstruction=records.get('reconstruction')
    complete=counts is not None and not any(counts[s] for s in ('pending','failed','recovery_eligible','manual_followup'))
    translation='Complete' if complete else 'Incomplete' if counts is not None else 'Not started; unit counts not yet saved'
    structural=reconstruction['status'] if reconstruction else 'Not registered'
    lines=['# Run summary','',f'**{_text(source)}** · {_text(identity["source_language"])} → {_text(identity["target_language"])}',
           f'Run: `{m["run_id"]}` · Created: {m["created_at"]} (UTC)','',
           f'- Translation: **{translation}**'+(' (provisional; not finalized)' if not final and counts is not None else ''),
           f'- Structural reconstruction: **{structural}** (saved validation; output hash checked)' if reconstruction else f'- Structural reconstruction: **{structural}**',
           '- Human visual review: **Not recorded**; inspect in PowerPoint.','']
    if reconstruction and reconstruction['status']=='passed' and not reconstruction['failures']:
        version=reconstruction.get('deliverable_version');label=f'v{version:03d}' if version else 'historical output (unversioned)'
        lines += [f'**Current validated deliverable — {label}: '+_link(root,paths['output'],'Open translated PPTX')+'**',
                  'Release coverage: '+('complete translation.' if complete else '**partial translation; unresolved source text remains.**'),'']
    else:lines += ['**No registered passing deliverable.**','']
    if counts is not None:
        lines += ['| Total | Accepted Gemini | Accepted Claude | Preserved | Pending | Failed | Recovery eligible | Manual follow-up |',
                  '|---:|---:|---:|---:|---:|---:|---:|---:|',
                  '| '+' | '.join(str(n) for n in [sum(counts.values()),*[counts[s] for s in COUNTS]])+' |','']
    recovery=records.get('primary',{}).get('offline_recovery')
    if recovery:
        metrics=stage_metrics(records['primary']);events=recovery['events']
        warnings=sum(len(e['candidate']['validation']['warnings']) for e in events)
        lines += [f'Primary candidates: **{metrics["completed"]} structurally eligible**; '
                  f'{len(events)} recovered by offline policy-3 revalidation ({warnings} numeric advisories). '
                  'No new provider submissions; historical failures and attempt counts retained. '
                  'Structural eligibility is not semantic acceptance.','']
    claude_recovery=records.get('fallback',{}).get('fallback_offline_recovery')
    if claude_recovery:
        metrics=stage_metrics(records['fallback'])
        lines += [f'Claude: **{metrics["completed"]} structurally eligible; {metrics["pending"]} pending**. '
                  f'{len(claude_recovery["events"])} saved candidates recovered by offline revalidation; '
                  'the historical failed batch is retained unchanged. Recovery adds no provider submissions '
                  'or retries and does not imply semantic acceptance.','']
    lines += ['| Stage | Model | Status | Submissions | Retries | Operational failures | Accepted / rejected |',
              '|---|---|---|---:|---:|---:|---|']
    for stage in ('primary','evaluation','fallback','fallback_evaluation'):
        if stage not in records:
            lines.append(f'| {LABELS[stage]} | — | Not used / not started | 0 | 0 | 0 | — |');continue
        metrics=stage_metrics(records[stage]);decisions=f'{metrics["accepted"]} / {metrics["rejected"]}' if 'evaluation' in stage else '—'
        lines.append('| '+' | '.join(_text(x) for x in (LABELS[stage],metrics['model'],metrics['status'],metrics['submissions'],metrics['retries'],metrics['operational_failures'],decisions))+' |')
    lines += ['', 'Submissions count saved attempts, including interrupted/in-flight attempts; SDK retries may add network requests.',
              f'Finalization: **{final["status"] if final else "Not exported"}**.']
    if reconstruction:
        checks=reconstruction.get('validation',{}).get('span_checks',[])
        lines += [f'Reconstruction: {reconstruction["reopened_slide_count"]} slides; {reconstruction["applied_translations"]} applied translations; '
                  f'span checks {sum(c["passed"] for c in checks)}/{len(checks)} passed; {len(reconstruction["failures"])} validation failures; '
                  f'{len(reconstruction["manual_review"])} reconstruction review cases.']
        lines += [f'- {_text(error,240)}' for error in reconstruction['failures'][:3]]
    latest=data['latest']
    if latest and (paths.get('latest_attempt')!=paths.get('reconstruction')):
        lines += [f'Latest reconstruction attempt: **{latest["status"]}**; '+
                  ('awaiting registration.' if latest['status']=='passed' else 'not a successful release.')]
        lines += [f'- {_text(e,240)}' for e in latest.get('failures',[])[:3]]
    manual=[r for r in (final or {}).get('units',[]) if r['status'] in ('manual_followup','failed','recovery_eligible')]
    if manual:
        lines += ['',f'Follow-up: **{len(manual)} units**; see the manual/recovery report for full histories.']
        lines += [f'- `{r["unit_key"]}` (slide {r["source_unit"].get("slide_number","?")}): {_text(r["reason"],180)}' for r in manual[:3]]
    else:lines += ['', 'Manual/recovery follow-up: '+('none.' if complete else 'see pending work and stage checkpoints; no completed release implied.')]
    links=[_link(root,root/'run.json','Manifest')]
    for stage,path in paths.items():
        if stage=='output':continue
        links.append(_link(root,path,LABELS.get(stage,'Original Gemini history' if stage=='original_primary' else 'Original Claude history' if stage=='original_fallback' else 'Claude recovery' if stage=='fallback_recovery' else 'Latest attempt validation')))
    for label,path in _final_reports(data).items():
        if path.is_file():links.append(_link(root,path,'Unit CSV' if label=='CSV' else 'Manual/recovery report'))
    lines += ['', 'Details: '+' · '.join(links), '', f'Derived from manifest SHA-256 `{data["hashes"][Path(manifest_path).resolve()]}`.','']
    # Verify again after rendering; callers can retry a concurrent stage update.
    for path,digest in data['hashes'].items():
        if pt._file_hash(path)!=digest:raise ValueError('Saved records changed during reporting; retry.')
    return '\n'.join(lines)


class _ManifestLock:
    def __init__(self,path):self.path=Path(path).with_name('run.json.lock')
    def __enter__(self):
        with self.path.open('x',encoding='utf-8') as stream:stream.write('offline summary writer\n')
    def __exit__(self,*args):self.path.unlink()


def _atomic_markdown(path,text):
    descriptor,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(text);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


def write_summary(manifest_path, *, _manifest_locked=False):
    """Explicit offline export; the Markdown is the only artifact changed."""
    path=Path(manifest_path).resolve()
    with nullcontext() if _manifest_locked else _ManifestLock(path):
        text=render_summary(path);output=path.parent/'RUN_SUMMARY.md'
        if not output.exists() or output.read_text(encoding='utf-8')!=text:_atomic_markdown(output,text)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--write',action='store_true',help='Write only RUN_SUMMARY.md; default is read-only preview.')
    args=parser.parse_args()
    if args.write:print(write_summary(args.manifest))
    else:print(render_summary(args.manifest),end='')





def stage_cases(state, *, primary=False):
    """Small exception messages; raw responses remain available only on demand."""
    cases=[]
    for batch in state.get('batches',[]):
        for attempt in batch['attempts']:
            if attempt.get('error'):
                label=''
                if primary:
                    unresolved=any(state['unit_results'][key]['status']!='validated' for key in attempt['unit_keys'])
                    current=attempt is batch['attempts'][-1] and unresolved
                    label=('Current submission error' if current else 'Historical submission error')+' | '
                cases.append(f"{label}Batch {batch['batch_index']} {attempt['status']}: {attempt['error']}")
        for decision in batch.get('evaluations',[]):
            if not decision['passed']:cases.append(f"{decision['unit_key']} REJECTED | {decision['feedback']}")
    return cases


def print_cases(cases, limit=5):
    for case in cases[:limit]:
        message=' '.join(str(case).split())
        print(' -',message if len(message)<=300 else message[:300]+'…')
    if len(cases)>limit:print(f' - {len(cases)-limit} more cases; inspect the saved checkpoint/report.')


def print_stage_summary(run, stage):
    """Read-only notebook display, including saved progress when execution is off."""
    if run is None:return
    path=run.path(stage)
    if path is None:
        print(f'{LABELS[stage]} | run {run.state["run_id"]} | not registered')
        return
    if stage=='primary':state=pt.read_primary_state(path)
    elif stage=='fallback':
        from . import fallback_translation as ft
        state=ft._read(path)
    else:state=read_record(path)
    if stage=='finalization':
        print_finalization_summary(state,run)
    elif stage=='reconstruction':
        print_reconstruction_summary(dict(report=state,report_path=str(path),output_path=state['output']),run)
    else:
        metrics=stage_metrics(state)
        print(f'{LABELS[stage]} | run {run.state["run_id"]} | {metrics["status"]} | model {metrics["model"]}')
        print(f'Completed: {metrics["completed"]} | failed: {metrics["failed"]} | pending: {metrics["pending"]}'
              +(f' | accepted: {metrics["accepted"]} | rejected: {metrics["rejected"]}' if 'evaluation' in stage else ''))
        print(f'Recorded submissions: {metrics["submissions"]} | retries: {metrics["retries"]} | operational failures: {metrics["operational_failures"]}'
              +(f' | in-flight: {metrics["running"]}' if metrics['running'] else ''))
        if state.get('fallback_offline_recovery'):
            print(f'Offline Claude revalidations: {len(state["fallback_offline_recovery"]["events"])}; historical failed response retained; no semantic acceptance implied')
        if stage=='fallback' and state.get('schema_version')==2:
            groups=Counter(r['status'] for r in state['unit_results'].values())
            print(f'Invalid: {groups["invalid"]} | missing: {groups["missing"]} | provider failures: {groups["provider_error"]}')
        if stage=='primary':
            groups=Counter(r['status'] for r in state['unit_results'].values())
            print(f'Retry-pending: {groups["retry_pending"]+groups["running"]} | exhausted: {groups["exhausted"]}')
            attempts=[a for b in state['batches'] for a in b['attempts']]
            if attempts:
                latest=max(attempts,key=lambda a:a['started_at'])
                print(f'Latest submission {latest["request_id"]}: {latest["status"]}')
        if state.get('offline_recovery'):
            print(f'Offline revalidated: {len(state["offline_recovery"]["events"])} | structural eligibility only; historical failures below remain recorded')
        print_cases(stage_cases(state,primary=stage=='primary'))
    print('Checkpoint/report:',path)


def print_finalization_summary(snapshot, run=None):
    counts=snapshot['summary']['counts']
    run_id=getattr(run,'state',{}).get('run_id','unselected')
    print(f'Finalization | run {run_id} | {snapshot["status"]}')
    print('Total:',snapshot['summary']['total_units'],'|', ' | '.join(f'{name}: {counts[name]}' for name in COUNTS))
    print_cases([f"{r['unit_key']}: {r['reason']}" for r in snapshot['units'] if r['status'] in ('failed','recovery_eligible','manual_followup')])


def print_reconstruction_summary(result, run=None):
    report=result['report'];run_id=getattr(run,'state',{}).get('run_id','unselected')
    checks=report.get('validation',{}).get('span_checks',[])
    print(f'Reconstruction | run {run_id} | coverage {report.get("translation_coverage","unknown")}')
    print('PPTX:',result['output_path']);print('Report:',result['report_path'])
    print('Validation:',report['status'],'| slides:',report.get('reopened_slide_count','?'),
          '| applied:',report['applied_translations'],'| unchanged non-targets:',report['verified_non_target_units'],
          '| span checks:',f'{sum(c["passed"] for c in checks)}/{len(checks)}',
          '| failures:',len(report['failures']),'| manual review:',len(report['manual_review']))
    print_cases(['ERROR: '+error for error in report['failures']]+[str(case) for case in report['manual_review']])
    print('Human visual review: not recorded; inspect wrapping, clipping and fonts in PowerPoint.')


def inspect_unit(manifest_path, unit_key):
    """On-demand complete saved detail for one unit, without modifying any record."""
    data=load_run(manifest_path);result={}
    for stage,state in data['records'].items():
        if stage=='reconstruction':continue
        units=state.get('units',state.get('payload',{}).get('units',[]))
        unit=next((u for u in units if u['unit_key']==unit_key),None)
        batches=[b for b in state.get('batches',[]) if unit_key in b['unit_keys']]
        if unit is not None or batches:
            result[stage]=dict(unit=unit,batches=batches)
            if 'unit_results' in state:result[stage]['result']=state['unit_results'].get(unit_key)
    if not result:raise KeyError(unit_key)
    return result


if __name__=='__main__':main()
