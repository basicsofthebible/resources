"""Explicit run selection and validated artifact handoffs. No provider execution."""
from copy import deepcopy
import json
from pathlib import Path
from . import primary_translation as pt

VERSION = 1
STAGES = ('primary','evaluation','fallback','fallback_evaluation','finalization','reconstruction')
DEPENDENCIES = dict(primary=(), evaluation=('primary',), fallback=('primary','evaluation'),
                    fallback_evaluation=('fallback',), finalization=('primary','evaluation'),
                    reconstruction=('finalization',))


def _identity(config, styled):
    return dict(source_path=str(config.input_path.resolve()),source_sha256=pt._file_hash(config.input_path),
                source_language=config.source_language,target_language=config.target_language,
                include_smartart=config.include_smartart,include_styled_spans=styled)


class ActiveRun:
    """A small artifact index; adapters retain all execution/resume policy."""
    def __init__(self, path, state):
        self.manifest_path=Path(path).resolve();self.state=state
        self._saved_hash=pt._file_hash(self.manifest_path)
        self._validated={}
        self._dirty=False

    @classmethod
    def create(cls, config, units, styled=True):
        from .artifact_layout import create_run_folder
        folder, run_id, naming = create_run_folder(config)
        path=folder/'run.json'
        state=dict(schema_version=VERSION,stage='pptx_active_run',run_id=run_id,created_at=pt._now(),artifact_layout=1,naming=naming,
                   identity=_identity(config,styled),units_sha256=pt._digest(units),stages={},history=[])
        pt._write_checkpoint(path,state)
        run=cls(path,state)
        run.refresh_summary()
        return run

    @classmethod
    def open(cls, path, config, styled=True):
        path=Path(path).expanduser().resolve()
        state=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
        integrity=state.pop('integrity',None)
        if integrity!=pt._digest(state) or state.get('schema_version')!=VERSION or state.get('stage')!='pptx_active_run':
            raise ValueError('Invalid active-run manifest integrity/version.')
        if state['identity']!=_identity(config,styled):raise ValueError('Selected run/source/language/extraction settings mismatch.')
        if set(state['stages'])-set(STAGES):raise ValueError('Unknown run stage.')
        if state.get('artifact_layout') not in (None,1):raise ValueError('Unknown artifact layout.')
        run=cls(path,state);run.sync(persist=False);return run

    def _save(self):
        lock=self.manifest_path.with_name(self.manifest_path.name+'.lock')
        with lock.open('x',encoding='utf-8') as stream:
            stream.write(f'manifest writer started at {pt._now()}\n')
        try:
            if pt._file_hash(self.manifest_path)!=self._saved_hash:raise ValueError('Run manifest changed in another session; reselect it.')
            pt._write_checkpoint(self.manifest_path,self.state)
            self._saved_hash=pt._file_hash(self.manifest_path)
            self._dirty=False
            self.refresh_summary(_manifest_locked=True)
        finally:lock.unlink()

    def refresh_summary(self, *, _manifest_locked=False):
        """Reporting failures never roll back a committed stage or retry a provider."""
        if self.state.get('artifact_layout')!=1:return
        from .run_reporting import write_summary
        try:
            return write_summary(self.manifest_path,_manifest_locked=_manifest_locked)
        except (OSError,ValueError) as exc:
            print(f'Run {self.state["run_id"]}: saved artifacts retained; RUN_SUMMARY.md refresh failed: {exc}. '
                  'Retry with run_reporting.write_summary(manifest_path).')

    def _source(self):
        identity=self.state['identity'];source=Path(identity['source_path'])
        if pt._file_hash(source)!=identity['source_sha256']:raise ValueError('Selected run source changed.')
        return source

    def _upstream(self, stage):
        names=list(DEPENDENCIES[stage])
        if stage=='finalization':names += [s for s in ('fallback','fallback_evaluation') if s in self.state['stages']]
        for name in names:
            if name not in self.state['stages']:raise ValueError(f'{stage} requires this run\'s {name} checkpoint.')
        return {s:self.state['stages'][s]['sha256'] for s in names}

    def _path(self, stage):
        entry=self.state['stages'].get(stage)
        return Path(entry['path']) if entry else None

    def _validate(self, stage, path):
        from . import evaluation as ev
        from . import fallback_translation as ft
        from . import fallback_evaluation as fe
        from . import finalization as fin
        source=self._source()
        path=Path(path).resolve()
        if self.manifest_path.parent not in path.parents:raise ValueError('Run artifacts must remain inside the selected run directory.')
        if stage=='primary':
            state,_=pt.read_validated_candidates(path,source)
            c=state['compatibility'];i=self.state['identity']
            if state.get('diagnostic') or c['units_sha256']!=self.state['units_sha256'] or any(c[k]!=i[k] for k in ('source_path','source_language','target_language')):
                raise ValueError('Primary checkpoint does not match this run.')
        elif stage=='evaluation':
            state=fin._validate_evaluation(path,self._path('primary'),source,ev.build_evaluation_payload(self._path('primary'),source))
        elif stage=='fallback':
            state=ft._read(path);c=state['compatibility']
            expected=ft.build_fallback_payload(self._path('primary'),self._path('evaluation'),source,policy_version=pt.vp.payload_policy(state['payload'],default=2))
            if (state['payload']!=expected or c['primary_sha256']!=pt._file_hash(self._path('primary'))
                    or c['evaluation_sha256']!=pt._file_hash(self._path('evaluation')) or expected.get('diagnostic')):
                raise ValueError('Fallback checkpoint belongs to a different run branch.')
            fe.build_fallback_evaluation_payload(path,source)
        elif stage=='fallback_evaluation':
            state=fin._validate_evaluation(path,self._path('fallback'),source,fe.build_fallback_evaluation_payload(self._path('fallback'),source),fallback=True)
        else:
            from . import finalized_reconstruction as fr
            final_path=path if stage=='finalization' else self._path('finalization')
            plan=fr.read_validated_snapshot(final_path,source)
            expected={'primary':self._path('primary'),'primary_evaluation':self._path('evaluation')}
            expected.update({s:self._path(s) for s in ('fallback','fallback_evaluation') if self._path(s)})
            if set(plan['resolved_inputs'])!={'source',*expected} or any(Path(plan['resolved_inputs'][s]).resolve()!=p.resolve() for s,p in expected.items()):
                raise ValueError('Finalization ancestry belongs to another run branch.')
            state=plan['snapshot']
            if stage=='reconstruction':
                state=fr.validate_reconstruction_artifacts(path,final_path,source)['report']
        return state

    def _invalidate(self, changed):
        removed=set()
        for stage in STAGES:
            entry=self.state['stages'].get(stage)
            if entry and ((set(DEPENDENCIES[stage]) | ({'fallback','fallback_evaluation'} if stage=='finalization' else set())) & (set(changed)|removed)):
                self.state['history'].append(dict(stage=stage,reason='upstream_changed',artifact=self.state['stages'].pop(stage),at=pt._now()))
                print(f'Run {self.state["run_id"]}: {stage} is stale; retained artifact, next execution starts a new branch.')
                removed.add(stage)
        return removed

    def sync(self, *, persist=True):
        if pt._file_hash(self.manifest_path)!=self._saved_hash:
            raise ValueError('Run manifest changed in another session; reselect it.')
        self._source()
        changed=[]
        for stage in STAGES:
            entry=self.state['stages'].get(stage)
            if entry is None:continue
            path=Path(entry['path']);digest=pt._file_hash(path)
            if entry['upstream']!=self._upstream(stage):raise ValueError('Manifest upstream identity mismatch.')
            if self._validated.get(stage)==digest and entry['sha256']==digest and stage!='reconstruction' and not entry['settings'].get('recovery_original_sha256') and not entry['settings'].get('recovery_parent_sha256'):continue
            state=self._validate(stage,path)
            if state.get('compatibility',{})!=entry['settings']:raise ValueError('Recorded checkpoint settings changed.')
            self._validated[stage]=digest
            if pt._file_hash(path)!=digest:raise ValueError('Checkpoint changed during run validation.')
            if digest!=entry['sha256']:
                entry.update(sha256=digest,status=state.get('status'))
                changed.append(stage);self._invalidate([stage])
        if changed:self._dirty=True
        if persist and self._dirty:self._save()
        return self

    def path(self, stage):
        self.sync(persist=False);return self._path(stage)

    def primary_units(self, units):
        """Keep exact saved source serialization after equivalent XML prefix changes."""
        self.sync()
        path=self._path('primary')
        if path is None:
            if pt._digest(units)!=self.state['units_sha256']:raise ValueError('Run extraction/classification changed.')
            return units
        saved=pt.read_primary_state(path)['units'];current=deepcopy(units)
        if len(saved)!=len(current):raise ValueError('Run source unit coverage changed.')
        from . import pptx_helpers as ph
        for new,old in zip(current,saved):
            ph.reconcile_span_formats(new, old, coverage_error='Run formatting coverage changed.',
                                      formatting_error='Run source formatting changed.')
        if current!=saved:raise ValueError('Run source units/classification changed.')
        return saved

    def prepare(self, stage):
        """Return this exact run's target and explicit adapter resume mode."""
        self.sync();upstream=self._upstream(stage)
        path=self._path(stage)
        if stage=='fallback' and path is not None:
            from . import fallback_translation as ft
            selected=ft._read(path)
            if selected.get('fallback_offline_recovery') and not selected.get('offline_recovery_parent'):
                from .fallback_recovery import continuation
                path=continuation(path)
        if path is None:
            token=pt._digest(upstream)[:20]
            root=self.manifest_path.parent
            if self.state.get('artifact_layout')==1:root=root/'checkpoints'
            path=root/(f'{stage}_{token}.json' if stage!='primary' else 'primary.json')
        # Deterministic owned targets recover a checkpoint saved before manifest commit.
        if path.exists():self.record(stage,path)
        return path,path.exists()

    def record(self, stage, path):
        """Commit only an already persisted, validated artifact; never select by recency."""
        self.sync();upstream=self._upstream(stage)
        digest=pt._file_hash(path);state=self._validate(stage,path)
        if pt._file_hash(path)!=digest:raise ValueError('Artifact changed during manifest validation.')
        old=self.state['stages'].get(stage)
        if old and Path(old['path']).resolve()!=Path(path).resolve():
            self.state['history'].append(dict(stage=stage,reason='replaced_artifact',artifact=deepcopy(old),at=pt._now()))
        self.state['stages'][stage]=dict(path=str(Path(path).resolve()),sha256=digest,upstream=upstream,
                                      settings=deepcopy(state.get('compatibility',{})),status=state.get('status'))
        self._invalidate([stage]) if old is None or old['sha256']!=digest else None
        self._save()

    def recover_primary(self):
        """Explicit offline numeric revalidation, then ordinary primary registration."""
        from .primary_recovery import recover
        self.sync()
        path=self._path('primary')
        if path is None:raise ValueError('Register the original primary checkpoint first.')
        state=pt.read_primary_state(path,self._source())
        if state.get('offline_recovery'):
            return dict(path=str(path),sha256=pt._file_hash(path),
                        unit_keys=[e['unit_key'] for e in state['offline_recovery']['events']])
        result=recover(path,self._source())
        self.record('primary',result['path'])
        return result

    def recover_fallback(self):
        """Explicit offline revalidation of the one saved legacy Claude response."""
        from . import fallback_translation as ft
        from .fallback_recovery import recover
        self.sync();path=self._path('fallback')
        if path is None:raise ValueError('Register the original Claude checkpoint first.')
        state=ft._read(path)
        if state.get('fallback_offline_recovery'):
            recovery=state['fallback_offline_recovery']
            return dict(path=recovery['artifact_path'],sha256=recovery['artifact_sha256'],
                        unit_keys=[e['unit_key'] for e in recovery['events']])
        result=recover(path,self._source())
        self.record('fallback',result['path'])
        return result

    def _receipt_path(self, stage):
        token=pt._digest(self._upstream(stage))[:20]
        root=self.manifest_path.parent
        if self.state.get('artifact_layout')==1:root=root/'checkpoints'
        return root/f'{stage}_attempt_{token}.json'

    def _remember_artifact(self, stage, path):
        pt._write_checkpoint(self._receipt_path(stage),dict(schema_version=1,stage=stage,
            upstream=self._upstream(stage),path=str(Path(path).resolve()),sha256=pt._file_hash(path)))

    def _remembered_artifact(self, stage):
        receipt=self._receipt_path(stage)
        if not receipt.exists():return None
        record=json.loads(receipt.read_text(encoding='utf-8'),object_pairs_hook=pt._unique_object)
        integrity=record.pop('integrity',None)
        if (integrity!=pt._digest(record) or record['schema_version']!=1 or record['stage']!=stage
                or record['upstream']!=self._upstream(stage)
                or pt._file_hash(record['path'])!=record['sha256']):
            raise ValueError('Invalid final-stage attempt reference.')
        path=Path(record['path']).resolve()
        if self.manifest_path.parent not in path.parents:raise ValueError('Attempt artifact is outside this run.')
        return path

    def finalize(self):
        """Export once per lineage, or recover a persisted export before registration."""
        from . import finalization as fin
        if self.state.get('artifact_layout')==1:
            from .artifact_layout import finalize
            return finalize(self)
        self.sync();self._upstream('finalization')
        path=self._path('finalization') or self._remembered_artifact('finalization')
        if path is None:
            snapshot=fin.build_finalization_snapshot(self._path('primary'),self._path('evaluation'),self._source(),
                fallback_checkpoint=self._path('fallback'),fallback_evaluation_checkpoint=self._path('fallback_evaluation'))
            files=fin.write_finalization_artifacts(snapshot,self.manifest_path.parent/'finalization')
            path=Path(files['snapshot'])
            self._remember_artifact('finalization',path)
        self._validate('finalization',path)
        files={k:str(path.parent/name) for k,name in [('snapshot','finalization.json'),('summary','summary.csv'),('manual_review','manual_followup.md')]}
        if not all(Path(p).is_file() for p in files.values()):raise ValueError('Finalization report artifacts are missing.')
        self.record('finalization',path)
        return files

    def reconstruct(self, *, regenerate=False):
        """Generate/reuse outputs only; caller displays validation before registration."""
        from . import finalized_reconstruction as fr
        if self.state.get('artifact_layout')==1:
            from .artifact_layout import reconstruct
            try:return reconstruct(self, regenerate=regenerate)
            finally:self.refresh_summary()
        if regenerate:raise ValueError('Explicit regeneration is supported for new-layout runs only.')
        self.sync();self._upstream('reconstruction')
        path=self._path('reconstruction') or self._remembered_artifact('reconstruction')
        if path is not None:
            state=json.loads(path.read_text(encoding='utf-8'))
            if state.get('status') in ('passed','manual_review'):
                return fr.validate_reconstruction_artifacts(path,self._path('finalization'),self._source())
            # Failed artifacts are retained. A new attempt gets a new directory.
        result=fr.reconstruct_finalized_snapshot(self._path('finalization'),self.manifest_path.parent/'translated_pptx',self._source())
        self._remember_artifact('reconstruction',result['report_path'])
        return result
