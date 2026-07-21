"""
Stage 6 — SEO/AEO Content Generation (+ post-generation guardrail).

Reads the canonical datacenter record and generates the Payment Plan PDP
section: charter-voice prose, semantic HTML tables (milestone schedule +
pricing), and FAQPage JSON-LD.

Because this stage uses free generation (no variable injection), the safety
net moves AFTER generation: every number appearing in the generated content
must exist in the source JSON (with Indian-format tolerance), and no numbers
may appear that are not in the JSON. Failures route the content — not the
extraction — to review.
"""

from __future__ import annotations

import json
import math
import re

from openai import OpenAI

from . import config

# ---------------------------------------------------------------------------
# VOICE CHARTER
# ---------------------------------------------------------------------------
# PASTE THE FULL "Senior Real Estate Writer" VOICE CHARTER v2.0 HERE.
# The block below is a minimal structural stand-in so the pipeline runs
# end-to-end; replace it with the production charter verbatim.
VOICE_CHARTER = """\
[SENIOR REAL ESTATE WRITER, VOICE CHARTER v2.0, REPLACE WITH PRODUCTION TEXT]
- Write as a senior real estate content writer: authoritative, factual and
  buyer first, with clean flowing sentences and precise Indian real estate
  vocabulary (SBA, carpet area, AOS, CLP, PLC, RPSF). Explain, never sell.
- No hype adjectives and no exclamation marks.
- CRITICAL PUNCTUATION RULE: never use a hyphen, an en dash or an em dash
  anywhere in the output. Rephrase every compound adjective into plain words
  (write "payment plan linked to construction stages", not the hyphenated form;
  write "nothing to pay until possession", not the hyphenated form; write
  "built up area" and "super built up area", never the hyphenated forms). Use
  commas, full stops or separate sentences in place of any dash. Before you
  return, scan every character of your authored text and delete any hyphen, en
  dash or em dash that remains.
- Amounts in Indian formatting (for example Rs 81.67 Lakh) only when the exact
  figure is also stated. Never round away the exact figure.
- Every number stated must come from the supplied JSON. If a fact is not in the
  JSON, it does not exist. Never estimate or generalise from market knowledge.
- British English spellings per Square Yards convention.
"""

GENERATION_SYSTEM_PROMPT = f"""\
You are the Senior Real Estate Writer for Square Yards, generating the
"Payment Plan" section of a Project Detail Page.

VOICE CHARTER (binding):
{VOICE_CHARTER}

CONTENT STRUCTURE (binding), Answer Engine Optimised H3 blocks:
- seo_prose_html is a single <section> made of several <h3> blocks. There is no
  <h2> (the page renders the section heading itself).
- Every <h3> is phrased as a natural question a buyer or an AI assistant would
  ask, so the answer beneath it can be lifted directly (this is the AEO format).
  Adapt the questions to the actual data, never copy these verbatim and never
  invent facts. Question style to follow:
    "What is the payment plan at <project>?"
    "What is the complete payment schedule?"
    "What is the total price of a 2 BHK at <project>?"
    "How does the price vary by floor?"
    "What is included in the price?"
    "Which additional charges apply beyond the price?"
    "How do the prime location charges vary by floor?"
    "Is GST included in the quoted price?"
- ONLY ASK A QUESTION THE JSON CAN ANSWER. Every <h3> must be fully answerable
  from the supplied data. NEVER emit a question whose answer would be "not
  specified", "not available", "contact the developer", or any variation. If the
  data for a topic is absent, simply do not create that <h3>. An empty or filler
  answer is a defect, not a block.
- PAYMENT PLAN BLOCKS ARE CONDITIONAL. Only create the payment plan and payment
  schedule questions when payment_plans is present and non empty. When
  payment_plans is empty (a pure price sheet), omit them and lead instead with
  the price or rate, then the charges and location charges. Do not manufacture a
  payment plan question just to fill the slot.
- DO NOT echo a generic section caption as a plan name. If plan_name_as_printed
  is a bare caption like "PAYMENT SCHEDULE", "PAYMENT PLAN", "COST SHEET" or
  "SCHEDULE", do not write "Plan name: PAYMENT SCHEDULE"; instead describe the
  plan by its structure or validated type (e.g. "a construction linked payment
  plan"). Only surface a plan name when it is a real, specific name.
- NAMED PLANS WITH NO MILESTONES. When payment_plans lists real plan NAMES but
  none carry a milestone breakdown (e.g. Hiranandani's "30:70", "20*5" and
  "Construction Linked (CLP)" budget plans), do not fabricate milestones and do
  not skip them: add a short <h3> such as "Which payment plans are available?"
  that names each distinct plan as a <ul> and states that the budget differs by
  plan (point the buyer to the budget ranges table). Deduplicate repeated plan
  names.
- COMPLETE SCHEDULE, NEVER DROP A MILESTONE. When a plan has milestones, exactly
  ONE <h3> presents the payment schedule as a <table> with one row for EVERY
  milestone in the plan's milestones array, in the SAME order, from the first
  (e.g. expression of interest / EOI / booking) through to possession. The row
  count must equal the number of milestones. Do NOT frame this block as
  "construction stages" only and do NOT split the schedule so that an early
  stage (booking, EOI, a "within N days" stage) is left out. Reproduce each
  milestone's label and its value or verbatim value_text. You may still add a
  separate booking question that highlights the upfront amount, but the schedule
  table itself must remain complete.
- Under EVERY <h3>, without exception, provide ALL of: (1) a one or two sentence
  direct answer in a <p>; (2) a short <ul> of 2 to 4 key highlights that pull
  out the most important figures or takeaways; and (3) WHEN the data is tabular
  (a milestone schedule, a price build up, a charges list, a budget grid) the
  <table> as well. A block must NEVER be a bare <table> with no surrounding <p>
  and <ul>, and never a lone <p>. The bullets and the table are complementary:
  the bullets highlight, the table gives the full detail. So a schedule block has
  a <p> + <ul> of highlights (e.g. the booking percentage, the possession
  percentage, the headline charges) + the full schedule <table>; a charges block
  has a <p> + <ul> of the headline charges + the charges <table>. CRITICAL: a
  bullet may ONLY cite a number that appears in the JSON. Do NOT invent DERIVED
  numbers such as a COUNT of milestones, stages, unit types, towers or charges
  (e.g. never write "divided into 14 stages" or "across 4 unit types" unless
  that exact number is in the JSON). Describe counts qualitatively instead
  ("payments are spread across the construction stages").
- Produce as many <h3> blocks as the data genuinely supports (typically four to
  seven when a payment plan is present, fewer for a bare price sheet), ordered
  the way a buyer reasons: the plan, the booking and milestone schedule, the
  price or rate, the additional charges, the location charges, and GST.

HTML/CSS STANDARDS (binding):
- Semantic markup only: <section>, <h3>, <p>, <ul>/<li>, and <table> with
  <thead>/<tbody>/<th scope="col">. No inline styles and no divs used as tables.
- The milestone schedule and the pricing build up are AEO targets: clean
  semantic tables, one row per milestone or per charge, exact printed labels.

STRUCTURE-AWARE FRAMING (branch on plan_type_validated):
- construction_linked: explain the booking percentage and the spread across
  construction stages, and the payment predictability tied to build progress.
- down_payment or possession_linked (for example 10:90): lead with the low
  upfront amount and the story of nothing to pay until possession.
- subvention: explain the mechanic of no EMI until possession and its conditions.
- flexi, time_linked or other_unclassified: describe exactly what the JSON
  shows and do not force a narrative.
- Multiple plans: compare them, one table per plan.

PRICING DETAIL BLOCKS (add an <h3> for each ONLY when the field is present and
non empty in the JSON; skip silently when absent so the previous project shapes
still work):
- pricing.floor_pricing: an <h3> such as "How does the price vary by floor?"
  with a <table> reproducing the matrix, the floor band in the first column and
  one column per header. When this block is present the price is floor
  dependent, so do NOT also state a single total price; point the buyer to this
  table instead. When the cells carry a `breakdown` (a full cost stack such as
  Agreement Cost, Stamp Duty, Registration, GST, Total Cost), produce TWO blocks:
  one <h3> giving the Total Cost by floor and unit as an overview table, and a
  SECOND <h3> such as "What does the total cost include?" that shows the full
  composition (Agreement Cost, Stamp Duty, Registration, GST, Total Cost) for at
  least one representative floor band and unit as a <table>, so the buyer sees
  the total is inclusive of those government charges. Use only numbers present
  in the JSON.
- pricing.unit_inventory: an <h3> such as "What are the available units and their
  prices?" Summarise, do NOT paste every row: state the towers covered, the unit
  types, and the basic price RANGE (the lowest and highest basic_price, both of
  which exist in the JSON). You may show a short representative <table>, but keep
  it brief; the full inventory lives in the record.
- pricing.price_ranges: create an <h3> such as "What are the budget ranges by
  configuration and view?" with a one or two sentence intro <p> and a short <ul>
  naming the payment plans, clusters and views the budgets cover. KEEP THIS BLOCK
  SHORT: do NOT type out every budget row yourself (a sheet can have dozens); the
  complete per-plan tables are assembled separately and appended to this block,
  so a brief intro is all you need here. This keeps you from running out of
  output space. Do not invent numbers beyond price_low/price_high.
- Balcony area: when a unit_types entry has a balcony_sqft, state it alongside
  the carpet area with its exact unit; never convert.
- pricing.inclusions: an <h3> such as "What is included in the price?" with the
  inclusions listed verbatim as a <ul>.
- pricing.additional_charges: an <h3> such as "Which additional charges apply
  beyond the price?" with a <table> or <ul> showing each charge, its amount or
  rate, and its basis. State the basis exactly: per square foot, per KW, per
  KVA, per square foot per month (recurring), a lump sum, or conditional. When a
  charge has a non empty "covers" list, state that the single amount covers
  those named items. When a charge has an applies_to that names a unit or
  configuration (e.g. a maintenance amount that differs per unit type), show
  that unit/configuration next to its amount so each figure is attributable, for
  example a "Unit / Configuration" column in the charges table. When charges carry a "when_due" of "at_possession" or
  "one_time" or "recurring", group or label them by that timing (for example a
  "Charges payable at possession" grouping). Make plain these are charged on top
  of the quoted price.
- pricing.location_charges (an ARRAY of PLC tables): an <h3> such as "How do the
  location charges vary?" Render EACH table in the array, keeping its own label
  (for example one for view or facing and one for floor), each as a <table> of
  the verbatim condition against its rate, including rows shown as NIL. Never
  merge separate PLC tables into one.
- Per unit or per category rate: when a unit_types entry carries its own
  rate_psf (the basic sale price differs by category or tower), state each
  category and its rate per square foot; do not imply a single blanket rate.
- Areas: state each area with the exact unit recorded (Sq.ft or Sq.mt). NEVER
  convert between Sq.ft and Sq.mt.
- pricing.conditions (and a plan's conditions): fold the buyer-relevant ones
  into a short "Good to know" <h3> or into the most relevant block, for example
  that the total cost is inclusive of GST, registration and stamp duty, that a
  floor rise applies from a certain floor, or that some charges are extra as
  applicable. Present them as a <ul>.
- Document-level terms_and_conditions are NOT provided to you and must NOT be
  fabricated; every buyer-relevant charge or condition you need has already been
  structured into pricing.additional_charges or pricing.conditions.

ABSOLUTE DATA RULES:
1. Every percentage, amount, area and milestone label must exist in the
   supplied JSON. Never introduce a number that is not in the JSON.
2. Carry conditions forward: if the JSON says the grand total excludes stamp
   duty and registration, or that GST, registration and possession charges are
   extra as applicable, the content must state it.
3. Do not publish derived signals (inferred city or status) as facts.
4. Obey the no hyphen and no dash rule from the charter EVERYWHERE: every <h3>
   heading, description, bullet, table cell and the meta description. This
   applies even to labels and inclusions copied from the source JSON. Keep every
   word and number, but replace any hyphen, en dash or em dash with a comma, the
   word "and", or a space (for example render "VRV system - hot and cold in the
   apartment" as "VRV system, hot and cold in the apartment"). The exact original
   wording is preserved in the extraction record, so the buyer facing content
   stays completely free of dashes.

FAQ: do NOT produce a separate FAQ or any FAQPage JSON-LD. The AEO <h3>
question and answer blocks inside seo_prose_html ARE the FAQ; accommodate every
frequently asked question as one of those on page <h3> blocks.

OUTPUT: respond ONLY with a JSON object (no markdown fences) with keys:
  "seo_prose_html"            — the <section> of AEO <h3> blocks described above
  "payment_schedule_table_html" — semantic <table> per plan (concatenated)
  "pricing_table_html"        — semantic pricing <table>, or null if no pricing
  "meta_description"          — 160 characters or fewer, exact figure safe
"""


class GenerationError(Exception):
    pass


def generate_content(record: dict, client: OpenAI | None = None) -> dict:
    """Generate the content package from a canonical datacenter record."""
    client = client or OpenAI(api_key=config.OPENAI_API_KEY)

    extraction = record["extraction"]
    payload = {
        "project_name": extraction.get("document", {}).get("project_name"),
        "document_type": extraction.get("document", {}).get("document_type"),
        "pricing": extraction.get("pricing"),
        "payment_plans": extraction.get("payment_plans"),
    }

    response = client.chat.completions.create(
        model=config.GENERATION_MODEL,
        temperature=config.GENERATION_TEMPERATURE,
        max_tokens=config.MAX_OUTPUT_TOKENS_GENERATION,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": GENERATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Source JSON (the only permissible source of facts):\n\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2)
                ),
            },
        ],
    )

    choice = response.choices[0]
    if choice.finish_reason == "length":
        raise GenerationError("Generation truncated; raise MAX_OUTPUT_TOKENS_GENERATION.")
    try:
        content = json.loads(choice.message.content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"Failed to parse generation JSON: {exc}") from exc
    content = inject_complete_price_ranges(extraction, content)
    content = inject_complete_schedule(extraction, content)
    content = inject_complete_charges(extraction, content)
    content = inject_conditions_block(extraction, content)
    return strip_dashes_from_content(content)


# ---------------------------------------------------------------------------
# Deterministic no-dash guarantee (safety net over the prompt rule)
# ---------------------------------------------------------------------------
# The charter forbids hyphens/dashes, but a model occasionally reproduces one
# from a source label ("Power Back-Up", "built-up"). This pass removes any that
# survive, operating ONLY on text nodes so HTML tags and attributes are never
# touched. Numbers are unaffected (a dash between digits becomes a space, which
# does not occur in this content).
_TAG_RE = re.compile(r"<[^>]+>")
_DASH_RUN_RE = re.compile(r"\s*[-‐‑‒–—―−]+\s*")


def _strip_dashes_text(text: str) -> str:
    # Replace dashes and collapse runs of whitespace, but do NOT strip the ends:
    # this function runs per text node between tags, so a boundary space adjacent
    # to an inline element (e.g. "Book with <strong>10%</strong>") must survive.
    if not text:
        return text
    out = _DASH_RUN_RE.sub(" ", text)
    return re.sub(r"[ \t]{2,}", " ", out)


def _strip_dashes_html(html: str) -> str:
    """Replace dashes inside text nodes; leave everything within < ... > intact."""
    if not html:
        return html
    parts: list[str] = []
    last = 0
    for m in _TAG_RE.finditer(html):
        parts.append(_strip_dashes_text(html[last:m.start()]))
        parts.append(m.group(0))  # tag/attributes untouched
        last = m.end()
    parts.append(_strip_dashes_text(html[last:]))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Deterministic completeness for price_ranges (safety net over the prompt)
# ---------------------------------------------------------------------------
# LLMs reliably summarise but drop rows from large tables. When a sheet has many
# budget ranges spread across several payment plans (e.g. Hiranandani: 44 cells
# over 30:70 / 20*5 / CLP), the model tends to render one partial table. This
# pass rebuilds the budget block deterministically from pricing.price_ranges:
# one labelled table per distinct payment plan, every entry included. Purely
# additive: it only runs when price_ranges is non-empty, and every number it
# prints already exists in the JSON (so reconciliation still passes).

def _esc_html(text) -> str:
    s = "" if text is None else str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_H3_RE = re.compile(r"<h3\b[^>]*>(.*?)</h3>", re.DOTALL | re.IGNORECASE)


def _find_block(prose: str, keyword_regex: str):
    """Find the first <h3> whose TAG-STRIPPED text matches keyword_regex, and
    return (block_start, block_end) spanning that heading through just before the
    next <h3> (or </section>/end). Tag-tolerant, so inline markup in a heading
    never causes a miss (which would otherwise duplicate the block). None if no
    matching heading exists."""
    for m in _H3_RE.finditer(prose):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).lower()
        if re.search(keyword_regex, inner, re.IGNORECASE):
            nxt = re.search(r"<h3\b", prose[m.end():])
            if nxt:
                end = m.end() + nxt.start()
            else:
                end = prose.find("</section>") if "</section>" in prose else len(prose)
            return m.start(), end
    return None


def _price_range_tables_html(price_ranges: list) -> str:
    plans: list[str] = []
    by_plan: dict[str, list] = {}
    for e in price_ranges:
        plan = e.get("payment_plan") or "Payment plan"
        if plan not in by_plan:
            by_plan[plan] = []
            plans.append(plan)
        by_plan[plan].append(e)

    cols = ["Cluster", "Building", "Configuration", "Carpet area", "View", "Price range"]
    head = "<tr>" + "".join(f'<th scope="col">{c}</th>' for c in cols) + "</tr>"
    parts: list[str] = []
    for plan in plans:
        rows = by_plan[plan]
        # A short line of prose before each table so no table stands bare.
        # No derived counts here (they would not exist in the JSON).
        intro = f"<p>Indicative budgets under the {_esc_html(plan)}:</p>"
        rows_html = []
        for e in rows:
            carpet = e.get("carpet_sqft")
            carpet_s = ""
            if carpet is not None:
                carpet_s = f"{carpet} {e.get('carpet_unit') or ''}".strip()
            cells = [e.get("cluster"), e.get("building"), e.get("configuration"),
                     carpet_s, e.get("view"), e.get("price_text")]
            rows_html.append("<tr>" + "".join(f"<td>{_esc_html(c)}</td>" for c in cells) + "</tr>")
        parts.append(
            intro + f"<table><caption>{_esc_html(plan)}</caption>"
            f"<thead>{head}</thead><tbody>{''.join(rows_html)}</tbody></table>"
        )
    return "".join(parts)


def inject_complete_price_ranges(extraction: dict, content: dict) -> dict:
    """Replace the LLM's budget-ranges block with a complete, per-plan set of
    tables built directly from pricing.price_ranges. No-op when there are none."""
    pricing = (extraction or {}).get("pricing") or {}
    price_ranges = pricing.get("price_ranges") or []
    prose = content.get("seo_prose_html")
    if not price_ranges or not isinstance(prose, str):
        return content

    intro = ("<p>The indicative budget ranges are grouped by payment plan below. "
             "Each row is the budget for that configuration and view.</p>")

    def _distinct(key):
        seen: list[str] = []
        for e in price_ranges:
            v = e.get(key)
            if v and v not in seen:
                seen.append(v)
        return seen

    highlights = []
    plans, views, clusters = _distinct("payment_plan"), _distinct("view"), _distinct("cluster")
    if plans:
        highlights.append(f"Payment plans: {', '.join(plans)}.")
    if clusters:
        highlights.append(f"Clusters: {', '.join(clusters)}.")
    if views:
        highlights.append(f"Views priced separately: {', '.join(views)}.")
    highlights_ul = "<ul>" + "".join(f"<li>{_esc_html(h)}</li>" for h in highlights) + "</ul>" if highlights else ""

    block = ("<h3>What are the budget ranges by configuration and view?</h3>"
             + intro + highlights_ul + _price_range_tables_html(price_ranges))

    # Replace an existing budget <h3> block (tag-tolerant match).
    span = _find_block(prose, r"budget")
    if span:
        prose = prose[:span[0]] + block + prose[span[1]:]
    elif "</section>" in prose:
        prose = prose.replace("</section>", block + "</section>", 1)
    else:
        prose = prose + block

    content = dict(content)
    content["seo_prose_html"] = prose
    return content


# Boilerplate that is ALWAYS dropped from the buyer-facing conditions list.
_COND_STRONG_EXCLUDE = (
    "in favour", "in favor", "prior notice", "sole discretion", "good faith",
    "mandatory document", "signature verification", "rera registration",
    "registration number", "account number", "a/c ", "photocopy",
)
# Payment-instrument / paperwork words that drop a condition ONLY when it is not
# actually about a charge or a pricing rule (a maintenance clause may mention a
# cheque yet still be buyer-relevant).
_COND_WEAK_EXCLUDE = ("cheque", "draft", "dd/", "documents required")
_COND_KEEP = (
    "maintenance", "charge", "gst", "stamp", "tds", "floor rise", "deposit",
    "tax", "fee", "rate", "extra", "applicable", "registration", "inclusive",
    "exclusive", "carpet", "bwssb", "e-stamp", "e stamp", "psf", "per square",
)


def _buyer_relevant_conditions(conditions: list) -> list:
    """Filter pricing.conditions down to buyer-relevant ones, dropping legal
    boilerplate the model may have parked there while KEEPING a genuine charge or
    pricing rule even if it happens to mention a cheque/DD."""
    out: list[str] = []
    seen: set[str] = set()
    for c in conditions:
        if not isinstance(c, str):
            continue
        low = f" {c.lower()} "
        if any(x in low for x in _COND_STRONG_EXCLUDE):
            continue
        if any(x in low for x in _COND_WEAK_EXCLUDE) and not any(k in low for k in _COND_KEEP):
            continue
        key = c.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def inject_conditions_block(extraction: dict, content: dict) -> dict:
    """Guarantee the buyer-relevant conditions (the "pointers" printed below the
    tables) surface as a complete "Good to know" block, rebuilt deterministically
    from pricing.conditions. No-op when there are none."""
    pricing = (extraction or {}).get("pricing") or {}
    conds = _buyer_relevant_conditions(pricing.get("conditions") or [])
    prose = content.get("seo_prose_html")
    if not conds or not isinstance(prose, str):
        return content

    intro = "<p>Keep these conditions and additional obligations in mind when planning your purchase.</p>"
    bullets = "".join(f"<li>{_esc_html(c)}</li>" for c in conds)
    block = f"<h3>Good to know</h3>{intro}<ul>{bullets}</ul>"

    span = _find_block(prose, r"good to know")
    if span:
        prose = prose[:span[0]] + block + prose[span[1]:]
    elif "</section>" in prose:
        prose = prose.replace("</section>", block + "</section>", 1)
    else:
        prose = prose + block

    content = dict(content)
    content["seo_prose_html"] = prose
    return content


def _augment_or_create_block(prose: str, keyword_regex: str, title: str,
                             intro: str, extra_html: str) -> str:
    """Find the matching <h3> block (tag-tolerant) and append extra_html after
    removing any table the model rendered there; if no such block exists, create
    one before the "Good to know" block or the end of the section."""
    span = _find_block(prose, keyword_regex)
    if span:
        block = re.sub(r"<table.*?</table>", "", prose[span[0]:span[1]], flags=re.S)
        return prose[:span[0]] + block + extra_html + prose[span[1]:]
    new_block = f"<h3>{title}</h3>{intro}{extra_html}"
    gtk = _find_block(prose, r"good to know")
    if gtk:
        return prose[:gtk[0]] + new_block + prose[gtk[0]:]
    if "</section>" in prose:
        return prose.replace("</section>", new_block + "</section>", 1)
    return prose + new_block


def _milestone_payment_text(m: dict) -> str:
    if m.get("value_text"):
        return m["value_text"]
    val, vt = m.get("value"), m.get("value_type")
    if val is None:
        return ""
    return f"{val}%" if vt == "percentage" else str(val)


_GENERIC_PLAN_CAPTIONS = {"payment schedule", "payment plan", "schedule", "cost sheet"}


def _schedule_tables_html(payment_plans: list) -> str:
    parts: list[str] = []
    for pl in payment_plans:
        ms = pl.get("milestones") or []
        if not ms:
            continue
        rows = "".join(
            f"<tr><td>{_esc_html(m.get('label'))}</td>"
            f"<td>{_esc_html(_milestone_payment_text(m))}</td></tr>"
            for m in ms
        )
        head = '<tr><th scope="col">Stage</th><th scope="col">Payment</th></tr>'
        name = pl.get("plan_name_as_printed") or ""
        cap = (f"<caption>{_esc_html(name)}</caption>"
               if name and name.strip().lower() not in _GENERIC_PLAN_CAPTIONS else "")
        parts.append(f"<table>{cap}<thead>{head}</thead><tbody>{rows}</tbody></table>")
    return "".join(parts)


def inject_complete_schedule(extraction: dict, content: dict) -> dict:
    """Guarantee a complete milestone schedule <table> (every milestone, in
    order) in the schedule block. No-op when no plan has milestones."""
    plans = (extraction or {}).get("payment_plans") or []
    prose = content.get("seo_prose_html")
    if not isinstance(prose, str) or not any(pl.get("milestones") for pl in plans):
        return content
    tables = _schedule_tables_html(plans)
    if not tables:
        return content
    prose = _augment_or_create_block(
        prose, r"(payment schedule|schedule|milestone)",
        "What is the complete payment schedule?",
        "<p>The full payment schedule is below.</p>", tables)
    content = dict(content)
    content["seo_prose_html"] = prose
    return content


_CHARGE_BASIS_LABEL = {
    "per_sqft": "Per sq ft", "per_sqft_per_month": "Per sq ft per month",
    "per_kw": "Per KW", "per_kva": "Per KVA", "flat": "Lump sum",
    "conditional": "Conditional", "text": "",
}


def _charge_amount_text(a: dict) -> str:
    if a.get("amount_text"):
        return a["amount_text"]
    amt = a.get("amount")
    if amt == 0:
        return "Free"
    if isinstance(amt, (int, float)):
        # Preserve decimals (e.g. a 2.25 per-sq-ft-per-month rate); rounding to
        # whole rupees would both misstate the figure and, for a decimal >= the
        # ignore threshold, trip a false reconciliation failure.
        if float(amt).is_integer():
            return f"Rs {amt:,.0f}"
        return f"Rs {amt:,.2f}".rstrip("0").rstrip(".")
    return ""


def _charges_table_html(additional_charges: list) -> str:
    if not additional_charges:
        return ""
    show_unit = any(a.get("applies_to") for a in additional_charges)
    cols = ["Charge", "Amount", "Basis"] + (["Unit / Configuration"] if show_unit else [])
    head = "<tr>" + "".join(f'<th scope="col">{c}</th>' for c in cols) + "</tr>"
    rows = []
    for a in additional_charges:
        cells = [a.get("label"), _charge_amount_text(a),
                 _CHARGE_BASIS_LABEL.get(a.get("charge_type"), a.get("charge_type") or "")]
        if show_unit:
            cells.append(a.get("applies_to") or "")
        rows.append("<tr>" + "".join(f"<td>{_esc_html(c)}</td>" for c in cells) + "</tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def inject_complete_charges(extraction: dict, content: dict) -> dict:
    """Guarantee a complete additional-charges <table> (every charge, with its
    basis and unit when applicable). No-op when there are no additional charges."""
    pricing = (extraction or {}).get("pricing") or {}
    charges = pricing.get("additional_charges") or []
    prose = content.get("seo_prose_html")
    if not isinstance(prose, str) or not charges:
        return content
    table = _charges_table_html(charges)
    if not table:
        return content
    prose = _augment_or_create_block(
        prose, r"(additional charges|other charges|charges apply)",
        "Which additional charges apply beyond the price?",
        "<p>These charges apply on top of the quoted price.</p>", table)
    content = dict(content)
    content["seo_prose_html"] = prose
    return content


def strip_dashes_from_content(content: dict) -> dict:
    """Return the content package with every hyphen/dash removed from the
    human-readable fields. Idempotent; safe to run on already-clean content."""
    cleaned = dict(content)
    for key in ("seo_prose_html", "payment_schedule_table_html", "pricing_table_html"):
        if isinstance(cleaned.get(key), str):
            cleaned[key] = _strip_dashes_html(cleaned[key])
    if isinstance(cleaned.get("meta_description"), str):
        cleaned["meta_description"] = _strip_dashes_text(cleaned["meta_description"]).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Post-generation numeric reconciliation (the variable-safety replacement)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers_in_json(obj) -> set[float]:
    found: set[float] = set()

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            found.add(float(o))
        elif isinstance(o, str):
            for m in _NUM_RE.findall(o):
                try:
                    found.add(float(m.replace(",", "")))
                except ValueError:
                    pass

    walk(obj)
    return found


def _allowed_derivations(base: set[float]) -> set[float]:
    """Numbers legitimately derivable from source numbers in prose:
    lakh/crore restatements of exact INR figures, e.g. 8166640 -> 81.6664,
    81.67, 0.8166640 (crore), 0.82. A writer may round, truncate or ceil the last
    printed decimal (81.745 -> 81.74 or 81.75), so accept floor, round AND ceil at
    each precision; all still trace to the same exact figure."""
    derived: set[float] = set()
    for n in base:
        if n >= 100000:  # only large amounts get lakh/crore restatement
            for value in (n / 1e5, n / 1e7):  # lakh, crore
                derived.add(round(value, 4))
                for places in (1, 2):
                    f = 10 ** places
                    derived.add(round(value, places))
                    derived.add(math.floor(value * f) / f)
                    derived.add(math.ceil(value * f) / f)
    return derived


def reconcile_numbers(record: dict, content: dict) -> dict:
    """Every number in the generated content must exist in the source JSON
    (or be an exact lakh/crore restatement of one). Returns a report dict."""
    source_payload = {
        "pricing": record["extraction"].get("pricing"),
        "payment_plans": record["extraction"].get("payment_plans"),
    }
    allowed = _numbers_in_json(source_payload)
    allowed |= _allowed_derivations(allowed)
    allowed.add(100.0)  # the schedule TOTAL row

    content_text = json.dumps(content, ensure_ascii=False)

    violations: list[str] = []
    for match in _NUM_RE.findall(content_text):
        try:
            val = float(match.replace(",", ""))
        except ValueError:
            continue
        if val < config.POST_GEN_IGNORE_NUMBERS_BELOW:
            continue  # sr numbers, small structural ints
        if val not in allowed:
            violations.append(match)

    return {
        "check": "post_generation_numeric_reconciliation",
        "result": "pass" if not violations else "fail",
        "violations": sorted(set(violations)),
        "detail": (
            "All numbers in generated content trace to the source JSON."
            if not violations else
            f"{len(set(violations))} number(s) in generated content not found in "
            "source JSON — content routed to review, not published."
        ),
    }
