# SVG Chinese converter

Convert editable SVG display text between Simplified and Traditional Chinese locally, while preserving SVG hierarchy, attributes, coordinates, styles, and span boundaries. This is useful for producing a second Chinese writing-system version of an already translated graphic. No LLM, API credentials, or translation service is needed.

This converter does not translate English into Chinese, correct fonts, or automatically adjust layout.

## Requirements and installation

The development notebook records Python 3.11.10. The development README records OpenCC 1.4.2, lxml 5.3.0, and ipykernel 6.29.5; these versions are pinned in [requirements.txt](requirements.txt). A fresh installation has not been independently verified for this publication.

Create and activate a Python 3.11 environment, then run from this directory:

```sh
python -m pip install -r requirements.txt
```

Use a Jupyter-capable frontend, such as VS Code with its Python and Jupyter extensions, and select that environment as the notebook kernel. A separate Jupyter Notebook/Lab frontend can also be used if installed. OpenCC supplies its conversion dictionaries; no separate dictionary download is needed. Fonts and Adobe Illustrator are not required for conversion, but suitable fonts and a viewer/editor are needed for visual review.

## Run the notebook

1. Create an `inputs/` directory here and place your SVGs directly inside it.
2. Open [svg_chinese_converter.ipynb](svg_chinese_converter.ipynb) and select your environment's Python kernel.
3. Set `CONVERSION_DIRECTION` to `"s2t"` (Simplified to Traditional, the default) or `"t2s"` (Traditional to Simplified). `INPUT_DIR` defaults to `Path("inputs")`.
4. Run all cells with this directory as the working directory. Review the final status table, QC CSVs, and rendered SVGs.

The notebook selects matching `.svg` files in sorted filename order without searching subdirectories. Filename language tokens are case-sensitive:

| Direction | Input filename must contain | Output replacement |
|---|---|---|
| `s2t` | `simplified_chinese` | `traditional_chinese` |
| `t2s` | `traditional_chinese` | `simplified_chinese` |

For example, `inputs/chart_simplified_chinese.svg` produces `outputs/chart_traditional_chinese.svg` and `reports/chart_traditional_chinese_qc.csv`. Output directories are created when needed. Input SVGs are not overwritten. No matching files means no conversions; file-level failures are reported without stopping the remaining batch.

## Existing files

The notebook skips an existing output without modifying or revalidating it, warning if its QC report is missing. A report without its output also causes a skip and warning. To reconvert, move both the output SVG and QC CSV to a backup location outside these directories, then rerun.

These skip safeguards belong to the notebook. Direct calls to `convert_svg()` in [svg_chinese.py](svg_chinese.py) can overwrite existing outputs and reports.

## Validation and QC

Each UTF-8 CSV records text element IDs where available, XML segment locations, original and converted strings, change flags, and warnings/errors. Checks flag suspicious Unicode, segment conversion failures, and adjacent Chinese segments where phrase context may cross boundaries. Failed segment conversions retain their original text.

New results are checked before saving and reparsed afterward. Structural comparison checks hierarchy, attributes, namespaces, and text content, allowing only recorded display-text changes. The final CSV row records XML validation. This is not SVG schema validation; a structural PASS does not establish linguistic correctness or visual equivalence. Review warnings and conversion failures; unchanged shared characters are normal.

## Limitations and visual review

Chinese conversion is context-dependent, especially for ambiguous characters, names, and regional usage. Each text slot is converted independently; phrase context cannot cross span boundaries, and adjacency warnings do not detect every semantic split. The converter does not provide complete regional terminology localization. Text outlined as paths and text stored in attributes are not converted.

Font definitions, glyph mappings, font references, and styles are preserved, not repaired. Newly converted characters may be missing from installed fonts or embedded font subsets. Font fallback and different glyph metrics can change spacing or line fit even though coordinates and transforms are preserved. XML serialization formatting may also change.

Proofread the Chinese and open results in Adobe Illustrator or another suitable SVG editor/viewer to inspect glyphs, spacing, alignment, and clipping. Handle font and layout corrections manually.

## TBE development and testing

The four *Timeline of Bible Events* (TBE) graphics were used to develop and test Simplified-to-Traditional conversion. Their starting point was the [TBE SVG translation workflow](../tbe/README.md), which translates source graphics into Simplified Chinese using an LLM.

Before conversion, the Simplified Chinese SVGs were manually adjusted in Adobe Illustrator: fonts were changed to support Chinese characters and minor text-position corrections were made. These were manual preparation steps, not features of this converter. The adjusted inputs differ from the same-named files in the existing TBE workflow.

The exact four adjusted inputs, four Traditional outputs, and associated QC reports are not included in this initial publication. They remain outside this directory pending review of embedded-font licensing, including Myriad Pro. No example SVGs or font data are bundled here. Supply your own editable Chinese SVGs to run the notebook. The supplied development examples demonstrate `s2t`; reverse conversion is supported but is not demonstrated by those examples.

## Licensing and attribution

Converter code is covered by the repository's [MIT license](../../../../LICENSE.md). The TBE artwork is by Shawn Handran / [basicsofthebible.org](https://basicsofthebible.org/tbe/); its separate CC BY-NC-SA 4.0 terms are described in the [TBE README](../tbe/README.md).

Dependencies retain their own licenses; see [OpenCC](https://github.com/BYVoid/OpenCC), [lxml](https://lxml.de/), and [IPython/ipykernel](https://github.com/ipython/ipykernel). Input artwork and any embedded fonts retain their own applicable terms. The converter does not remove font data or establish redistribution permissions.
