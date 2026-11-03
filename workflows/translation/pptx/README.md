# PPTX Translation Workflow

Translate editable PowerPoint presentations while preserving their existing slide structure and formatting. This notebook-based workflow combines Gemini translation, independent OpenAI semantic review, optional Claude correction, and validated reconstruction into a new `.pptx` file. It helps avoid manually rebuilding translated slides or reapplying emphasis in an unfamiliar language.

The workflow is intended for people comfortable with Python notebooks and LLM APIs. It was developed and validated primarily for Biblical educational presentations. Its evaluator and Claude fallback prompts contain Protestant/Evangelical domain instructions; changing the source presentation or Gemini context does not make every stage domain-neutral. To adapt the workflow for other subject areas, users can customize the prompt instructions in `pptx_translation/evaluation.py` and `pptx_translation/fallback_translation.py`, and review `pptx_translation/fallback_evaluation.py` for corresponding evaluation instructions.  

## Key capabilities

- **Preserve editable slides.** Targeted XML changes retain existing shapes and formatting instead of recreating the presentation from plain text.
- **Keep styled text meaningful.** Ordered styled spans map translated wording back to formatting boundaries, including emphasis and supported linked runs, while preserving structural items such as tabs and soft breaks.
- **Translate efficiently.** Paragraph-aware batching reduces request overhead without splitting paragraphs across requests.
- **Add independent semantic review.** OpenAI evaluates meaning and required literal content; optional Claude candidates receive their own evaluation before selection.
- **Resume with traceability.** Hash-bound checkpoints retain candidates, decisions, attempts and provenance. A run manifest connects compatible artifacts and supports continuation after interruption.
- **Check reconstruction integrity.** Validation checks text placement, formatting, package structure and supported SmartArt data/cache consistency. Reports distinguish partial translation from complete coverage.

## How it works

| Stage | Result |
| --- | --- |
| Extract, classify and review QC | Paragraph units, source locators, styled spans, preserved-content classifications and extraction reports. |
| Gemini primary translation | Structurally validated translation candidates, saved incrementally. |
| OpenAI semantic evaluation | One binary accept/reject decision per complete paragraph, with concrete feedback for rejections. No numeric ratings or rewrites. |
| Optional Claude fallback | Corrections for eligible primary semantic rejections, followed by a separate OpenAI evaluation. |
| Offline finalization | An authoritative selection snapshot plus CSV and Markdown review reports. |
| Offline reconstruction and validation | A new editable PPTX, structural validation report and explicit coverage status. |

Only candidates accepted by the matching evaluator are selected for production reconstruction. Preserved units keep their source text. Unresolved units also retain source text and make translation coverage incomplete; they are never silently treated as accepted translations.

## Requirements and installation

Use a Python 3.11 environment, the environment used for the public distribution's offline validation. Select that same environment as the notebook kernel.

From this workflow directory, install the offline dependency and a Jupyter interface:

```shell
python -m pip install -r requirements.txt
python -m pip install jupyterlab ipykernel
```

[requirements.txt](requirements.txt) specifies `python-pptx>=1.0.2,<2`; its dependencies are installed by pip. Jupyter and provider SDKs are separate from that requirements file. The notebook does not install packages, load `.env` files, or require an editable package installation.

For live Gemini translation and OpenAI evaluation, also install:

```shell
python -m pip install google-genai openai
```

Install Claude's SDK only if using fallback generation:

```shell
python -m pip install anthropic
```

Provider accounts, API access and suitable model access are required for enabled live stages. Confirm the stage-local model settings before execution; saved model IDs do not guarantee availability in your account. PowerPoint is needed for final visual inspection, not for the Python extraction/reconstruction process.

### Credentials

Supply credentials through the environment inherited by the Jupyter kernel:

| Variable | Used for |
| --- | --- |
| `GEMINI_API_KEY` | Gemini primary translation. |
| `OPENAI_API_KEY` | Primary and fallback semantic evaluation. |
| `ANTHROPIC_API_KEY` | Optional Claude fallback generation. |

For example, in PowerShell before starting Jupyter, replacing the placeholders with your own keys:

```powershell
$env:GEMINI_API_KEY = "your-gemini-api-key"
$env:OPENAI_API_KEY = "your-openai-api-key"
# Set only when using Claude:
$env:ANTHROPIC_API_KEY = "your-anthropic-api-key"
```

For this notebook, use **`GEMINI_API_KEY`**. The Google Gen AI SDK can discover `GOOGLE_API_KEY` or `GEMINI_API_KEY` when constructing a client without an explicit key. This workflow instead checks `GEMINI_API_KEY` and passes its value explicitly, so `GOOGLE_API_KEY` alone does not satisfy the notebook's check. There is no need to set both.

Offline extraction, QC and disabled-stage previews require no provider credentials. Keep keys out of notebooks and version control. Enabled stages send extracted text and relevant translation context to their providers; PowerPoint XML reconstruction stays local. Checkpoints and reports can contain source text and raw responses, so treat generated runs as private data.

## Quick start

1. **Open the notebook.** Open `pptx_workflow.ipynb` in your preferred Jupyter-compatible environment, such as VS Code or JupyterLab, and select a Python kernel with the required dependencies installed. The saved input is the 50-slide English presentation [Help_I_Dont_Understand_The_Bible.pptx](pptx_files/Help_I_Dont_Understand_The_Bible.pptx), with English as source and Traditional Chinese as target. Review the language settings and leave `run_manifest_path=None` for a fresh run.
2. Leave all six execution flags `False` and `write_reports=False`, then use Run All. With the bundled English presentation present, this extracts/classifies text, builds QC details and previews the translation plan without provider calls or run exports. Inspect `qc_reports` beyond the printed totals.
3. To translate the selected presentation, configure credentials and accessible models, then enable and execute each required stage in order as described below. Disable each execution flag after use.
4. Review the final reports and open the generated presentation in PowerPoint before sharing it.

For a smaller alternative, set `input_path=project_root / "examples" / "stage1_sample.pptx"` to use [the six-slide synthetic sample](examples/stage1_sample.pptx). It contains text boxes, mixed formatting, a table, grouped text, Bible-reference examples, numbers and repeated labels. It contains no SmartArt and does not demonstrate every production capability. [examples/create_sample.py](examples/create_sample.py) can recreate a missing sample; it refuses to overwrite an existing file.

For your own presentation, place a `.pptx` file in `pptx_files/` (or another private local folder) and change the `input_path` argument inside the notebook's `WorkflowConfig(...)`, for example:

```python
input_path=project_root / "pptx_files" / "your_presentation.pptx",
```

Keep inputs outside `output_root`. Start a fresh run when changing source content, languages or incompatible stage settings.

## Real-world demonstration

The bundled 50-slide Bible-study presentation uses public-domain World English Bible (WEB) quotations and demonstrates English-to-Traditional-Chinese translation. Formatting examples include mixed emphasis and styled runs, hyperlinks, tables, and bullets. Paired examples show explicit line breaks stored in the presentation versus automatic wrapping determined by text-box width and fonts; translated text may wrap differently.

| Presentation | Purpose |
| --- | --- |
| [English source](pptx_files/Help_I_Dont_Understand_The_Bible.pptx) | Default notebook input; 220 source units, including 209 translated units and 11 preserved units. |
| [Automated Chinese reconstruction](examples/Help_I_Dont_Understand_The_Bible__zh-Hant__20261103T183824Z__37bc2f1e/Help_I_Dont_Understand_The_Bible__zh-Hant__v001.pptx) | Untouched automated result, including its translation and layout limitations. |
| [Manually edited Chinese presentation](examples/Help_I_Dont_Understand_The_Bible__zh-Hant__20261103T183824Z__37bc2f1e/Help_I_Dont_Understand_The_Bible__zh-Hant__v001_manual_edit.pptx) | Separate copy with manual text and layout edits; these edits are not part of the automated result. |

The completed run translated all 209 eligible paragraphs successfully with Gemini (100%); OpenAI accepted all 209 (100%). Claude fallback was not needed. Finalization recorded complete coverage, and editable PowerPoint reconstruction passed structural validation with no unresolved units. Private checkpoints, provider responses, manifests and run reports are not bundled.

Visual and linguistic review remains important: semantic acceptance and structural validation do not guarantee correct wrapping, alignment or terminology. Compare the untouched automated result with the manually edited copy to see where human edits remain useful. See [licenses and attributions](#licenses-and-attributions) before redistribution.

## Configuration and execution

The checkout directory may have any name. Launch from the workflow root or its parent when exactly one matching checkout is present. For another working directory or ambiguous discovery, set `PPTX_WORKFLOW_ROOT` to the directory containing this notebook and `pptx_translation/` before starting the kernel. Notebook-relative input, output and manifest paths are resolved against `project_root`.

Important notebook settings are:

| Setting | Purpose |
| --- | --- |
| `input_path`, `output_root` | Source presentation and generated-artifact root. |
| `source_language`, `target_language` | Translation languages; must match the selected run on resume. |
| `include_smartart` | Include SmartArt data in extraction; does not imply universal reconstruction support. |
| `write_reports` | Optionally persist detailed extraction/QC reports inside the run. |
| `primary_domain_context` | Optional supplementary Gemini context, saved as part of request identity. It does not alter evaluator or Claude prompts. |
| `primary_model_name`, `primary_temperature`, `primary_max_units`, `primary_max_chars` | Primary model, sampling and batch-size controls. The saved primary batch bounds are 80 units and 12,000 characters. |
| `evaluation_model_name`, `fallback_model_name` | Stage-local evaluator and optional Claude models. |
| `fallback_evaluation_model_name` | `None` inherits the recorded primary evaluator; a model string overrides it. |

Styled-span preservation is always enabled in the production notebook.

### Optional output location

The portable default is `project_root / "outputs"`. To store runs, checkpoints, reports and deliverables elsewhere, set the existing `output_root` argument inside `WorkflowConfig(...)`, for example on Windows:

```python
output_root=Path(r"C:\llm_translation_outputs\pptx"),
```

When the checkout is in OneDrive or another cloud-synchronized folder, a nonsynchronized local output directory may reduce interference with frequent checkpoint-file replacements. Git ignore rules control Git tracking, not cloud synchronization.

New runs use the configured output root. Resuming a run uses the selected manifest's existing run directory; changing `output_root` does not relocate or migrate saved runs. Select the exact saved `run.json` to continue that run.

### Stage controls

| Execution flag | When to enable |
| --- | --- |
| `enable_primary_translation` | After reviewing extraction and the complete Gemini plan. |
| `enable_evaluation` | After finishing the intended Gemini processing. |
| `enable_fallback_translation` | Only when eligible primary semantic rejections remain. |
| `enable_fallback_evaluation` | After structurally validated Claude candidates are available. |
| `enable_finalization` | After reviewing the completeness preview; exports or reuses offline reports. |
| `enable_reconstruction` | After a saved finalization snapshot exists; writes or revalidates offline output. |

Run the relevant stage's settings, preview and execution cells in order. No flag enables another stage. Checkpoint handoffs come from the active manifest; do not manually edit derived checkpoint variables. After upstream progress, rerun downstream preview cells to refresh their displayed plans.

All flags are saved off. The four provider submission limits—`primary_batch_limit`, `evaluation_batch_limit`, `fallback_batch_limit` and `fallback_evaluation_batch_limit`—are saved as `None`, allowing all remaining eligible work when enabled. Use a finite nonnegative integer to cap submissions for one invocation, or `0` to prepare/save a plan without submissions. Primary limits include validation retries; they count submissions rather than paragraphs. SDK retries and interrupted attempts can make network-call counts differ from saved workflow attempts.

Gemini skips structurally validated paragraphs and preserves its maximum three recorded attempts per paragraph across restart. A structurally validated candidate still needs semantic evaluation. OpenAI returns `passed=true` with empty feedback or `passed=false` with a brief substantive reason; faithful wording variation is allowed. Numeric warnings are evidence to evaluate, not automatic semantic failures.

Claude is optional and receives the source, rejected candidate and evaluator feedback. Generation/validation failures, missing evaluations and evaluator errors do not qualify as semantic rejections. A rejected Claude candidate does not trigger an automatic second correction round. Both accepted and rejected evaluator decisions are completed work; operational error batches retry only on explicit resumption.

## Outputs and resumption

Enabling Gemini for a fresh run creates a unique directory under `outputs/runs/` and prints its exact `run.json` path. Save that path.

```text
outputs/runs/<source>__<language>__<UTC-start>__<id>/
├── run.json
├── RUN_SUMMARY.md
├── checkpoints/                  # Provider records, finalization JSON, journals
├── reports/                      # CSV/Markdown reviews, optional extraction QC
│   └── reconstruction__a001/     # Attempt validation and diagnostic artifacts
└── final/                        # Passing, revalidated PPTX deliverables
```

Finalization produces hash-bound `finalization_<token>.json`, `unit_summary_<token>.csv` and `manual_followup_<token>.md` files. Passing deliverables use `<source>__<language>__v001.pptx`, with increasing versions within the run. Failed/manual-review attempts stay diagnostic and do not consume a deliverable version. A structurally passing deliverable can still have partial translation coverage; check the associated report and `RUN_SUMMARY.md`.

Compatible finalization/reconstruction artifacts are verified and reused. Original inputs and earlier deliverables are not overwritten. The summary links current outputs and records coverage, decisions, errors and structural validation; it does not automatically record human visual approval.

### Ordinary restart and resume

1. Restart the kernel and set **only `run_manifest_path`** to the exact saved manifest in the configuration cell. A project-relative path or an absolute path may be used.
2. Keep the source file, languages, extraction settings and stage request settings compatible with that run. Run All with execution flags off to inspect saved status.
3. Enable and rerun the required stage. Completed work is skipped and pending/error work follows that stage's resume policy. Disable its flag afterward.
4. Continue downstream stages explicitly. Changed upstream hashes invalidate dependent handoffs while retaining previous branch files; the next enabled downstream stage creates a compatible branch.

Submission caps may change during resume. Model, prompt, temperature, domain context and batching changes are subject to strict stage compatibility checks. To deliberately start another run, set `run_manifest_path=None` and rerun configuration before enabling Gemini; rerunning the Gemini execution cell alone continues the current active run.

Do not edit checkpoints or move a saved run expecting its recorded source-path identity to change automatically. Hash checks reject missing, modified or mismatched inputs. A crash before durable response saving may require repeating a provider request; exactly-once remote execution is not guaranteed. Remove a stale `.lock` only after confirming its writer has stopped.

### Explicit offline primary recovery

Specialized recovery is separate from ordinary resume. `active_run.recover_primary()` can revalidate the exact saved third response of narrowly eligible older checkpoints exhausted solely by strict numeric-marker validation. `active_run.recover_fallback()` handles a narrowly defined older Claude checkpoint with a single complete saved failed response and eligible validation issues.

These operations make no provider requests, preserve the original records, verify ancestry and publish separate recovery artifacts. They are not general checkpoint repair or migration, and recovered candidates still require OpenAI semantic evaluation. Consult the eligibility checks in [primary_recovery.py](pptx_translation/primary_recovery.py) and [fallback_recovery.py](pptx_translation/fallback_recovery.py) before using them; they are unnecessary for normal fresh-run operation.

## Supported features and limitations

| Feature | Scope |
| --- | --- |
| Text boxes, grouped shapes and tables | Paragraph-level extraction and targeted editable reconstruction. |
| Styled text | Ordered text spans preserve local run formatting; structural spans retain tabs, soft breaks and fixed whitespace. Ordinary spaces within text spans follow the target-language rules; significant Unicode whitespace and directional controls remain protected. |
| Hyperlinks | Supported styled replacements retain linked-run formatting and relationships. Unsafe ordinary paragraph replacements are retained for manual review. |
| SmartArt | Conservative native editable support for `IconCircleLabelList` diagrams with validated identities, matching data/drawing-cache text and supported label structure. Labels must have one paragraph and one or two ordinary text runs. A matching layout name alone does not establish support. |
| Unsupported SmartArt/layout structures | May be extracted but are not automatically rebuilt or repaired. Unsupported or unsafe targets remain unchanged with review information, or fail validation/planning when required guarantees cannot be established. |
| Charts, picture-embedded text, notes, masters and layouts | Outside normal text extraction/translation; inspect and handle manually. |
| Fonts, overflow, layout and RTL | No automatic font replacement, geometry changes, fitting, wrapping overrides or right-to-left layout repair. |

Translation coverage refers to extracted source units, not proof that every visible object or unextracted part of a presentation was translated. Source preservation also preserves limitations of the original file. Supported formatting does not guarantee that translated wording has the same visual fit.

## Validation and manual review

Assess three separate questions:

1. **Structural integrity:** Python checks source hashes, unit identities, expected text, styled spans, run formatting, relationships/hyperlinks, slide order/visibility, unchanged non-target parts and supported SmartArt cache synchronization. Reconstruction reopens the output to check text and source correspondence.
2. **Semantic acceptance and completeness:** OpenAI evaluates each paragraph's meaning and required literals. Finalization accounts for every extracted unit and reports unresolved work. Model acceptance does not guarantee linguistic accuracy.
3. **Human approval:** Open the output in PowerPoint and check fonts, emphasis, hyperlinks, clipping, wrapping, text expansion, RTL behavior and untranslated/unsupported content. Have an appropriate reviewer check meaning and terminology before distribution.

An incomplete finalization can be exported as a partial translation. `FLAGGED FOR MANUAL FOLLOWUP:` identifies explicit reviewer-marked cases in reports; it is not automatically attached to every unresolved unit, and no visible slide annotations are added. Review counts and unresolved records even when the structural report passes.

## Testing

The workflow has been validated through automated regression tests and live translations of real-world PowerPoint presentations. Structural validation checks the reconstructed presentation, but human review remains important for translation quality, typography, and slide layout.

The complete public offline suite passed **234 tests**, including the six setup-prefix tests, during distribution acceptance. This is a recorded acceptance result, not a claim that every subsequent edit has rerun the full suite. The separate live demonstration above adds real-document evidence; recorded provider responses, migration/equivalence checks and full executable notebook integration tests are not included. The synthetic SmartArt tests validate mapping and reconstruction consistency, not PowerPoint rendering of every diagram.

## Repository structure

| Path | Purpose |
| --- | --- |
| [pptx_workflow.ipynb](pptx_workflow.ipynb) | Guided configuration, previews and independently enabled execution stages. |
| [pptx_translation/](pptx_translation/) | Local production package used by the notebook. |
| [requirements.txt](requirements.txt) | Offline runtime dependency. |
| [pptx_files/](pptx_files/) | Bundled English source and conventional location for private inputs; only the approved English file is Git-visible. |
| [examples/](examples/) | Synthetic presentation and generator, plus automated and manually edited Chinese demonstrations. |
| [tests/](tests/) | Portable offline regressions, synthetic helpers and reviewed checkpoint vectors. |
| `outputs/` | Generated local runs, reports and deliverables; ignored by Git. |

```text
pptx/
├── pptx_workflow.ipynb
├── pptx_translation/             # 21 production Python modules
├── pptx_files/
│   └── Help_I_Dont_Understand_The_Bible.pptx
├── examples/
│   ├── stage1_sample.pptx
│   ├── create_sample.py
│   └── Help_I_Dont_Understand_The_Bible__zh-Hant__20261103T183824Z__37bc2f1e/
│       ├── Help_I_Dont_Understand_The_Bible__zh-Hant__v001.pptx
│       └── Help_I_Dont_Understand_The_Bible__zh-Hant__v001_manual_edit.pptx
├── tests/                       # Offline tests, support and synthetic fixtures
├── requirements.txt
├── README.md
└── .gitignore
```

The scoped `.gitignore` permits exactly these four bundled presentations while excluding other PPTX inputs, credentials, generated outputs, checkpoints and caches. Private production run artifacts are not bundled.

## Licenses and attributions

The workflow software is covered by the repository's [MIT License](../../../LICENSE.md). Original illustrations by Shawn Handran are licensed under [Creative Commons Attribution–NonCommercial–ShareAlike 4.0 International (CC BY-NC-SA 4.0)](https://creativecommons.org/licenses/by-nc-sa/4.0/); credit Shawn Handran / basicsofthebible.org. These terms apply to the author's original illustrations, not to third-party maps or Scripture quotations.  

### Wikimedia Commons maps

The maps retain their separate [Creative Commons Attribution–ShareAlike 3.0 Unported (CC BY-SA 3.0) license](https://creativecommons.org/licenses/by-sa/3.0/), including attribution and share-alike requirements for adaptations; they are not relicensed under MIT or CC BY-NC-SA 4.0.

| Slide | Map and source | Attribution |
| --- | --- | --- |
| 18 | [12 Tribes of Israel](https://commons.wikimedia.org/wiki/File:12_Tribes_of_Israel_Map.svg) | Kordas, user יוסי, Janz, and derivative contributor Richardprins, as specified by Wikimedia Commons. |
| 22 | [Kingdoms Around Israel, 830 BCE](https://commons.wikimedia.org/wiki/File:Kingdoms_around_Israel_830_map.svg) | FinnWikiNo and derivative contributor Richardprins, as specified by Wikimedia Commons. |

The source maps are embedded as raster images and sized/positioned within the slides. The maps retain their source credits in the English and Chinese presentations; surrounding slide text was translated. Earlier image edits, cropping and the exact Commons revision used have not been independently established, so this notice does not assert that the embedded maps are unmodified originals.  

### Scripture acknowledgments

Scripture quotations in the English demonstration are from the [World English Bible (WEB)](https://worldenglish.bible/), a public-domain Bible translation. The Chinese presentations contain translations derived from those English quotations; they are not official WEB translations. Retained WEB labels identify the English source, not an authorized Chinese edition.

The WEB public-domain status applies to its Scripture text; the separate artwork and map licenses above still apply.
