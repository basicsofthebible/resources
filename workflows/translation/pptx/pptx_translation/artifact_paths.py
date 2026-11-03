"""Module-anchored project roots and exact recorded artifact resolution.

No workflow-stage imports, environment discovery, newest-file selection, or writes.
Explicit recorded paths retain their original precedence and hash requirements.
"""
from pathlib import Path, PureWindowsPath
from . import checkpoint_io


def project_root(module_file=None, *, path_factory=None):
    """Support the flat layout and one enclosing pptx_translation package level."""
    make_path = Path if path_factory is None else path_factory
    folder = make_path(__file__ if module_file is None else module_file).resolve().parent
    return folder.parent if folder.name == 'pptx_translation' else folder


def resolve_input(record, source_override=None, *, module_file=None,
                  path_factory=None, hash_function=None):
    """Find only the explicitly recorded input with the exact saved identity.

    Optional dependencies retain stage-level Path/hash interception contracts.
    An override is the sole candidate. Ordinary resolution preserves candidate
    order and the legacy finalization error text, including for recovery callers.
    """
    make_path = Path if path_factory is None else path_factory
    file_hash = checkpoint_io.file_hash if hash_function is None else hash_function
    recorded = record['path']
    linked = PureWindowsPath(recorded) if '\\' in recorded else make_path(recorded)
    candidates = [make_path(source_override).resolve()] if source_override is not None else [make_path(recorded)]
    if source_override is None:
        for anchor in ('outputs','pptx_files','examples'):
            if anchor in linked.parts:
                candidates.append(project_root(module_file, path_factory=make_path).joinpath(*linked.parts[linked.parts.index(anchor):]))
        if not linked.is_absolute():candidates.append(project_root(module_file, path_factory=make_path) / recorded)
    for path in candidates:
        if path.is_file() and file_hash(path)==record['sha256']:
            return path.resolve()
    raise ValueError(f'Missing or changed finalization input: {recorded}')
