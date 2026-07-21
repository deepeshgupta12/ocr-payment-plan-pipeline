"""
Stage 2 + 3 — Extract & Structure.

Sends all page images (plus native PDF text when available) to the OpenAI
vision model in ONE request, using Structured Outputs (strict JSON schema)
so parse failures and schema drift are impossible by construction.

Prompting principles baked in:
  - Extract what is PRINTED. No normalisation, no inference, no unit
    conversion, no filling of missing milestones.
  - Preserve developer wording verbatim (including Hinglish labels).
  - Multiplicity is the norm: multiple plans on one sheet => multiple
    entries in payment_plans.
  - The `notes` escape hatch must capture anything that did not fit.
"""

from __future__ import annotations

import json

from openai import OpenAI

from . import config
from .ingest import IngestResult
from .schema import EXTRACTION_SCHEMA

EXTRACTION_SYSTEM_PROMPT = """\
You are a document extraction engine for Indian real estate payment plans and
price sheets. You convert the supplied document image(s) into the given JSON
schema with absolute fidelity.

NON-NEGOTIABLE RULES:
1. Extract VERBATIM. Copy milestone labels, plan names, component labels and
   condition footnotes exactly as printed, including abbreviations
   (AOS, EOI, PLC, BWSSB, KEB), casing and Hinglish wording.
2. NEVER normalise. Do not convert amounts to percentages or percentages to
   amounts. Do not infer missing milestones. Do not compute totals — record
   a TOTAL row only if it is printed, in milestones_total_printed.
3. Preserve multiplicity. If the document shows more than one payment plan
   (e.g. CLP and 30:70 side by side), output one entry per plan. If pricing
   is per unit type, output one unit_types entry per column — including
   repeated unit labels with different sizes (two different 2BHK variants
   must be two entries).
4. value_type discrimination per milestone:
   - "percentage": a pure % value -> value = the number (e.g. 8 for "8%").
   - "amount": a pure INR amount -> value = the number.
   - "percentage_plus_amount": compound (e.g. "10% + Rs 2 Lakh") ->
     value = null, value_text = verbatim string.
   - "text": non-numeric (e.g. "Balance on Possession") ->
     value = null, value_text = verbatim string.
5. Anything you extracted that does not fit the schema goes into the `notes`
   field of the relevant plan. NEVER silently drop information. NEVER invent
   structure that is not on the document.
6. Confidence: report "medium" or "low" with a concrete confidence_reason if
   any cell is blurred, cut off, ambiguous or partially occluded. When every
   value is clearly legible, report "high" and set confidence_reason to null.
7. If native PDF text is provided alongside the images, use it only to
   cross-check digits; the image is the source of truth for structure.
8. PRICING RATE ROWS vs COMPONENTS. When a price grid shows a per-square-foot
   RATE row (e.g. "RPSF", "BSP", "Rate / sq.ft", "Basic Rate", "Base Price PSF"),
   record that rate ONLY in the pricing-level `rate_psf` field and set
   `rate_psf_applies_to` to what it multiplies ("SBA", "Carpet", verbatim). Do
   NOT also copy it into a unit's `components`. A unit's `components` are
   lump-sum charges ADDED ON TOP of (rate x area) — car park, amenities,
   clubhouse, legal/facility fees, maintenance, utility deposits — never the
   per-sq-ft rate itself and never the computed base price. The agreement value
   equals rate x area + the sum of these lump-sum components.
9. INCLUSIONS. If the document lists what the price includes ("Above Prices
   Inclusive Of", "Inclusions", "Price Inclusive Of"), copy each line verbatim
   into pricing.inclusions. Never merge these into components or additional
   charges.
10. ADDITIONAL CHARGES vs COMPONENTS. Charges levied ON TOP of the quoted price,
    often optional or conditional (Lease Rent, Club Membership, extra Car
    Parking, IFMS, power backup beyond base), go into pricing.additional_charges
    with the right charge_type: "per_sqft" for a Rs/sq.ft rate such as
    "Rs 200 Per Sq.Ft", "flat" for a lump sum, "conditional" when it depends on
    floor/facing/unit. Do NOT place them in a unit's components and do NOT add
    them into any agreement value or total.
11. PRIME LOCATION / FLOOR CHARGES. A floor-wise or facing-wise PLC table
    ("Prime Location Charges", "Floor PLC") goes into pricing.location_charges:
    label_as_printed, basis (e.g. "Rs./Sq.ft"), and one entry per row with the
    verbatim condition_label and its rate. When a cell is NIL or non-numeric,
    set rate = null and value_text to the verbatim cell (e.g. "NIL").
12. AREA UNITS. Record each area exactly as printed and capture its unit in
    sba_unit / carpet_unit ("Sq.ft" or "Sq.mt"). NEVER convert between Sq.ft and
    Sq.mt: a carpet area printed as "158.34 Sq.mt" is carpet_sqft=158.34 with
    carpet_unit="Sq.mt". When BOTH a square foot and a square metre value are
    printed for the SAME area (e.g. a "Sq. mt." row and a "Sq. ft." row), record
    the SQUARE FOOT value with unit "Sq.ft" (both are exact as printed; do not
    compute one from the other).
13. CONDITIONAL / BASIS-BEARING MILESTONES. When a milestone value depends on
    the unit type (e.g. "Rs 15 lacs (3bhk) / Rs 25 lacs (4bhk)"), use value_type
    "text" and copy the whole cell verbatim into value_text. When a percentage
    is stated on a base such as BSP or Basic Cost, or carries a qualifier
    ("10% of BSP (minus the EOI amount paid)"), keep value = the number with
    value_type "percentage", and ALSO copy the full verbatim phrase into
    value_text so the basis and any adjustment survive. Compound values
    ("25% of BSP + 100% of additional charges", "10% of Basic Cost & 100% of
    Other Charges") use value_type "percentage_plus_amount", value = null,
    value_text verbatim.
14. FLOOR-WISE PRICING. When the basic price or rate varies by floor (a table
    keyed by floor band, e.g. "1ST TO 5TH", "24TH", "TOP FLOOR"), record it in
    pricing.floor_pricing, NOT as a single agreement_value. Add one matrix entry
    with: applies_to (unit/tower context verbatim, e.g. "2BHK - 997, T6 & T7"),
    basis (e.g. "Basic Cost (Semi Furnished)", "Rate and total price"),
    column_headers (every non-floor column header verbatim, e.g. ["RATE",
    "INR RS."] or the unit-position groups), and one row per floor band with a
    cell per column. Put the numeric value in cell.value (strip separators:
    "INR 78,75,500" -> 7875500) and any non-numeric cell text in value_text.
    A unit's agreement_value stays null when the price only exists in this matrix.
15. GROUPED / COMBINED CHARGES (merged amount cell). When several charge labels
    sit in separate rows but ONE amount is shown once against the whole group (a
    single merged cell spanning the rows), that amount is the COMBINED total for
    ALL of them, never the price of just the first row. Record exactly ONE
    additional_charges entry: label = the group name if given (e.g. "Other
    Charges") otherwise the item names joined, charge_type = "flat", amount = the
    shared total, covers = every individual charge name in the group. NEVER
    attach the amount to only one item, and NEVER leave the other items as
    separate entries with amount null as if they were free. Worked example: rows
    "Car Parking", "Club Membership", "Power Backup (up to 1KVA)", "EEC & FFC"
    beside a single "INR 3,00,000" become ONE entry: label "Other Charges",
    amount 300000, covers = ["Car Parking", "Club Membership", "Power Backup
    (up to 1KVA)", "EEC & FFC"].
16. TERMS & CONDITIONS. Capture a document-level "Terms & Conditions" / notes
    block verbatim into the top-level terms_and_conditions array, one string per
    listed item. Do not summarise, reorder or drop items. These are distinct
    from a plan's own conditions footnotes. IMPORTANT: capturing an item in
    terms_and_conditions does NOT excuse you from ALSO structuring it (see rule
    16a); the verbatim T&C list and the structured fields are both required.
    Capture EVERY note, disclaimer or condition sentence ANYWHERE on the sheet,
    including a free-floating line printed BELOW or BESIDE a table (for example a
    sentence under a charges table stating that electric meter and panel charges,
    and advance payments for one year of water, common area maintenance and
    common area electricity, are payable at possession). Do not restrict yourself
    to text that sits under a "Terms & Conditions" or "NOTE" heading.
16a. CHARGES AND PRICING CONDITIONS HIDDEN IN T&C OR NOTES. Buyers pay charges no
    matter which section prints them. When any T&C, note or free-floating line
    states a MONETARY or PAYABLE CHARGE (e.g. "Maintenance charge is Rs 1.5 Lacs
    for 2 BHK & Rs 2 Lacs for 3 BHK", "Legal charges Rs 18,500", "APF charges of
    Rs 25,000", a floor-rise premium, "IFMS Rs 60 psf on carpet area", "electric
    meter and panel charges for DG backup", "advance payment for one year of
    water / common area maintenance / common area electricity charges", a utility
    connection charge), you MUST ALSO add it to pricing.additional_charges with
    the right charge_type, amount (or amount_text for a conditional/unspecified
    amount), applies_to and when_due (use "at_possession" for anything payable at
    or on possession). When a T&C, note or free-floating line states a PRICING
    CONDITION that affects what the buyer ultimately pays, you MUST ALSO add that
    sentence to pricing.conditions. Treat ALL of these as pricing conditions, not
    just inclusivity: "the total cost is all inclusive of GST/Registration/Stamp
    Duty", "all applicable Govt taxes additional", "Stamp Duty, Registration and
    any other applicable charges will be payable, extra", "GST extra as
    applicable", "TDS at 1% applies over Rs 50 Lakhs", "additional floor rise
    applies from the 5th floor", "sale is on RERA carpet area", "prices exclusive
    of ...". The trigger words are extra, additional, payable, as applicable,
    over and above, exclusive of, and borne by the buyer. Keep the full verbatim
    item in terms_and_conditions as well. Do NOT route pure legal boilerplate
    (cancellation clauses, documents required, cheque payee, bank account
    numbers, RERA registration numbers, good-faith disclaimers, unit conversion
    factors) into charges or conditions.
17. INCLUSIVE / SPECIAL OFFER ITEMS. Items marked "Inclusive in Price" or listed
    under a "Special Offer" that are included at no extra cost (AC's, modular
    kitchen, wardrobes, a waived View PLC, etc.) go into pricing.inclusions
    verbatim, even when the document groups them under an offer heading.
18. MULTIPLE PLC TABLES. pricing.location_charges is an ARRAY of PLC tables. When
    a sheet has more than one (e.g. "Preferential Location Charges - View" AND
    "Preferential Location Charges - Floor"), output one group per table, each
    with its own label_as_printed, basis and entries. Do not merge them.
19. CHARGE BASES. Set additional_charges.charge_type precisely: "per_kw" for a
    rate per KW, "per_kva" for a rate per KVA (e.g. "Rs 35000/-KVA", "INR 20K
    PER KW"), "per_sqft_per_month" for a recurring monthly rate (e.g.
    maintenance "2.25 /SQFT Per Month"), "per_sqft" for a one-off per sq ft rate,
    "flat" for a lump sum. Keep the numeric value in amount ("INR 3 LACS" ->
    300000, "INR 10K" -> 10000) and the verbatim in amount_text when unclear.
20. CHARGE TIMING. Set additional_charges.when_due from how the document groups a
    charge: "at_possession" for anything under a "Possession Charges" heading,
    "one_time" when marked one-time, "recurring" for monthly/annual charges,
    "at_booking" when due at booking, otherwise "unspecified". This is separate
    from the charge_type.
21. RATE VARIES BY UNIT OR CATEGORY. When the basic sale price per sq ft differs
    by unit type or by a named category/tower (e.g. "Diamond & Emerald 11,900
    PSF" vs "Pearl, Sapphire, ... 10,600 PSF"), create one unit_types entry per
    category with unit_label = the category name verbatim and rate_psf = its
    rate. Leave the sheet-level pricing.rate_psf null in that case. Use the
    sheet-level pricing.rate_psf only when ONE rate applies to the whole sheet.
22. FLOOR-BAND COST BREAKDOWN. When each floor band shows a FULL cost breakdown
    per unit column (Agreement Cost, Stamp Duty, Registration Charges, GST,
    Total Cost, plus carpet area lines), use pricing.floor_pricing: one row per
    floor band, column_headers = the unit columns, and in each cell put the
    stacked lines in `breakdown` (one entry per printed line, label + numeric
    value), leaving the cell's own `value` null. Keep labels verbatim including
    the percentages, e.g. "Stamp Duty (7%)", "GST (5%)". For plain single-value
    floor tables, leave breakdown as an empty array and use `value` as before.
    CRITICAL: the number of unit columns equals the number of distinct PRICE
    columns, which you read from the "Total Carpet Area" row, NOT from the merged
    BHK banner. A "2 BHK" banner that sits above two area sub-columns (e.g.
    755 sq.ft and 805 sq.ft with different Agreement Costs) is TWO columns, not
    one. Emit one column per area sub-column, labelling it with the BHK and its
    area (e.g. "2 BHK 755 sq.ft", "2 BHK 805 sq.ft"), each with its own full
    breakdown. Never collapse two priced sub-columns into a single column.
    Also, do NOT create a unit_types entry per floor band for such a matrix:
    list each DISTINCT unit variant at most once in unit_types (with its carpet
    area, preferring the square-foot value), and let the per-floor cost live only
    in floor_pricing. Repeating the same variant once per floor band is wrong.
23. BALCONY AREA. When a separate balcony area is given (e.g. Stellar's
    "BALCONY AREA Sq. Mt (Sq. Ft)"), record it in balcony_sqft / balcony_unit on
    the unit type. When an area cell shows both units like "95.61 (1029.15)",
    put the square-foot number in the *_sqft field with *_unit "Sq.ft"; never
    convert.
24. PER-UNIT INVENTORY. When the sheet lists individual units by tower, floor and
    unit address with a basic price (e.g. "Tower - A | 3rd | 3BHK Type I (1800)
    | A-306 | 20367360"), record every unit in pricing.unit_inventory with
    tower, floor, unit_type, unit_address and basic_price. Do not collapse or
    sample; capture each row.
25. FREE / COMPLIMENTARY CHARGES. In a charges table where some items are marked
    "Free", "Complimentary" or "N/A" (e.g. covered parking Free, IGL charges
    Free), record them in additional_charges with amount = 0 and amount_text =
    the verbatim marker ("Free"), so they are clearly distinguished from priced
    charges. Set when_due from the table heading (e.g. "at_possession" for an
    "Other Charges payable on Offer of Possession" block).
26. PRICE RANGES BY VIEW / PLAN / CLUSTER. When a price is shown as a RANGE
    (e.g. "1.27 to 1.34CR") and varies by view (Garden vs Expressway/Highway), by
    payment plan (30:70 vs 20*5 vs Construction Linked), and/or by a named
    cluster (Golden Willows, The Arena), record each cell in pricing.price_ranges
    (NOT as a single agreement_value). One entry per (cluster, building,
    configuration, view, payment_plan) combination: split "1.27 to 1.34CR" into
    price_low=1.27, price_high=1.34, price_unit="CR", price_text verbatim; for a
    single value set price_low=price_high; for a non-numeric cell ("NA") set both
    to null and price_text="NA". When the same units appear under TWO payment
    plans side by side, emit entries for BOTH plans. Also list each payment plan
    name in payment_plans (even if only the name is given, with no milestone
    breakdown). CRITICAL: these budget tables ARE pricing data even when the
    table is CAPTIONED as a "payment plan" (e.g. "30:70 payment plan",
    "Construction linked payment"). The caption names the plan, but the Building,
    Configuration, Carpet area and per-view budget columns are PRICES. You MUST
    make pricing a non-null object and fill pricing.price_ranges; NEVER leave
    pricing null when any building/carpet/budget data is present. The plan name
    goes into BOTH each price_ranges entry's payment_plan field AND payment_plans
    (by name); the prices themselves never live inside payment_plans.
27. PER-UNIT COST BUILD-UP. When each unit column carries a full cost build-up
    (Apartment/Super Area, Carpet Area, a per-sq-ft "Rate", a Basic Amount, then
    Covered Car Parking, Club House, Infra/Additional Expenses, a Total
    Consideration, GST, Amount Including GST), map it to a unit_types entry:
    unit_label verbatim, sba_sqft = the apartment/super area with sba_unit,
    carpet_sqft = the carpet (note "Incl Balcony" in carpet context),
    rate_psf = the per-unit rate, components = the add-on lump sums (Car Parking,
    Club House, Infra), agreement_value = the Total Consideration BEFORE GST
    (= rate x super area + components), gst_amount and grand_total = Amount
    Including GST. Do NOT put the rate or the Basic Amount into components. When a
    booking stage groups an Earnest Deposit that differs by unit type (e.g. "10 L
    for 4 BHK, 15 L for 5BHK") with a balance under one percentage, keep the
    milestone percentage as value and copy the full conditional wording into
    value_text.
28. PER-UNIT CHARGES. When a charge has a DIFFERENT value for each unit column or
    configuration (e.g. Bren's "1 Year Maintenance Expenses" showing 229,562 /
    225,300 / 274,662 / 269,507 across the 4BHK Type 1, 4BHK Type 2, 5BHK Type 1
    and 5BHK Type 2 columns), create ONE additional_charges entry PER unit, put
    the number in amount (229562, not just amount_text) and set applies_to to
    that unit's label ("4BHK Type 1", ...). Never leave several identically
    labelled charge rows unattributed to their unit.
29. PLAN NAME vs SECTION CAPTION. A generic caption such as "PAYMENT SCHEDULE",
    "PAYMENT PLAN", "COST SHEET" or "SCHEDULE" is a section header, NOT a plan
    name. Set plan_name_as_printed to the specific plan name the document prints
    (e.g. "Construction Linked Plan", "Flexi 25:25:25:25", "30:70"); if the sheet
    only shows a generic caption, set plan_name_as_printed to a concise
    descriptive name that matches the plan structure (e.g. "Construction Linked
    Payment Plan") rather than the bare caption.
"""


class ExtractionError(Exception):
    pass


def extract(ingested: IngestResult, client: OpenAI | None = None) -> dict:
    """Run structured extraction. Returns the extraction dict (schema-valid)."""
    client = client or OpenAI(api_key=config.OPENAI_API_KEY)

    user_content: list[dict] = []
    for i, img_b64 in enumerate(ingested.images_b64, start=1):
        if ingested.page_count > 1:
            user_content.append({"type": "text", "text": f"Page {i} of {ingested.page_count}:"})
        user_content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{ingested.image_media_type};base64,{img_b64}",
                    "detail": "high",
                },
            }
        )

    if ingested.native_text:
        user_content.append(
            {
                "type": "text",
                "text": (
                    "Native PDF text layer (cross-check aid only; the image is "
                    "the source of truth for structure):\n\n" + ingested.native_text
                ),
            }
        )

    user_content.append(
        {
            "type": "text",
            "text": (
                "Extract this document into the JSON schema. Remember: verbatim, "
                "no normalisation, preserve multiplicity, use the notes escape "
                "hatch for anything that does not fit."
            ),
        }
    )

    response = client.chat.completions.create(
        model=config.EXTRACTION_MODEL,
        temperature=config.EXTRACTION_TEMPERATURE,
        max_tokens=config.MAX_OUTPUT_TOKENS_EXTRACTION,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_schema", "json_schema": EXTRACTION_SCHEMA},
    )

    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise ExtractionError(
            "Extraction output truncated (finish_reason=length). "
            "Increase MAX_OUTPUT_TOKENS_EXTRACTION or split the document."
        )
    if getattr(choice.message, "refusal", None):
        raise ExtractionError(f"Model refused extraction: {choice.message.refusal}")

    try:
        extraction = json.loads(choice.message.content)
    except (TypeError, json.JSONDecodeError) as exc:  # defensive; strict mode should prevent this
        raise ExtractionError(f"Failed to parse extraction JSON: {exc}") from exc

    return enrich_conditions_from_terms(extraction)


# ---------------------------------------------------------------------------
# Deterministic safety net for rule 16a (pricing conditions hidden in T&C)
# ---------------------------------------------------------------------------
# The prompt asks the model to duplicate buyer-relevant T&C sentences into
# pricing.conditions, but models are inconsistent about it. This pass guarantees
# it: any terms_and_conditions item that reads like a PRICING condition (a
# trigger word) and is not legal boilerplate (an exclusion word) is copied into
# pricing.conditions. Purely additive: it never removes a condition, and it only
# copies verbatim text the model already captured. Content generation and
# numeric reconciliation both read pricing.conditions, so nothing is dropped.
_CONDITION_TRIGGERS = (
    "extra", "additional", "as applicable", "inclusive of", "exclusive of",
    "payable", "over and above", "floor rise", "rera carpet", "carpet area",
    " tds ", "borne by", "govt", "government", "gst", "stamp duty", "registration",
)
_CONDITION_EXCLUDES = (
    "cancel", "document", "cheque", "draft", "in favour", "in favor", "account",
    "a/c ", "registration number", "rera registration", "good faith",
    "sq.mt. =", "sq. mt. =", "sq.mt =", "non-transferable", "signature verification",
    "mandatory document", "prior notice", "sole discretion",
)


def enrich_conditions_from_terms(extraction: dict) -> dict:
    """Copy pricing-relevant T&C sentences into pricing.conditions (idempotent)."""
    pricing = extraction.get("pricing")
    if not isinstance(pricing, dict):
        return extraction
    terms = extraction.get("terms_and_conditions") or []
    conditions = list(pricing.get("conditions") or [])
    seen = {c.strip().lower() for c in conditions}
    for item in terms:
        if not isinstance(item, str):
            continue
        low = f" {item.lower()} "
        if any(x in low for x in _CONDITION_EXCLUDES):
            continue
        if any(x in low for x in _CONDITION_TRIGGERS) and item.strip().lower() not in seen:
            conditions.append(item)
            seen.add(item.strip().lower())
    pricing["conditions"] = conditions
    return extraction
