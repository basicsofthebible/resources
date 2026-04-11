# Word LLM Translation Workflow

This folder contains an example notebook-based workflow for translating styled Word documents with multiple LLM stages while preserving document structure as much as possible.

Create a `.env` file in this folder and store the required API keys there before running the notebooks. This workflow expects model credentials to be loaded from environment variables, not entered directly into the notebook code.

## Model configuration note

This workflow currently uses a staged multi-model setup:

- **Primary translator:** Gemini
- **Evaluator:** OpenAI GPT
- **Fallback translator:** Anthropic Claude

The notebooks and `workflow_helpers.py` are currently written around those provider-specific client paths and response patterns. If you want to substitute a different provider for one of these stages, you should expect to update the corresponding helper functions as well.

## Current notebooks

At present, this workflow includes the following completed notebooks:

- `0_word_llm_translation_workflow_parse_and_batch_docx.ipynb`
- `1_primary_translation.ipynb`
- `2_rerun_primary_translation_errors.ipynb`
- `3_primary_translation_analysis.ipynb`
- `4_evaluate_primary_translation.ipynb`
- `5_fallback_translation.ipynb`
- `6_finalize_translation.ipynb`

The helper functions shared across notebooks are stored in:

- `workflow_helpers.py`

This folder also contains:

- `word_files/` — source Word files and intermediate Word outputs
- `checkpoints/` — timestamped JSON checkpoint files written between notebook stages
- `final_translation_files/` — final output artifacts generated at the end of the workflow

## Purpose of the workflow

This example workflow is designed to:

- extract styled paragraph content from a `.docx` file
- preserve useful formatting cues for translation
- batch content for efficient LLM processing
- run a primary translation pass
- rerun failed primary translation batches if needed
- inspect and analyze primary translation results
- evaluate the primary translation with a second model
- run fallback translation for items that fail evaluation
- generate finalized review and export artifacts in multiple formats

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
- runs the primary translation pass using Gemini
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
- performs basic inspection and analysis of the primary translation state
- checks for likely untranslated or suspicious items
- reconstructs a Word document using the `primary_translation` field for review

### 4. Evaluate primary translation
`4_evaluate_primary_translation.ipynb`

This notebook:

- loads the primary translation checkpoint
- defines the evaluator prompt
- evaluates the primary translation in batches using an OpenAI/GPT model
- records evaluator pass/fail results and feedback per element
- promotes evaluator-passed primary translations into the `final` fields
- saves an evaluation-completed checkpoint

### 5. Fallback translation
`5_fallback_translation.ipynb`

This notebook:

- loads the evaluation checkpoint
- identifies only the elements that failed evaluation
- runs fallback translation on those elements using Anthropic Claude
- reevaluates fallback translations using the saved evaluator configuration
- writes accepted fallback results into the `final` fields
- saves a fallback-completed checkpoint

### 6. Finalize translation
`6_finalize_translation.ipynb`

This notebook:

- loads the fallback-completed checkpoint
- verifies final translation coverage
- generates final output artifacts
- exports finalized Word, spreadsheet, JSON, and Markdown outputs
- writes outputs into `final_translation_files/`
- updates workflow metadata to the `finalized` stage

## Checkpoint design

Checkpoint files are saved as JSON with this top-level structure:

```json
{
  "metadata": { ... },
  "elements": [ ... ]
}
```

The `metadata` block is used to carry workflow provenance between notebooks, including items such as:

- workflow stage
- source DOCX filename
- source language
- target language
- model names
- saved system messages used in earlier stages

The `elements` list contains the extracted document units together with translation-, evaluation-, fallback-, and finalization-related schema fields.

## Current document scope

This workflow currently focuses on paragraph-based document extraction.

At this stage:

- tables are intentionally omitted
- blank paragraphs are skipped
- image-only paragraphs are skipped
- footnotes are extracted and appended after body paragraphs
- batching is heuristic and may be adjusted depending on document size and structure

## Notes on formatting preservation

The workflow aims to preserve useful structure and inline emphasis, but it is still an example workflow and not a full fidelity DOCX round-trip engine.

Current handling includes:

- paragraph styles where practical
- bold / italic markdown-style cues during translation
- reconstruction of translated content into Word output using the source file as a style template where possible
- appended and interlinear Word exports at later workflow stages

## Final outputs

The finalization stage can generate artifacts such as:

- appended final `.docx`
- interlinear `.docx`
- full-schema `.csv`
- reduced `.csv`
- reduced `.xlsx`
- full workflow-state `.json`
- reduced `.json`
- Markdown parallel text
- unresolved-item report files when applicable

## Folder contents
word/  
├── checkpoints/  
├── final_translation_files/  
├── word_files/  
├── 0_word_llm_translation_workflow_parse_and_batch_docx.ipynb  
├── 1_primary_translation.ipynb  
├── 2_rerun_primary_translation_errors.ipynb  
├── 3_primary_translation_analysis.ipynb  
├── 4_evaluate_primary_translation.ipynb  
├── 5_fallback_translation.ipynb  
├── 6_finalize_translation.ipynb  
└── workflow_helpers.py  

## Intended usage

This workflow is best understood as a staged notebook pipeline:

1. place the input .docx file in word_files/
2. run Notebook 0 to extract and batch content
3. run Notebook 1 for primary translation
4. run Notebook 2 only if retrying failed primary batches is necessary
5. run Notebook 3 to inspect the primary translation state
6. run Notebook 4 to evaluate the primary translation
7. run Notebook 5 to fallback-translate and reevaluate failed items
8. run Notebook 6 to generate finalized output artifacts

## Public repo note

This example was cleaned up from a larger prototyping process. The notebooks and helper file are organized as a metadata-aware staged workflow so the intermediate state can move cleanly from one notebook to the next.

## Licensing note

The code in this workflow is part of the repository-wide MIT-licensed codebase unless otherwise noted.

The example Word source materials in this folder are original creative works by Shawn Handran and are licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International license (CC BY-NC-SA 4.0).

You may share and adapt those example materials for noncommercial purposes with attribution, and any adaptations must be distributed under the same license.

License details: https://creativecommons.org/licenses/by-nc-sa/4.0/