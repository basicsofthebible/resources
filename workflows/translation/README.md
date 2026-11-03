# Translation Workflows

This folder contains notebook-based translation workflows for different source formats.

## Current folders

### `svg`
An [SVG translation workflow](svg/README.md) for extracting translatable text from structured SVG files, packaging the content for LLM translation, and writing the translated result back into SVG-oriented output files.

### `word`
A [Word document translation workflow](word/README.md) for parsing styled `.docx` files into structured elements, batching content for LLM processing, running staged translation and review steps, and reconstructing translated Word output.

### `pptx`
The [PPTX Translation Workflow](pptx/README.md) translates PowerPoint presentations while preserving editable slide structures, styled text, tables, hyperlinks, and conservatively supported SmartArt. It combines Gemini translation, OpenAI semantic evaluation, and optional Claude fallback with validated reconstruction. Checkpoints, provenance, and recovery support traceable processing and safe resumption; final outputs still require human PowerPoint inspection.

## General structure

Each subfolder contains its own workflow-specific notebooks, helper code, and supporting folders such as intermediate JSON checkpoints or source/output files.
