# SVG Translation Workflows

This folder contains notebook-based workflows for translating text in structured SVG graphics while preserving visual layout and SVG structure.

Rather than relying on a single rigid pipeline, each subfolder is a self-contained example workflow tailored to the structural quirks and domain requirements of a specific graphic or set of graphics. All examples share the same core pattern—extracting SVG text elements into structured JSON translation units, translating them in packets with an LLM, and writing translated text back into SVG files—while adapting filtering, `<tspan>` recombination, and post-processing rules as needed.

## General workflow pattern

Across these examples, the typical workflow includes:

1. **Extract and inspect (`00_open_and_inspect_svg.ipynb`)**  
   Parse source SVGs from `svg_source_files/`, inspect `<text>` and `<tspan>` structure, filter out non-translatable elements (such as numbers, dates, symbols, or preserved terms), and export structured translation units to `json_files/`.
2. **Translate and reconstruct (`01_translate_svg.ipynb`)**  
   Batch translation units into LLM requests, validate the returned JSON, reinsert translated strings into the SVG structure (optionally collapsing fragmented `<tspan>` elements), and export translated SVGs and review tables to `svg_output_files/`.
3. **Graphic-specific post-processing (optional)**  
   Run any additional deterministic cleanup or normalization steps required by a particular graphic (for example, `02_finalize_bible_reference_abbreviations.ipynb` in [`rev/`](rev/)).

## Current workflows

- **[`bst/`](bst/README.md) — *Bible Structure and Timeline***  
  Foundational two-notebook workflow demonstrating baseline SVG text extraction, packetized LLM translation, and SVG reconstruction on relatively straightforward text elements.

- **[`tbe/`](tbe/README.md) — *Timeline of Bible Events***  
  Extends the baseline workflow to handle more complex SVG text layout, including recombining visible labels split across fragmented `<tspan>` elements and optionally writing collapsed-tspan SVG outputs.

- **[`rev/`](rev/README.md) — *Revelation Graphics* (*Seven Churches of Revelation* & *Structure of Revelation*)**  
  Builds on the earlier examples with graphic-specific filtering and post-processing, including Greek-text and symbol preservation, repeated-term notes, collapsed-tspan output, and a third notebook step for deterministic normalization of Bible reference abbreviations.

## Licensing note

The code in these workflows is part of the repository-wide MIT-licensed codebase unless otherwise noted. Example SVG source files and related creative materials in the subfolders are subject to the Creative Commons licensing terms described in each subfolder's `README.md`.

