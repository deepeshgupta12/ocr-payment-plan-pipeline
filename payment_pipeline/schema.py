"""
JSON Schema for structured extraction, designed for OpenAI Structured Outputs
(strict mode). Strict mode requires:
  - every property listed in "required"
  - "additionalProperties": false on every object
Optional fields are therefore expressed as nullable (["type", "null"]).

Design principles (agreed in spec discussions):
  - No fixed payment plan structure is assumed: `payment_plans` is always an
    array; milestones carry a `value_type` discriminator; `notes` is an escape
    hatch so the model never drops or invents structure.
  - Extract VERBATIM. No normalisation inside the vision call. Amounts are
    never converted to percentages or vice versa.
  - Price-sheet documents carry a separate `pricing` block (nullable when the
    document is a pure payment plan).
"""

MILESTONE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sr_no": {
            "type": ["integer", "null"],
            "description": "Serial number as printed, null if not printed.",
        },
        "label": {
            "type": "string",
            "description": "Milestone label exactly as printed by the developer.",
        },
        "value": {
            "type": ["number", "null"],
            "description": "Numeric value (percentage or INR amount). Null when the milestone is text-only.",
        },
        "value_type": {
            "type": "string",
            "enum": ["percentage", "amount", "percentage_plus_amount", "text"],
        },
        "value_text": {
            "type": ["string", "null"],
            "description": "Verbatim text for text-type or compound values, e.g. '10% + Rs 2 Lakh' or 'On Possession'.",
        },
    },
    "required": ["sr_no", "label", "value", "value_type", "value_text"],
}

PAYMENT_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plan_name_as_printed": {"type": "string"},
        "plan_type_proposed": {
            "type": "string",
            "enum": [
                "construction_linked",
                "down_payment",
                "subvention",
                "flexi",
                "possession_linked",
                "time_linked",
                "other_unclassified",
            ],
            "description": "Model's proposed classification. Confirmed or overridden by the deterministic validator.",
        },
        "milestones": {"type": "array", "items": MILESTONE_SCHEMA},
        "milestones_total_printed": {
            "type": ["number", "null"],
            "description": "The TOTAL row value if printed on the document, else null. Do not compute it.",
        },
        "conditions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Footnotes/conditions verbatim, e.g. 'GST extra as applicable'.",
        },
        "applies_to": {
            "type": ["string", "null"],
            "description": "If the plan applies only to a tower/unit-type, note it verbatim; else null.",
        },
        "notes": {
            "type": ["string", "null"],
            "description": "Escape hatch: anything extracted that did not fit the structure. Never omit information silently.",
        },
    },
    "required": [
        "plan_name_as_printed",
        "plan_type_proposed",
        "milestones",
        "milestones_total_printed",
        "conditions",
        "applies_to",
        "notes",
    ],
}

PRICING_COMPONENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "description": "Component label verbatim."},
        "amount": {"type": ["number", "null"]},
        "amount_text": {
            "type": ["string", "null"],
            "description": "Verbatim text when the component is not a clean number.",
        },
    },
    "required": ["label", "amount", "amount_text"],
}

# Charges levied ON TOP of the quoted price (lease rent, club membership, extra
# car parking...). Distinct from PRICING_COMPONENT_SCHEMA, which is only the
# lump sums that build a unit's agreement value.
ADDITIONAL_CHARGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "description": "Charge label verbatim, e.g. 'Lease Rent', 'Club Membership'."},
        "charge_type": {
            "type": "string",
            "enum": ["per_sqft", "per_sqft_per_month", "per_kw", "per_kva", "flat", "conditional", "text"],
            "description": "per_sqft: rate per sq ft; per_sqft_per_month: recurring rate (e.g. maintenance); per_kw/per_kva: rate per kilowatt / kVA (e.g. power backup, UPPCL); flat: lump sum; conditional: depends on floor/facing/unit; text: non-numeric.",
        },
        "amount": {"type": ["number", "null"], "description": "Numeric rate or amount when clean; else null. For a combined group, the shared total."},
        "amount_text": {"type": ["string", "null"], "description": "Verbatim text when not a clean single number."},
        "applies_to": {"type": ["string", "null"], "description": "What it is charged on, verbatim, e.g. 'Per Sq.Ft', 'Per KW', 'Super Area'."},
        "when_due": {
            "type": "string",
            "enum": ["one_time", "at_booking", "at_possession", "recurring", "unspecified"],
            "description": "When the charge falls due, from the document's grouping: 'at_possession' for a 'Possession Charges' block, 'one_time' for a one-time charge, 'recurring' for monthly/annual (e.g. maintenance), else 'unspecified'.",
        },
        "covers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "When ONE amount covers several named charges (a combined 'Other Charges' total), list those charge names here; empty array for a standalone charge.",
        },
    },
    "required": ["label", "charge_type", "amount", "amount_text", "applies_to", "when_due", "covers"],
}

# One row of a floor-wise / facing-wise Prime Location Charges (PLC) table.
LOCATION_CHARGE_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "condition_label": {"type": "string", "description": "Floor range or facing verbatim, e.g. '6th to 10th Floor', 'Park Facing'."},
        "rate": {"type": ["number", "null"], "description": "Numeric rate when printed; null when the cell is NIL or non-numeric."},
        "value_text": {"type": ["string", "null"], "description": "Verbatim cell text when not a clean number, e.g. 'NIL'."},
    },
    "required": ["condition_label", "rate", "value_text"],
}

# One PLC table. A document may carry several (e.g. a View PLC table AND a
# Floor PLC table), so pricing.location_charges is an ARRAY of these groups.
LOCATION_CHARGE_GROUP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label_as_printed": {"type": ["string", "null"], "description": "Section label, e.g. 'Preferential Location Charges - View', 'Floor PLC'."},
        "basis": {"type": ["string", "null"], "description": "Rate basis verbatim, e.g. 'Rs./Sq.ft', 'Per SqFt'."},
        "entries": {"type": "array", "items": LOCATION_CHARGE_ENTRY_SCHEMA},
    },
    "required": ["label_as_printed", "basis", "entries"],
}

UNIT_TYPE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "unit_label": {"type": "string", "description": "e.g. '2BHK', '3BHK', '4 BHK + Servant Type 1', or a named category like 'Diamond & Emerald' as printed."},
        "rate_psf": {"type": ["number", "null"], "description": "Basic sale price per sq ft for THIS unit/category when the rate varies by unit or category (e.g. 'Diamond & Emerald 11,900 PSF'). Null when a single sheet-wide pricing.rate_psf applies."},
        "sba_sqft": {"type": ["number", "null"], "description": "Super built up area NUMBER as printed (record the unit in sba_unit; never convert)."},
        "sba_unit": {"type": ["string", "null"], "description": "Printed unit for the super area, e.g. 'Sq.ft', 'Sq.mt'."},
        "carpet_sqft": {"type": ["number", "null"], "description": "Carpet area NUMBER as printed (record the unit in carpet_unit; never convert)."},
        "carpet_unit": {"type": ["string", "null"], "description": "Printed unit for the carpet area, e.g. 'Sq.ft', 'Sq.mt'."},
        "balcony_sqft": {"type": ["number", "null"], "description": "Balcony area NUMBER as printed when a separate balcony area is given; else null. Never convert."},
        "balcony_unit": {"type": ["string", "null"], "description": "Printed unit for the balcony area, e.g. 'Sq.ft', 'Sq.mt'."},
        "components": {"type": "array", "items": PRICING_COMPONENT_SCHEMA},
        "agreement_value": {"type": ["number", "null"]},
        "gst_rate_percent": {"type": ["number", "null"]},
        "gst_amount": {"type": ["number", "null"]},
        "grand_total": {"type": ["number", "null"]},
        "grand_total_label_as_printed": {"type": ["string", "null"]},
    },
    "required": [
        "unit_label",
        "rate_psf",
        "sba_sqft",
        "sba_unit",
        "carpet_sqft",
        "carpet_unit",
        "balcony_sqft",
        "balcony_unit",
        "components",
        "agreement_value",
        "gst_rate_percent",
        "gst_amount",
        "grand_total",
        "grand_total_label_as_printed",
    ],
}

# Floor-wise pricing: one cell of a floor x column price/rate matrix. Handles
# both a simple FLOOR->RATE->PRICE table and a FLOOR x unit-position grid.
# One labelled line inside a cost breakdown (e.g. "Agreement Cost",
# "Stamp Duty (7%)", "Registration Charges", "GST (5%)", "Total Cost").
BREAKDOWN_LINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {"type": "string", "description": "Line label verbatim, e.g. 'Agreement Cost', 'Stamp Duty (7%)', 'Total Cost'."},
        "value": {"type": ["number", "null"], "description": "Numeric value with separators removed; null when non-numeric."},
        "value_text": {"type": ["string", "null"], "description": "Verbatim text when not a clean number."},
    },
    "required": ["label", "value", "value_text"],
}

FLOOR_PRICE_CELL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "column_label": {"type": "string", "description": "The column header this cell belongs to, verbatim, e.g. 'RATE', 'INR RS.', '2 BHK 755', 'UNIT NOS. 1, 2, 5 & 6'."},
        "value": {"type": ["number", "null"], "description": "Single numeric value of the cell (rate or price) with separators removed; null when the cell is a multi-line breakdown or non-numeric."},
        "value_text": {"type": ["string", "null"], "description": "Verbatim cell text when not a clean number, e.g. 'INCLUSIVE IN PRICE'."},
        "breakdown": {
            "type": "array",
            "items": BREAKDOWN_LINE_SCHEMA,
            "description": "When the cell is a stacked cost breakdown (Agreement Cost, Stamp Duty, Registration, GST, Total Cost, and area lines), one entry per line, verbatim. Empty array for a plain single-value cell.",
        },
    },
    "required": ["column_label", "value", "value_text", "breakdown"],
}

FLOOR_PRICE_ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "floor_label": {"type": "string", "description": "Floor or floor range verbatim, e.g. '1ST TO 5TH', '24TH', 'TOP FLOOR'."},
        "cells": {"type": "array", "items": FLOOR_PRICE_CELL_SCHEMA},
    },
    "required": ["floor_label", "cells"],
}

# One budget/price-range cell: a price given as a low-to-high range that varies
# by building, configuration, view and/or payment plan (e.g. Hiranandani's
# "Jasmine 2bhk Garden 1.27 to 1.34CR" under the 30:70 plan).
PRICE_RANGE_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "cluster": {"type": ["string", "null"], "description": "Sub-project/cluster name verbatim, e.g. 'Golden Willows', 'The Arena'."},
        "building": {"type": ["string", "null"], "description": "Building/tower name verbatim, e.g. 'Jasmine', 'Aster'."},
        "configuration": {"type": ["string", "null"], "description": "Unit configuration verbatim, e.g. '2bhk', '3bhk'."},
        "carpet_sqft": {"type": ["number", "null"], "description": "Carpet area number as printed; never convert."},
        "carpet_unit": {"type": ["string", "null"], "description": "Unit for the carpet area, e.g. 'sq.ft'."},
        "payment_plan": {"type": ["string", "null"], "description": "Which payment plan this budget applies to, verbatim, e.g. '30:70', '20*5', 'Construction Linked (CLP)'."},
        "view": {"type": ["string", "null"], "description": "View/facing this price column is for, verbatim, e.g. 'Garden', 'Expressway/Highway'."},
        "price_low": {"type": ["number", "null"], "description": "Lower bound of the range with separators removed; equals price_high for a single value; null when non-numeric (e.g. 'NA')."},
        "price_high": {"type": ["number", "null"], "description": "Upper bound of the range; null when non-numeric."},
        "price_unit": {"type": ["string", "null"], "description": "Unit of the price, verbatim, e.g. 'CR', 'Lakh'."},
        "price_text": {"type": ["string", "null"], "description": "Verbatim cell text, e.g. '1.27 to 1.34CR', 'NA'."},
    },
    "required": [
        "cluster", "building", "configuration", "carpet_sqft", "carpet_unit",
        "payment_plan", "view", "price_low", "price_high", "price_unit", "price_text",
    ],
}

# One specific unit in a tower/floor inventory price list (Basic Price per unit).
UNIT_INVENTORY_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "tower": {"type": ["string", "null"], "description": "Tower/block name verbatim, e.g. 'Tower - A'."},
        "floor": {"type": ["string", "null"], "description": "Floor verbatim, e.g. '3rd', '14th'."},
        "unit_type": {"type": ["string", "null"], "description": "Unit type verbatim, e.g. '3BHK Type III (2020)'."},
        "unit_address": {"type": ["string", "null"], "description": "Unit number/address verbatim, e.g. 'A-306'."},
        "basic_price": {"type": ["number", "null"], "description": "Basic price with separators removed."},
    },
    "required": ["tower", "floor", "unit_type", "unit_address", "basic_price"],
}

FLOOR_PRICING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "applies_to": {"type": ["string", "null"], "description": "Unit/tower context this matrix applies to, verbatim, e.g. '2BHK - 997, T6 & T7', '3 BHK+2T'."},
        "basis": {"type": ["string", "null"], "description": "What the numbers represent, verbatim, e.g. 'Basic Cost (Semi Furnished)', 'Rate and total price'."},
        "column_headers": {"type": "array", "items": {"type": "string"}, "description": "Column headers in order (excluding the floor column), verbatim."},
        "rows": {"type": "array", "items": FLOOR_PRICE_ROW_SCHEMA},
    },
    "required": ["applies_to", "basis", "column_headers", "rows"],
}

PRICING_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "properties": {
        "pricing_basis": {
            "type": "string",
            "enum": ["per_unit_type", "per_tower", "per_floor", "single", "other"],
        },
        "rate_psf": {"type": ["number", "null"]},
        "rate_psf_applies_to": {
            "type": ["string", "null"],
            "description": "e.g. 'SBA', 'Carpet' — verbatim from the document.",
        },
        "unit_types": {"type": "array", "items": UNIT_TYPE_SCHEMA},
        "inclusions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Verbatim lines from an 'Above Prices Inclusive Of' / 'Inclusions' block; empty array if none.",
        },
        "additional_charges": {
            "type": "array",
            "items": ADDITIONAL_CHARGE_SCHEMA,
            "description": "Charges ON TOP of the quoted price (lease rent, club membership, extra car parking...). NOT part of the agreement-value build-up. Empty array if none.",
        },
        "location_charges": {
            "type": "array",
            "items": LOCATION_CHARGE_GROUP_SCHEMA,
            "description": "Every Prime/Preferential Location Charge table on the sheet (a View PLC table and a Floor PLC table are two separate groups). Empty array when none.",
        },
        "floor_pricing": {
            "type": "array",
            "items": FLOOR_PRICING_SCHEMA,
            "description": "Floor-wise price/rate matrices when price varies by floor (and optionally by unit position). Empty array when price is a single value per unit type.",
        },
        "unit_inventory": {
            "type": "array",
            "items": UNIT_INVENTORY_ENTRY_SCHEMA,
            "description": "A tower/floor inventory price list of individual units (each with a unit address and basic price). Empty array when the sheet has no per-unit inventory.",
        },
        "price_ranges": {
            "type": "array",
            "items": PRICE_RANGE_ENTRY_SCHEMA,
            "description": "Budget/price ranges that vary by building, configuration, view and/or payment plan (prices shown as a low-to-high range). Empty array when prices are single values.",
        },
        "conditions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "pricing_basis", "rate_psf", "rate_psf_applies_to", "unit_types",
        "inclusions", "additional_charges", "location_charges", "floor_pricing",
        "unit_inventory", "price_ranges", "conditions",
    ],
}

EXTRACTION_SCHEMA = {
    "name": "payment_plan_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "document_type": {
                        "type": "string",
                        "enum": [
                            "payment_plan_only",
                            "price_sheet_with_payment_schedule",
                            "price_sheet_only",
                            "other",
                        ],
                    },
                    "title_as_printed": {"type": ["string", "null"]},
                    "project_name": {"type": ["string", "null"]},
                    "extraction_confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "confidence_reason": {
                        "type": ["string", "null"],
                        "description": "Required explanation when confidence is medium/low (blur, cut-off table, ambiguous cells...).",
                    },
                },
                "required": [
                    "document_type",
                    "title_as_printed",
                    "project_name",
                    "extraction_confidence",
                    "confidence_reason",
                ],
            },
            "pricing": PRICING_SCHEMA,
            "payment_plans": {"type": "array", "items": PAYMENT_PLAN_SCHEMA},
            "terms_and_conditions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Verbatim document-level Terms & Conditions / notes list, one string per item; empty array if none.",
            },
        },
        "required": ["document", "pricing", "payment_plans", "terms_and_conditions"],
    },
}
