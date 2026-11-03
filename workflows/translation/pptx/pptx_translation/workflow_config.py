"""Portable Stage 1 settings; imports never load credentials or create clients."""
from dataclasses import dataclass, field
from pathlib import Path
import importlib
import os
from .artifact_paths import project_root

PROJECT_ROOT = project_root(__file__)

@dataclass(frozen=True)
class WorkflowConfig:
    input_path: Path = PROJECT_ROOT / 'examples' / 'stage1_sample.pptx'
    output_root: Path = PROJECT_ROOT / 'outputs'
    source_language: str = 'English'
    target_language: str = 'Spanish'
    include_smartart: bool = True
    write_reports: bool = False
    # Explicit gate for provider construction and notebook primary execution.
    enable_paid_api: bool = False
    models: dict = field(default_factory=lambda: {'primary': '', 'evaluation': '', 'fallback': ''})

    def __post_init__(self):
        for name in ('input_path', 'output_root'):
            path = Path(getattr(self, name)).expanduser()
            object.__setattr__(self, name, (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve())
        if self.input_path.suffix.lower() != '.pptx' or self.input_path.name.startswith('~$'):
            raise ValueError('Select a .pptx presentation, not an Office lock file.')
        if not self.source_language.strip() or not self.target_language.strip():
            raise ValueError('Source and target languages must be nonempty.')
        if self.output_root == self.input_path or self.output_root in self.input_path.parents:
            raise ValueError('Keep input presentations outside the output directory.')

def api_key_status():
    return {name: 'present' if os.environ.get(name) else 'missing'
            for name in ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY')}

class LazyAPIClients:
    """Explicitly gated, on-demand SDK construction; never makes a request.

    SDKs are optional. The notebook requests Gemini only after both explicit
    primary-execution gates are enabled. Construction itself makes no request.
    """
    def __init__(self, config):
        self.config = config
        self._clients = {}

    def get(self, provider):
        if not self.config.enable_paid_api:
            raise RuntimeError('API initialization is disabled in this configuration.')
        specs = {'openai': ('openai', 'OpenAI', 'OPENAI_API_KEY'),
                 'anthropic': ('anthropic', 'Anthropic', 'ANTHROPIC_API_KEY'),
                 'gemini': ('google.genai', 'Client', 'GEMINI_API_KEY')}
        module, constructor, key_name = specs[provider]
        key = os.environ.get(key_name)
        if not key:
            raise ValueError(f'{key_name} is missing')
        if provider not in self._clients:
            self._clients[provider] = getattr(importlib.import_module(module), constructor)(api_key=key)
        return self._clients[provider]
