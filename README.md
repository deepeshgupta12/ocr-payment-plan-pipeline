# Payment Plan → SEO/AEO Content Pipeline

Ingests a project payment plan / price sheet (image or PDF), extracts it via
an OpenAI vision model into a strict JSON structure, validates it
deterministically, saves the canonical record to the project datacenter, and
generates PDP-standard SEO/AEO content — with a post-generation numeric
guardrail.

## Pipeline

```
Ingest (PDF/image)
  └─> Extract + Structure   (OpenAI vision, Structured Outputs, verbatim rules)
        └─> Validate         (deterministic per-plan-type rules, ±1 INR tolerance)
              └─> Save JSON  (canonical record in project datacenter)
                    └─> Generate content   (charter voice, structure-aware)
                          └─> Reconcile numbers (every number must trace to JSON)
```

## Setup

```bash
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
```

Put source documents (payment plans / price sheets) in `input_data/`.

## Usage

```bash
python main.py input_data/price_sheet.png --project-id divine-green-leaf
```

Outputs — each analysis produces a self-contained folder,
`./output/<project-id>/`, with one file per stage so every step is
independently inspectable:

| File | Stage | Contents |
|------|-------|----------|
| `01_image_analysis/` | Ingest | Rendered page image(s) the vision model saw (`page_N.png`), a `manifest.json`, and `visual_analysis.txt` — an AI description of the document's layout and capture quality |
| `02_extracted_data.json` | Extract | Raw, verbatim model extraction (pre-validation, no normalisation) |
| `03_structured_data.json` | Structure + Validate | Extraction reorganised for review, with the deterministic validation report attached |
| `04_record.json` | Save | Copy of the canonical datacenter record |
| `05_seo_aeo_content.json` | Generate | Generated content package (`seo_prose_html` as AEO question/answer `<h3>` blocks that double as the on-page FAQ, `payment_schedule_table_html`, `pricing_table_html`, `meta_description`) + reconciliation report |
| `06_seo_aeo_content.html` | Generate | Standalone, browsable HTML rendering of the content package (prose plus the standalone table blocks), with a status and reconciliation banner. Open it directly in a browser to preview the content |

The canonical record is also written to
`./datacenter_store/<project-id>.payment_plan.json` (the production
persistence target).

If validation routes the record to review, content generation is skipped and
`05_seo_aeo_content.json` is not produced. After human review, re-run with
`--force-generate`.

## Key design decisions (per spec discussions)

1. **No fixed plan structure assumed.** `payment_plans` is always an array;
   milestones carry a `value_type` discriminator (`percentage` / `amount` /
   `percentage_plus_amount` / `text`); every plan has a `notes` escape hatch.
   The extraction prompt forbids normalisation — extract what is printed.
2. **Vision model with Structured Outputs (strict mode).** Parse failures and
   schema drift are impossible by construction. Multi-page PDFs are sent in a
   single request so split tables are never read as two partial plans.
3. **Document-level flexibility.** `document_type` distinguishes pure payment
   plans from price sheets; price sheets carry a `pricing` block with
   per-unit-type entries (duplicate unit labels with different SBAs are
   distinct entries). The `pricing` block also captures, when present:
   `inclusions` (an "Above Prices Inclusive Of" list), `additional_charges`
   (levied on top of the quoted price — per-sq-ft rates like lease rent, or
   flat sums like club membership — kept separate from the agreement-value
   `components`), and `location_charges` (a floor/facing PLC table). Areas are
   stored as printed with their unit (`sba_unit` / `carpet_unit`, e.g. `Sq.ft`
   vs `Sq.mt`) and never converted. When price varies by floor (and optionally
   by unit position within a tower), it is captured as a `floor_pricing` matrix
   (floor bands x columns) rather than a single per-unit value; a single
   combined charge that covers several items records the shared total once with
   a `covers` list. `location_charges` is an array, so several PLC tables (a
   view/facing table and a floor table) are kept separate. Each additional
   charge carries a `charge_type` (`per_sqft`, `per_sqft_per_month`, `per_kw`,
   `per_kva`, `flat`, `conditional`, `text`) and a `when_due`
   (`one_time` / `at_booking` / `at_possession` / `recurring` / `unspecified`),
   so a possession-charges block or a recurring maintenance rate is modelled
   faithfully. When the basic rate differs by unit or named category (e.g.
   `Diamond & Emerald` vs `Pearl…`), each `unit_types` entry carries its own
   `rate_psf`. A `floor_pricing` cell can hold a full cost `breakdown` (Agreement
   Cost, Stamp Duty, Registration, GST, Total Cost) when the sheet stacks costs
   per floor band; a separate balcony area is kept in `balcony_sqft` /
   `balcony_unit`; a per-unit inventory (tower, floor, unit address, basic price)
   is captured in `unit_inventory`; and a charge shown as "Free" is recorded with
   `amount` 0 so it reads as complimentary rather than missing. When a price is a
   low-to-high RANGE that varies by building, configuration, view (e.g. Garden vs
   Expressway) and/or payment plan across named clusters, each cell is captured
   in `price_ranges` (`price_low` / `price_high` / `price_unit` / verbatim
   `price_text`) — even when the table is captioned as a "payment plan", the
   budgets are treated as pricing so nothing is dropped, and the content renders
   one complete table per payment plan, built deterministically from
   `price_ranges` (a safety net over the prompt) so no budget row is ever
   summarised away. The milestone schedule, the additional-charges table and the
   buyer-relevant "Good to know" conditions block are rebuilt the same way
   (directly from the record), so every block carries a description, highlight
   bullets AND its complete table — the model writes the prose, deterministic
   code guarantees the rows. A per-unit `rate_psf` (Bren's per-variant rate, Sikka's
   per-category rate) is used by the arithmetic reconciliation in preference to
   the sheet-wide rate. Document-level
   legal text is captured verbatim in a top-level
   `terms_and_conditions` array (kept in the record for reference and
   deliberately not fed to content generation, so legalese is never published).
   Because real buyer charges and pricing conditions are often buried in that
   T&C/notes prose (maintenance, legal, APF, "the total is inclusive of GST",
   "Govt taxes additional", "Stamp Duty extra", advance payments at possession),
   the extraction prompt structures them into `additional_charges` /
   `pricing.conditions`, and a deterministic post-extraction pass
   (`enrich_conditions_from_terms`) guarantees pricing-relevant T&C sentences are
   copied into `pricing.conditions` even when the model misses them — so no
   buyer-facing charge or condition is silently dropped. Legal boilerplate
   (cancellation, documents, cheque payee, account/RERA numbers) is excluded.
   All of these fields are additive: documents without them (e.g. a plain RPSF
   price sheet) are unaffected.
4. **Validation = "coherent enough to auto-publish?"** Sum-to-100 applies only
   to fully-percentage plans; pricing arithmetic reconciles with ±1 INR
   rounding tolerance; the model *proposes* plan type and the validator
   *confirms* it structurally — mismatches route to review.
5. **Datacenter record is canonical.** Content generation reads the saved
   record, never the raw extraction, so content can be regenerated on charter
   or format changes without re-touching the source document.
5a. **No-dash content guarantee.** The content charter forbids hyphens and
   dashes; on top of that prompt rule, a deterministic post-generation pass
   strips any dash that survives (e.g. a reproduced label like "Power Back-Up"),
   operating only on HTML text nodes so tags, attributes and numbers are never
   touched. The verbatim original is preserved in the extraction record.
6. **Post-generation numeric reconciliation** replaces the safety that
   variable injection provides elsewhere on the PDP: every number in the
   generated content must exist in the source JSON (exact lakh/crore
   restatements of exact figures allowed); violations flag the content as
   review_required and block publish.
7. **Derived signals stay internal.** Inferred city (utility-board hints) and
   construction status are stored as signals for master-data cross-checks —
   never published as facts.

## Production integration points

- `payment_pipeline/datacenter.py` — replace `FileDatacenterClient` with the
  real datacenter API client (same two-method interface).
- `payment_pipeline/content_generation.py` — paste the production
  **Senior Real Estate Writer voice charter v2.0** into `VOICE_CHARTER`, and
  align the HTML rules block with the PDP HTML/CSS standards document.
- `payment_pipeline/config.py` — models, DPI, tolerances, page caps.
