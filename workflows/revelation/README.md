# OpenBible Revelation Analysis

This folder contains public-facing notebooks for exploring cross-references to and
from Revelation using a processed OpenBible.info cross-reference dataset.

## Cross-References in the Book of Revelation

Visualized in the [`Structure of Revelation`](https://basicsofthebible.org/assets/img/projects/structureofrevelation.png) graphic.

Available at [basicsofthebible.org](https://basicsofthebible.org).

[CC-BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.en) 2024, by Shawn Handran.

![Structure of Revelation](https://basicsofthebible.org/assets/img/projects/structureofrevelation.png)

## Notebooks

Run the notebooks in this order:

1. `notebooks/01_revelation_cross_reference_analysis.ipynb`
   - Defines `rev_from` and `rev_to`.
   - Summarizes cross-references to and from Revelation.
   - Compares three New Testament ranking methods: incoming, outgoing, and
     combined references.
   - Builds Plotly charts for cross-references to Revelation.

2. `notebooks/02_structure_of_revelation_segments.ipynb`
   - Loads the Structure of Revelation schema.
   - Calculates cross-reference counts for each Revelation section.
   - Builds the Plotly section-count chart using schema colors.

3. `notebooks/03_biblegateway_reference_lookup.ipynb`
   - Optional lookup notebook.
   - Shows cross-reference rows for a selected source verse.
   - Adds Bible Gateway links for the target references.

## Data

`data/cross_references_revelation_public.csv` is derived from the local
`cross_references_expanded.csv` source file. The `Votes` column is intentionally
omitted because these notebooks count cross-reference rows and do not use vote
totals.

`data/structure_of_revelation_schema.csv` contains the Revelation section
definitions and color assignments used by the structure notebook.

## Definitions

- `rev_from`: cross-references where Revelation is in the `From Book` column.
  These are references from Revelation to other parts of the Bible.
- `rev_to`: cross-references where Revelation is in the `To Verse start Book`
  column. These are references to Revelation from other parts of the Bible.

## Dependencies

Minimal Python packages:

```text
pandas
plotly
ipykernel
nbformat
```

The notebooks use Plotly for charts. They do not require matplotlib, seaborn, or
static image export packages.

## Source Notes

The cross-reference data comes from OpenBible.info. The Structure of Revelation
schema and explanatory framing are project-specific materials for the Revelation
Graphic Explainer.
