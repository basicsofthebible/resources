"""Production artifact placement and publication; no translation policy or providers."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import unicodedata

from . import primary_translation as pt

# Common display names used by the notebook. Unknown names remain explicit,
# safe slugs; callers may supply a BCP-47 tag directly.
LANGUAGES = {'english':'en', 'spanish':'es', 'french':'fr', 'german':'de',
             'italian':'it', 'portuguese':'pt', 'simplified chinese':'zh-Hans',
             'traditional chinese':'zh-Hant', 'japanese':'ja', 'korean':'ko',
             'arabic':'ar', 'russian':'ru', 'hindi':'hi'}


def safe_component(value, limit=100):
    value=unicodedata.normalize('NFKC',value)
    value=re.sub(r'[^\w.-]+','_',value,flags=re.UNICODE).strip(' ._')[:limit].rstrip(' .')
    if not value or value in ('.','..'):value='presentation'
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}:
        value='_'+value
    return value


def language_tag(value):
    value=value.strip()
    return LANGUAGES.get(value.casefold(),safe_component(value,40))


def create_run_folder(config):
    root=config.output_root/'runs';root.mkdir(parents=True,exist_ok=True)
    naming=dict(source_stem=safe_component(config.input_path.stem),language=language_tag(config.target_language),
                started_utc=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    while True:
        short=secrets.token_hex(4)
        folder=root/f"{naming['source_stem']}__{naming['language']}__{naming['started_utc']}__{short}"
        try:folder.mkdir()
        except FileExistsError:continue
        break
    for name in ('checkpoints','reports','final'):(folder/name).mkdir()
    return folder,'run_'+short,naming


@contextmanager
def writer(run, stage):
    lock=run.manifest_path.parent/'checkpoints'/f'{stage}.lock'
    with lock.open('x',encoding='utf-8') as stream:stream.write(f'pid={os.getpid()} started={pt._now()}\n')
    try:yield
    finally:lock.unlink()


def _read(path):
    state=json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
    integrity=state.pop('integrity',None)
    if integrity!=pt._digest(state):raise ValueError('Invalid artifact publication record integrity.')
    return state


def _owned(root, path):
    path=Path(path).resolve()
    if root not in path.parents:raise ValueError('Artifact outside selected run.')
    return path


def finalize(run):
    from . import finalization as fin
    with writer(run,'finalization'):
        run.sync();upstream=run._upstream('finalization');root=run.manifest_path.parent
        token=pt._digest(upstream)[:20]
        files=dict(snapshot=str(root/'checkpoints'/f'finalization_{token}.json'),
                   summary=str(root/'reports'/f'unit_summary_{token}.csv'),
                   manual_review=str(root/'reports'/f'manual_followup_{token}.md'))
        path=run._path('finalization') or Path(files['snapshot'])
        if path.exists():
            run._validate('finalization',path)
        else:
            snapshot=fin.build_finalization_snapshot(run._path('primary'),run._path('evaluation'),run._source(),
                fallback_checkpoint=run._path('fallback'),fallback_evaluation_checkpoint=run._path('fallback_evaluation'))
            # Snapshot is the commit marker and is written last. Recover only the
            # exact owned report targets of an interrupted, uncommitted export.
            for key in ('summary','manual_review'):Path(files[key]).unlink(missing_ok=True)
            fin.write_finalization_artifacts(snapshot,root,artifact_paths=files)
        if not all(Path(p).is_file() for p in files.values()):raise ValueError('Finalization reports are missing.')
        run.record('finalization',path)
        return files


def _next_attempt(root):
    number=1
    while True:
        folder=root/'reports'/f'reconstruction__a{number:03d}'
        try:folder.mkdir()
        except FileExistsError:number+=1;continue
        return number,folder


def _save_journal(path, journal):
    pt._write_checkpoint(path.parent/f"reconstruction__{journal['attempt_id']}.json",journal)
    pt._write_checkpoint(path,journal)


def _publish(run, journal_path, journal):
    """Resume an exact publication after any interruption around the file move."""
    from . import finalized_reconstruction as fr
    root=run.manifest_path.parent
    report_path=_owned(root,journal['report_path']);report=_read(report_path)
    output=_owned(root,report['output'])
    if report['status']!='passed':return dict(output_path=str(output),report_path=str(report_path),report=report)
    if 'destination' not in journal:
        # Validate saved staging output before assigning a successful version.
        fr.validate_reconstruction_artifacts(report_path,run._path('finalization'),run._source())
        naming=run.state['naming'];stem=f"{naming['source_stem']}__{naming['language']}"
        versions=[int(m.group(1)) for p in (root/'final').glob('*.pptx')
                  if (m:=re.fullmatch(re.escape(stem)+r'__v(\d+)\.pptx',p.name))]
        version=max(versions,default=0)+1
        journal.update(destination=str(root/'final'/f'{stem}__v{version:03d}.pptx'),version=version)
        _save_journal(journal_path,journal)
    destination=_owned(root,journal['destination'])
    if destination.parent!=root/'final':raise ValueError('Invalid publication destination.')
    if destination.exists():
        if pt._file_hash(destination)!=report['output_sha256']:raise ValueError('Publication destination changed.')
    else:
        if pt._file_hash(output)!=report['output_sha256']:raise ValueError('Staged output changed.')
        # The run-level writer lock reserves this version; rename is atomic on
        # this filesystem. No previously existing destination is replaced.
        os.rename(output,destination)
    report.update(output=str(destination),artifact_layout=1,attempt_id=journal['attempt_id'],
                  deliverable_version=journal['version'])
    report['validation']['output']=str(destination)
    pt._write_checkpoint(report_path,report)
    result=fr.validate_reconstruction_artifacts(report_path,run._path('finalization'),run._source())
    run._remember_artifact('reconstruction',report_path)
    journal['status']='published';_save_journal(journal_path,journal)
    return result


def reconstruct(run, *, regenerate=False):
    from . import finalized_reconstruction as fr
    with writer(run,'reconstruction'):
        run.sync();upstream=run._upstream('reconstruction');root=run.manifest_path.parent
        token=pt._digest(upstream)[:20]
        journal_path=root/'checkpoints'/f'reconstruction_publication_{token}.json'
        journal=_read(journal_path) if journal_path.exists() else None
        if journal:
            if journal['upstream']!=upstream:raise ValueError('Publication lineage mismatch.')
            path=_owned(root,journal['report_path'])
            if path.exists():
                report=_read(path)
                # Always finish an interrupted publication, including when the
                # caller requested regeneration. A subsequent call can regenerate.
                if report['status']=='passed' and (journal['status']!='published' or not regenerate):
                    return _publish(run,journal_path,journal)
                if report['status']=='manual_review' and not regenerate:
                    return fr.validate_reconstruction_artifacts(path,run._path('finalization'),run._source())
        if not regenerate:
            path=run._path('reconstruction') or run._remembered_artifact('reconstruction')
            if path is not None:
                return fr.validate_reconstruction_artifacts(path,run._path('finalization'),run._source())
        number,folder=_next_attempt(root)
        journal=dict(schema_version=1,upstream=upstream,attempt_id=f'a{number:03d}',status='running',
                     report_path=str(folder/'reconstruction_report.json'))
        _save_journal(journal_path,journal)
        try:
            result=fr.reconstruct_finalized_snapshot(run._path('finalization'),root/'reports',run._source(),artifact_dir=folder)
        except BaseException as exc:
            # Retain any partial package and a readable diagnostic. Never publish.
            (folder/'interrupted.txt').write_text(f'{type(exc).__name__}: {exc}\n',encoding='utf-8')
            raise
        result['report']['attempt_id']=journal['attempt_id']
        pt._write_checkpoint(Path(result['report_path']),result['report'])
        if result['report']['status']=='passed':return _publish(run,journal_path,journal)
        # Failed and manual-review packages remain diagnostics, without a release version.
        journal['status']=result['report']['status'];_save_journal(journal_path,journal)
        return result


def write_qc_report(run, report):
    """Persist optional QC within its new-layout run; identical repeats reuse it."""
    if run.state.get('artifact_layout')!=1:raise ValueError('QC export requires a new-layout run.')
    if report['source_sha256']!=run.state['identity']['source_sha256']:raise ValueError('QC source mismatch.')
    path=run.manifest_path.parent/'reports'/'extraction_qc.json'
    with writer(run,'qc'):
        run.sync()
        if path.exists():
            if _read(path)!=report:raise ValueError('Saved QC differs; refusing to overwrite it.')
        else:pt._write_checkpoint(path,report)
    return path
