"""
Orchestrator — wires the six stages together.

    Ingest -> Extract -> Structure -> Validate -> Save JSON to datacenter
           -> Content generation (from the datacenter record)
           -> Post-generation numeric reconciliation

Routing rules:
  - Extraction failures abort with a clear error.
  - Validation failures/warnings still SAVE the record (with
    status=review_required) so nothing is lost, but content generation is
    skipped unless force_generate=True.
  - Post-generation reconciliation failure marks the CONTENT (not the
    record) as review_required; it is written alongside but flagged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from openai import OpenAI

from . import config, outputs
from .content_generation import generate_content, reconcile_numbers
from .datacenter import FileDatacenterClient, build_record
from .extraction import extract
from .ingest import ingest
from .validation import validate


@dataclass
class PipelineResult:
    project_id: str
    record_path: str
    validation_status: str
    content: dict | None = None
    content_status: str | None = None       # "publishable" | "review_required" | None
    reconciliation: dict | None = None
    messages: list[str] = field(default_factory=list)


def run_pipeline(
    input_path: str,
    project_id: str,
    output_dir: str = "./output",
    force_generate: bool = False,
    client: OpenAI | None = None,
) -> PipelineResult:
    client = client or OpenAI(api_key=config.OPENAI_API_KEY)
    os.makedirs(output_dir, exist_ok=True)
    proj_out = outputs.project_output_dir(output_dir, project_id)
    messages: list[str] = []

    # Stage 1 — Ingest -----------------------------------------------------
    ingested = ingest(input_path)
    messages.append(
        f"[ingest] {ingested.source_type} | {ingested.page_count} page(s)"
        + (" | native text layer present" if ingested.native_text else "")
    )
    img_dir = outputs.write_image_analysis(proj_out, ingested, client=client)
    messages.append(f"[output] image analysis -> {img_dir}")

    # Stages 2+3 — Extract & structure -------------------------------------
    extraction = extract(ingested, client=client)
    doc = extraction.get("document", {})
    messages.append(
        f"[extract] type={doc.get('document_type')} | project={doc.get('project_name')} "
        f"| confidence={doc.get('extraction_confidence')} "
        f"| plans={len(extraction.get('payment_plans', []))}"
    )
    messages.append(f"[output] extracted data -> {outputs.write_extracted_data(proj_out, extraction)}")

    # Stage 4 — Validate ----------------------------------------------------
    report = validate(extraction)
    messages.append(f"[validate] {report.status} | {len(report.checks)} checks")
    for reason in report.review_reasons:
        messages.append(f"[validate]   review: {reason}")
    messages.append(
        f"[output] structured data -> "
        f"{outputs.write_structured_data(proj_out, extraction, report.to_dict())}"
    )

    # Stage 5 — Format & save to datacenter ----------------------------------
    record = build_record(project_id, extraction, report.to_dict(), input_path)
    dc = FileDatacenterClient(config.DATACENTER_DIR)
    record_path = dc.save(project_id, record)
    messages.append(f"[datacenter] saved canonical record -> {record_path}")
    messages.append(f"[output] record -> {outputs.write_record(proj_out, record)}")

    result = PipelineResult(
        project_id=project_id,
        record_path=record_path,
        validation_status=report.status,
        messages=messages,
    )

    # Stage 6 — Content generation (reads the datacenter record) -------------
    if report.review_required and not force_generate:
        messages.append(
            "[content] skipped: record is review_required. "
            "Re-run with force_generate=True after human review."
        )
        return result

    saved_record = dc.load(project_id)  # generation reads the canonical record
    content = generate_content(saved_record, client=client)
    reconciliation = reconcile_numbers(saved_record, content)
    content_status = "publishable" if reconciliation["result"] == "pass" else "review_required"
    messages.append(f"[content] generated | reconciliation={reconciliation['result']}")
    if reconciliation["result"] != "pass":
        messages.append(f"[content]   violations: {reconciliation['violations']}")

    content_path = outputs.write_seo_aeo_content(
        proj_out, content_status, reconciliation, content
    )
    messages.append(f"[output] SEO/AEO content -> {content_path}")
    html_path = outputs.write_seo_aeo_html(
        proj_out, project_id, doc.get("project_name"),
        content_status, reconciliation, content,
    )
    messages.append(f"[output] SEO/AEO content (HTML view) -> {html_path}")

    result.content = content
    result.content_status = content_status
    result.reconciliation = reconciliation
    return result
