"""
Stage 4 — Validate (pre-save).

Deterministic, auditable checks over the extraction. The validator's job is
NOT "does this match the schema" (structured outputs guarantees that) but:
"is this plan coherent enough to auto-publish, or does a human need to look
at it?"

Rule philosophy (agreed in spec discussions):
  - Rules are PER PLAN TYPE. sum-to-100 applies only when every milestone is
    percentage-type. Amount-based plans reconcile against pricing when
    available. text/compound milestones or unclassified plans route to
    manual review rather than failing.
  - Arithmetic tolerance of ±1 INR for rounding (developers round grand
    totals to the nearest rupee).
  - The model PROPOSES plan_type; the validator CONFIRMS it against
    structural rules. A mismatch flags review instead of silently storing a
    misclassification.
  - Derived signals (city, construction status) are stored as SIGNALS for
    internal matching against project master data — never published as facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config


@dataclass
class Check:
    check: str
    result: str                 # "pass" | "fail" | "warn" | "skip"
    detail: str = ""
    expected: float | None = None
    computed: float | None = None

    def to_dict(self) -> dict:
        d = {"check": self.check, "result": self.result}
        if self.detail:
            d["detail"] = self.detail
        if self.expected is not None:
            d["expected"] = self.expected
        if self.computed is not None:
            d["computed"] = self.computed
        return d


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    derived_signals: dict = field(default_factory=dict)

    @property
    def review_required(self) -> bool:
        return bool(self.review_reasons) or any(c.result == "fail" for c in self.checks)

    @property
    def status(self) -> str:
        return "review_required" if self.review_required else "auto_approved"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "review_required": self.review_required,
            "review_reasons": self.review_reasons,
            "checks": [c.to_dict() for c in self.checks],
            "derived_signals": self.derived_signals,
        }


# ---------------------------------------------------------------------------
# Plan-type structural classification (deterministic)
# ---------------------------------------------------------------------------

def _classify_plan_structurally(plan: dict) -> str:
    """Deterministic classification from milestone structure. Returns an enum
    value or 'other_unclassified' when structure is ambiguous."""
    milestones = plan.get("milestones", [])
    if not milestones:
        return "other_unclassified"

    types = {m.get("value_type") for m in milestones}
    if types - {"percentage"}:
        return "other_unclassified"  # mixed/amount/text plans need human or richer rules

    pct = [m["value"] for m in milestones if m.get("value") is not None]
    if not pct:
        return "other_unclassified"

    labels = " ".join((m.get("label") or "").lower() for m in milestones)

    construction_kw = ("slab", "foundation", "plinth", "brick", "plaster",
                       "excavation", "basement", "casting", "flooring",
                       "tiles", "structure")
    has_construction = any(k in labels for k in construction_kw)

    if len(pct) == 2 and max(pct) >= 60:
        # e.g. 10:90, 20:80, 30:70
        return "possession_linked" if "possession" in labels or "handover" in labels else "down_payment"
    if has_construction:
        return "construction_linked"
    if "possession" in labels or "handover" in labels:
        return "possession_linked"
    if any(k in labels for k in ("days", "months", "month")):
        return "time_linked"
    return "other_unclassified"


# ---------------------------------------------------------------------------
# Per-plan validation
# ---------------------------------------------------------------------------

def _validate_payment_plan(plan: dict, idx: int, report: ValidationReport) -> None:
    name = plan.get("plan_name_as_printed") or f"plan[{idx}]"
    milestones = plan.get("milestones", [])

    if not milestones:
        report.review_reasons.append(f"{name}: no milestones extracted.")
        report.checks.append(Check(f"{name}: milestones_present", "fail"))
        return

    value_types = [m.get("value_type") for m in milestones]

    # --- Rule set A: fully percentage-based plans -> sum-to-100 hard-ish gate
    if all(vt == "percentage" for vt in value_types):
        total = sum(m["value"] for m in milestones if m.get("value") is not None)
        printed = plan.get("milestones_total_printed")
        ok = abs(total - 100.0) <= config.PERCENTAGE_SUM_TOLERANCE
        report.checks.append(Check(
            f"{name}: percentage_sum", "pass" if ok else "fail",
            expected=100.0, computed=round(total, 4),
        ))
        if not ok:
            report.review_reasons.append(
                f"{name}: milestone percentages sum to {total}, not 100."
            )
        if printed is not None and abs(printed - total) > config.PERCENTAGE_SUM_TOLERANCE:
            report.checks.append(Check(
                f"{name}: printed_total_matches_computed", "fail",
                expected=printed, computed=round(total, 4),
            ))
            report.review_reasons.append(
                f"{name}: printed TOTAL ({printed}) disagrees with computed sum ({total})."
            )
        elif printed is not None:
            report.checks.append(Check(
                f"{name}: printed_total_matches_computed", "pass",
                expected=printed, computed=round(total, 4),
            ))

    # --- Rule set B: any text/compound/amount milestones -> soft review path
    else:
        mixed = sorted(set(value_types))
        report.checks.append(Check(
            f"{name}: percentage_sum", "skip",
            detail=f"Plan contains non-percentage milestones ({mixed}); sum-to-100 not applicable.",
        ))
        if "text" in value_types or "percentage_plus_amount" in value_types:
            report.review_reasons.append(
                f"{name}: contains text/compound milestones — human review before publish."
            )

    # --- Plan-type confirmation: model proposes, validator confirms
    proposed = plan.get("plan_type_proposed", "other_unclassified")
    structural = _classify_plan_structurally(plan)
    plan["plan_type_validated"] = structural
    if structural == "other_unclassified":
        report.checks.append(Check(
            f"{name}: plan_type_confirmation", "warn",
            detail=f"Structure inconclusive; model proposed '{proposed}'.",
        ))
        report.review_reasons.append(f"{name}: plan type could not be confirmed deterministically.")
    elif structural != proposed:
        report.checks.append(Check(
            f"{name}: plan_type_confirmation", "warn",
            detail=f"Model proposed '{proposed}' but structure indicates '{structural}'.",
        ))
        report.review_reasons.append(
            f"{name}: plan type mismatch (model: {proposed}, structural: {structural})."
        )
    else:
        report.checks.append(Check(
            f"{name}: plan_type_confirmation", "pass",
            detail=f"Model proposal '{proposed}' confirmed by structure.",
        ))


# ---------------------------------------------------------------------------
# Pricing reconciliation
# ---------------------------------------------------------------------------

_RATE_ROW_KEYWORDS = ("rpsf", "psf", "per sq", "rate", "bsp", "basic")


def _is_rate_row_component(component: dict, rate) -> bool:
    """A component is really the per-sq-ft RATE row duplicated into components
    (see extraction rule 8) when its amount equals rate_psf AND its label reads
    like a rate row. Kept deliberately tight so a genuine lump-sum charge that
    happens to look rate-like is never dropped."""
    if rate is None or component.get("amount") != rate:
        return False
    label = (component.get("label") or "").lower()
    return any(k in label for k in _RATE_ROW_KEYWORDS)


def _validate_pricing(pricing: dict, report: ValidationReport) -> None:
    tol = config.AMOUNT_TOLERANCE_INR
    rate = pricing.get("rate_psf")

    for unit in pricing.get("unit_types", []):
        label = f"{unit.get('unit_label', '?')}-{unit.get('sba_sqft', '?')}"
        sba = unit.get("sba_sqft")
        # A per-unit rate (e.g. Bren's per-variant "Rate per Square Feet",
        # Sikka's per-category rate) takes precedence over the sheet-wide rate.
        rate = unit.get("rate_psf") or pricing.get("rate_psf")
        agreement = unit.get("agreement_value")
        raw_components = unit.get("components", [])
        # A rate row wrongly duplicated into components would be double-counted
        # against SBA x rate; exclude it (never counted as a lump-sum charge).
        lump_sum = [c for c in raw_components if not _is_rate_row_component(c, rate)]
        rate_row_dropped = len(raw_components) - len(lump_sum)
        components = [c.get("amount") for c in lump_sum]

        # Agreement value = SBA x rate + sum(lump-sum components)
        if rate and sba and agreement is not None and all(a is not None for a in components):
            computed = sba * rate + sum(components)
            ok = abs(computed - agreement) <= tol
            report.checks.append(Check(
                f"{label}: agreement_value_reconciliation",
                "pass" if ok else "fail",
                expected=agreement, computed=round(computed, 2),
                detail=(f"Excluded {rate_row_dropped} duplicated rate row(s) from "
                        "lump-sum components." if rate_row_dropped else ""),
            ))
            if not ok:
                report.review_reasons.append(
                    f"{label}: agreement value {agreement} != computed {computed}."
                )
        else:
            report.checks.append(Check(
                f"{label}: agreement_value_reconciliation", "skip",
                detail="Missing rate/SBA/component amounts; cannot reconcile.",
            ))

        # GST amount = rate% of agreement value
        gst_rate, gst_amt = unit.get("gst_rate_percent"), unit.get("gst_amount")
        if gst_rate is not None and gst_amt is not None and agreement is not None:
            computed = agreement * gst_rate / 100.0
            ok = abs(computed - gst_amt) <= tol
            report.checks.append(Check(
                f"{label}: gst_reconciliation", "pass" if ok else "fail",
                expected=gst_amt, computed=round(computed, 2),
            ))
            if not ok:
                report.review_reasons.append(f"{label}: GST amount does not reconcile.")

        # Grand total = agreement + GST (± rounding)
        grand = unit.get("grand_total")
        if grand is not None and agreement is not None and gst_amt is not None:
            computed = agreement + gst_amt
            ok = abs(computed - grand) <= tol
            report.checks.append(Check(
                f"{label}: grand_total_reconciliation", "pass" if ok else "fail",
                expected=grand, computed=round(computed, 2),
                detail="" if abs(computed - grand) == 0 else
                       f"Rounding delta {abs(computed - grand):.2f} INR (within tolerance)" if ok else "",
            ))
            if not ok:
                report.review_reasons.append(f"{label}: grand total does not reconcile.")


# ---------------------------------------------------------------------------
# Derived signals (internal-only, never published as facts)
# ---------------------------------------------------------------------------

_CITY_UTILITY_HINTS = {
    "bwssb": "Bengaluru", "keb": "Bengaluru", "bescom": "Bengaluru",
    "mseb": "Maharashtra", "msedcl": "Maharashtra", "mahavitaran": "Maharashtra",
    "dhbvn": "Haryana (South)", "uhbvn": "Haryana (North)",
    "djb": "Delhi", "bses": "Delhi", "tneb": "Tamil Nadu",
    "hmwssb": "Hyderabad", "kseb": "Kerala",
}


def _derive_signals(extraction: dict) -> dict:
    signals: dict = {}
    blob = str(extraction).lower()

    for hint, city in _CITY_UTILITY_HINTS.items():
        if hint in blob:
            signals["city_inferred"] = city
            signals["city_inference_basis"] = f"Utility reference '{hint.upper()}' in document."
            break

    gst_present = "gst" in blob
    construction_plan = any(
        p.get("plan_type_validated") == "construction_linked"
        for p in extraction.get("payment_plans", [])
    )
    if gst_present or construction_plan:
        basis = []
        if gst_present:
            basis.append("GST line/condition present")
        if construction_plan:
            basis.append("construction-linked milestones")
        signals["construction_status_inferred"] = "under_construction"
        signals["status_inference_basis"] = " and ".join(basis)

    return signals


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate(extraction: dict) -> ValidationReport:
    """Run all deterministic checks. Mutates extraction in-place only to add
    plan_type_validated per plan. Returns the ValidationReport."""
    report = ValidationReport()

    doc = extraction.get("document", {})
    if doc.get("extraction_confidence") in ("medium", "low"):
        report.review_reasons.append(
            f"Model reported {doc.get('extraction_confidence')} confidence: "
            f"{doc.get('confidence_reason')}"
        )

    if not extraction.get("payment_plans") and doc.get("document_type") != "price_sheet_only":
        report.review_reasons.append("No payment plans extracted from a plan-bearing document.")

    for i, plan in enumerate(extraction.get("payment_plans", [])):
        _validate_payment_plan(plan, i, report)

    if extraction.get("pricing"):
        _validate_pricing(extraction["pricing"], report)

    report.derived_signals = _derive_signals(extraction)
    return report
