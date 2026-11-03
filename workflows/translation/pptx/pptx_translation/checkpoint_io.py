"""Shared hashing and atomic JSON snapshots; no workflow or provider imports.

Callers retain ownership of checkpoint schemas, parent directories, and locks.
Serialization and filesystem operations preserve the original primary utilities.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_checkpoint(path, state, *, digest_function=None):
    """Atomic UTF-8 snapshot; a stage lock protects creation and subsequent updates.

    The optional digest dependency preserves the primary module's late-bound
    compatibility hook. Ordinary neutral callers use this module's digest.
    """
    payload = deepcopy(state)
    payload['integrity'] = (digest if digest_function is None else digest_function)(state)
    descriptor, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
