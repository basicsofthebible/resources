# SVG Translation Workflow: Timeline of Bible Events

This directory contains the TBE (*Timeline of Bible Events*) SVG translation workflow example. It is a second published example of the SVG translation workflow, following the same overall two-notebook pattern as the earlier BST (*Bible Structure and Timeline*) example.

The TBE SVG files are more complex than the BST files, especially because some visible labels are split across fragmented SVG `<tspan>` elements. This example keeps the same basic extraction, translation, validation, and SVG rewrite workflow, while adding handling for recombining fragmented tspans into text-level translation units and optionally writing collapsed-tspan SVG outputs.

The example SVG files here are from *Timeline of Bible Events* from [basicsofthebible.org](https://basicsofthebible.org/tbe/).

## Contents

### Notebooks
- `00_open_and_inspect_svg.ipynb`  
  Opens SVG files, extracts text elements and nested tspans, preserves meaningful whitespace, recombines fragmented tspans into text-level translation units, filters out non-translation items such as numeric-only content and timeline/date markers, and writes translation-unit JSON files.

- `01_translate_svg.ipynb`  
  Loads translation units, sends them to an LLM in packetized JSON requests, validates and saves translated JSON output, applies translations back into SVG files, exports review tables, and optionally writes collapsed-tspan SVG outputs for fragmented text.

### Subdirectories
- `svg_source_files/`  
  Source SVG files to be processed.

- `json_files/`  
  Intermediate and output JSON files, including extracted translation units and translated text outputs.

- `svg_output_files/`  
  Translated SVG files and exported review tables.

- `svg_output_files_collapsed_tspans/`  
  Optional translated SVG outputs from the reconstruction path that collapses fragmented tspans into a single translated text span.

## Workflow

### Step 1 — Extract text from SVG
Run:

- `00_open_and_inspect_svg.ipynb`

This notebook:
- loads SVG files from `svg_source_files/`
- parses text and tspan elements
- preserves meaningful whitespace from fragmented tspans
- recombines fragmented tspans into text-level translation units
- filters out units that should not be translated
- writes translation-unit JSON to `json_files/`

Main output:
- `json_files/translation_units.json`

### Step 2 — Translate and reinsert text
Run:

- `01_translate_svg.ipynb`

This notebook:
- loads `translation_units.json`
- batches units into request-sized packets
- sends packets to the configured LLM
- validates returned JSON
- saves translated JSON files
- applies translated text back into the source SVGs
- writes translated SVGs to `svg_output_files/`
- optionally writes collapsed-tspan SVGs to `svg_output_files_collapsed_tspans/`

Typical outputs include:
- timestamped translated JSON files in `json_files/`
- timestamped translated SVG files in `svg_output_files/`
- optional collapsed-tspan SVG files in `svg_output_files_collapsed_tspans/`
- CSV / XLSX / Markdown review tables in `svg_output_files/`

A local `.env` file is used for API credentials when running the translation notebook. Do not commit `.env` to the repository.

## Expected working structure

```text
tbe/
├── 00_open_and_inspect_svg.ipynb
├── 01_translate_svg.ipynb
├── README.md
├── svg_source_files/
├── json_files/
├── svg_output_files/
└── svg_output_files_collapsed_tspans/
```

## Licensing note

The code in this workflow is part of the repository-wide MIT-licensed codebase unless otherwise noted.

The example SVG source files and related creative materials in this folder are original creative works by Shawn Handran and are licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International license (CC BY-NC-SA 4.0).

You may share and adapt those example materials for noncommercial purposes with attribution, and any adaptations must be distributed under the same license.

License details: https://creativecommons.org/licenses/by-nc-sa/4.0/
