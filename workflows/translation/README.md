# Translation Workflows

This folder contains notebook-based translation workflows for different source formats.

## Current folders

### `svg`
An SVG translation workflow for extracting translatable text from structured SVG files, packaging the content for LLM translation, and writing the translated result back into SVG-oriented output files.

### `word`
A Word document translation workflow for parsing styled `.docx` files into structured elements, batching content for LLM processing, running staged translation and review steps, and reconstructing translated Word output.

## General structure

Each subfolder contains its own workflow-specific notebooks, helper code, and supporting folders such as intermediate JSON checkpoints or source/output files.
