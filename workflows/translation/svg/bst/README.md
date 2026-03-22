# BST SVG Translation Workflow

This directory contains a two-step notebook workflow for extracting text from SVG files, preparing that text for LLM translation, and writing translated outputs back to JSON and SVG files.

## Contents

### Notebooks
- `00_open_and_inspect_svg.ipynb`  
  Opens SVG files, extracts text elements, filters out non-translation items such as numeric-only content, and writes translation-unit JSON files.

- `01_translation_svg.ipynb`  
  Loads translation units, sends them to an LLM in packetized JSON requests, validates and saves translated JSON output, and applies translations back into SVG files.

### Subdirectories
- `svg_source_files/`  
  Source SVG files to be processed.

- `json_files/`  
  Intermediate and output JSON files, including extracted translation units and translated text outputs.

- `svg_output_files/`  
  Translated SVG files and exported review tables.

## Workflow

### Step 1 — Extract text from SVG
Run:

- `00_open_and_inspect_svg.ipynb`

This notebook:
- loads SVG files from `svg_source_files/`
- parses text and tspan elements
- filters out units that should not be translated
- writes translation-unit JSON to `json_files/`

Main output:
- `json_files/translation_units.json`

### Step 2 — Translate and reinsert text
Run:

- `01_translation_svg.ipynb`

This notebook:
- loads `translation_units.json`
- batches units into request-sized packets
- sends packets to the configured LLM
- validates returned JSON
- saves translated JSON files
- applies translated text back into the source SVGs
- writes translated SVGs to `svg_output_files/`

Typical outputs include:
- timestamped translated JSON files in `json_files/`
- timestamped translated SVG files in `svg_output_files/`
- CSV / XLSX / Markdown review tables in `svg_output_files/`

## Expected working structure

```text
bst/
├── 00_open_and_inspect_svg.ipynb
├── 01_translation_svg.ipynb
├── README.md
├── .env
├── svg_source_files/
├── json_files/
└── svg_output_files/
