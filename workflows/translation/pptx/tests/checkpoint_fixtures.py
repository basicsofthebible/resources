"""Synthetic managed-run helper; no recorded private fixtures."""
from pathlib import Path
from pptx_translation import primary_translation as pt

def managed_run(config, primary, evaluation=None, fallback=None):
    """Isolated run using byte-identical copies of current-schema fake checkpoints."""
    import shutil
    from pptx_translation.active_run import ActiveRun
    units=pt._read_checkpoint(primary)['units']
    run=ActiveRun.create(config,units,any('styled_spans' in u for u in units))
    for stage,original in [('primary',primary),('evaluation',evaluation),('fallback',fallback)]:
        if original is None:continue
        target,_=run.prepare(stage)
        shutil.copyfile(original,target)
        run.record(stage,target)
    return run
