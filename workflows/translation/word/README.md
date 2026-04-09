# Word LLM Translation Workflow

This folder contains an example notebook-based workflow for translating styled Word documents with multiple LLM stages while preserving document structure as much as possible.

Create a `.env` file in this folder and store the required API keys there before running the notebooks. This workflow expects model credentials to be loaded from environment variables, not entered directly into the notebook code.

## Current workflow status

At present, this workflow includes the following completed notebooks:

- `0_word_llm_translation_workflow_parse_and_batch_docx.ipynb`
- `1_primary_translation.ipynb`
- `2_rerun_primary_translation_errors.ipynb`
- `3_primary_translation_analysis.ipynb`

The helper functions shared across notebooks are stored in:

- `workflow_helpers.py`

This folder also contains:

- `word_files/` — source Word files and generated Word outputs
- `checkpoints/` — timestamped JSON checkpoint files written between notebook stages

## Purpose of the workflow

This example workflow is designed to:

- extract styled paragraph content from a `.docx` file
- preserve useful formatting cues for translation
- batch content for efficient LLM processing
- run a primary translation pass
- rerun failed primary translation batches if needed
- inspect and analyze primary translation results
- reconstruct a translated Word document from the translated element state

The workflow is intended as a reusable example rather than a one-off script.

## Current notebook sequence

### 0. Parse and batch DOCX
`0_word_llm_translation_workflow_parse_and_batch_docx.ipynb`

This notebook:

- verifies the input DOCX path
- inspects paragraph styles and inline formatting
- extracts body paragraphs and footnotes into structured elements
- applies light text normalization for cleaner downstream translation
- counts tokens
- assigns batch numbers
- saves a metadata-aware JSON checkpoint

### 1. Primary translation
`1_primary_translation.ipynb`

This notebook:

- loads the batched checkpoint from Notebook 0
- applies the full workflow schema
- defines the primary translation prompt
- runs the primary translation pass
- saves the translated checkpoint with metadata and provenance

### 2. Rerun primary translation errors
`2_rerun_primary_translation_errors.ipynb`

This notebook:

- loads the primary translation checkpoint
- identifies batches with `primary_error`
- reruns only failed primary-translation batches
- saves an updated retry-stage checkpoint

This notebook is only needed when primary translation errors occur.

### 3. Primary translation analysis
`3_primary_translation_analysis.ipynb`

This notebook:

- loads the translated checkpoint
- performs basic inspection / analysis of the primary translation state
- reconstructs a Word document using the translated content

## Checkpoint design

Checkpoint files are saved as JSON with this top-level structure:

```json
{
  "metadata": { ... },
  "elements": [ ... ]
}
```

The metadata block is used to carry workflow provenance between notebooks, including items such as:
- workflow stage
- source DOCX filename
- source language
- target language
- model names
- saved system messages used in earlier stages

The elements list contains the extracted document units together with translation- and evaluation-related schema fields.

## Current document scope

This workflow currently focuses on paragraph-based document extraction.

At this stage:
- tables are intentionally omitted
- blank paragraphs are skipped
- image-only paragraphs are skipped
- footnotes are extracted and appended after body paragraphs
- batching is heuristic and may be adjusted depending on document size and structure
- Notes on formatting preservation

The workflow aims to preserve useful structure and inline emphasis, but it is still an example workflow and not a full fidelity DOCX round-trip engine.

Current handling includes:
- paragraph styles where practical
- bold / italic markdown-style cues during translation
- reconstruction of translated content into a Word document using the source file as a style template where possible

## Planned / in-progress stages

Additional stages are being developed beyond the notebooks currently committed here, including:
- evaluation of the primary translation with a second model
- fallback translation for items that fail evaluation
- finalization of accepted output

## Folder contents
word/  
├── checkpoints/  
├── word_files/  
├── 0_word_llm_translation_workflow_parse_and_batch_docx.ipynb  
├── 1_primary_translation.ipynb  
├── 2_rerun_primary_translation_errors.ipynb  
├── 3_primary_translation_analysis.ipynb  
└── workflow_helpers.py  

## Intended usage

This workflow is best understood as a staged notebook pipeline:
1. place the input .docx file in word_files/
2. run Notebook 0 to extract and batch content
3. run Notebook 1 for primary translation
4. run Notebook 2 only if retrying failed primary batches is necessary
5. run Notebook 3 to inspect results and reconstruct a translated Word document

## Public repo note

This example is being cleaned up from a larger prototyping process. The notebooks and helper file are being made more modular, metadata-aware, and reusable over time.

## Licensing note

The code in this workflow is part of the repository-wide MIT-licensed codebase unless otherwise noted.

The example Word source materials in this folder are original creative works by Shawn Handran and are licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International license (CC BY-NC-SA 4.0).

You may share and adapt those example materials for noncommercial purposes with attribution, and any adaptations must be distributed under the same license.

License details: https://creativecommons.org/licenses/by-nc-sa/4.0/
