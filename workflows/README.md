# Workflows

This folder contains reusable notebook-based workflows for processing and transforming structured content.

## Current contents

### `revelation`
Notebook workflow for analyzing Bible cross-references to and from Revelation using a processed OpenBible.info dataset. It includes:

- Cross-reference summaries for references to Revelation, references from Revelation, and internal Revelation references.
- Old Testament and New Testament breakdowns.
- New Testament ranking comparisons by incoming, outgoing, and combined cross-reference counts.
- Section-level counts for the Structure of Revelation graphic.
- An optional Bible Gateway lookup notebook for selected source verses.

The folder contains public CSV inputs, ordered notebooks, and small helper modules under `src/`.

### `translation`
A collection of translation workflows for different source formats. The materials in this folder are organized into format-specific subfolders, each containing its own notebooks, helper utilities, and supporting files for staged translation workflows.

## Reuse

This repository is intended to be open and reusable. Unless otherwise noted, you are welcome to use, adapt, and redistribute the code under the terms of the MIT License provided in `LICENSE.md`.

Please note that certain example files, source texts, images, SVGs, or Word documents may have different license terms, which are described in the relevant subfolder README files.
