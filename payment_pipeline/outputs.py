"""
Analysis output artifacts.

Splits the pipeline's intermediate and final results into separate,
inspectable files under ``output/<project_id>/`` so each stage of a payment
plan analysis can be reviewed on its own:

  01_image_analysis/         — the rendered page image(s) the vision model saw,
                               an ingest manifest, and an AI visual description
                               of the document (layout / legibility / quality)
  02_extracted_data.json     — the raw, verbatim extraction from the vision
                               model (pre-validation, no normalisation)
  03_structured_data.json    — the extraction reorganised for review, with the
                               deterministic validation report attached
  04_record.json             — the canonical datacenter record (as stored)
  05_seo_aeo_content.json    — the generated SEO/AEO content + reconciliation

Every stage that runs writes its artifact; skipped stages (e.g. content
generation gated behind review) simply produce no file. Artifact writing is
best-effort and never aborts the pipeline — the AI visual description in
particular degrades to a note on failure.
"""

from __future__ import annotations

import base64
import json
import os

from openai import OpenAI

from . import config
from .ingest import IngestResult

_MEDIA_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

VISUAL_ANALYSIS_SYSTEM_PROMPT = """\
You are a document-quality reviewer for Indian real estate payment plans and
price sheets. You are NOT extracting the data (a separate step does that).
Describe the document at a glance so a human triager knows what they are
looking at and whether the capture is clean.

Cover, in a few short paragraphs:
- Document type and overall layout (single plan, multiple plans side by side,
  a pricing grid, mixed).
- Table structure: how many tables, rough row/column counts, headers present.
- Legibility and capture quality: blur, skew, glare, cropping, watermarks,
  handwriting, stamps — anything that could hurt extraction accuracy.
- Any obvious ambiguities a reviewer should double-check.

Be factual and concise. Do not transcribe every cell; do not invent content.
"""


def _safe_project_id(project_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)


def project_output_dir(base_output_dir: str, project_id: str) -> str:
    """Return (creating it) the per-project analysis output directory."""
    path = os.path.join(base_output_dir, _safe_project_id(project_id))
    os.makedirs(path, exist_ok=True)
    return path


def _write_json(path: str, obj) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def _describe_document(ingested: IngestResult, client: OpenAI) -> str:
    """One vision call that narrates the document's layout and capture quality."""
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
    user_content.append(
        {"type": "text", "text": "Describe this document's layout and capture quality."}
    )

    response = client.chat.completions.create(
        model=config.EXTRACTION_MODEL,
        temperature=0.2,
        max_tokens=600,
        messages=[
            {"role": "system", "content": VISUAL_ANALYSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    return (response.choices[0].message.content or "").strip()


def write_image_analysis(
    out_dir: str, ingested: IngestResult, client: OpenAI | None = None
) -> str:
    """Write 01_image_analysis/: rendered pages, an ingest manifest, and an AI
    visual description. Returns the sub-directory path."""
    img_dir = os.path.join(out_dir, "01_image_analysis")
    os.makedirs(img_dir, exist_ok=True)

    ext = _MEDIA_TYPE_EXT.get(ingested.image_media_type, ".png")
    page_files: list[str] = []
    for i, img_b64 in enumerate(ingested.images_b64, start=1):
        page_name = f"page_{i}{ext}"
        with open(os.path.join(img_dir, page_name), "wb") as f:
            f.write(base64.b64decode(img_b64))
        page_files.append(page_name)

    # AI visual description (best-effort; a failure must not abort the run).
    client = client or OpenAI(api_key=config.OPENAI_API_KEY)
    try:
        description = _describe_document(ingested, client)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        description = (
            f"[visual analysis unavailable: {type(exc).__name__}: {exc}]"
        )
    with open(os.path.join(img_dir, "visual_analysis.txt"), "w", encoding="utf-8") as f:
        f.write(description + "\n")

    _write_json(
        os.path.join(img_dir, "manifest.json"),
        {
            "source_path": ingested.source_path,
            "source_type": ingested.source_type,
            "page_count": ingested.page_count,
            "image_media_type": ingested.image_media_type,
            "native_text_layer_present": ingested.native_text is not None,
            "pages": page_files,
            "visual_analysis_file": "visual_analysis.txt",
        },
    )
    return img_dir


def write_extracted_data(out_dir: str, extraction: dict) -> str:
    """Write 02_extracted_data.json — the raw verbatim model extraction."""
    return _write_json(os.path.join(out_dir, "02_extracted_data.json"), extraction)


def write_structured_data(
    out_dir: str, extraction: dict, validation_report: dict
) -> str:
    """Write 03_structured_data.json — the extraction split into a review-
    friendly structure with the validation report attached."""
    doc = extraction.get("document", {})
    structured = {
        "document": doc,
        "payment_plans": extraction.get("payment_plans", []),
        "pricing": extraction.get("pricing"),
        # Signals live on the validation report (validate() computes them there),
        # not on the raw extraction.
        "derived_signals": (validation_report or {}).get("derived_signals"),
        "validation": validation_report,
    }
    return _write_json(os.path.join(out_dir, "03_structured_data.json"), structured)


def write_record(out_dir: str, record: dict) -> str:
    """Write 04_record.json — a copy of the canonical datacenter record."""
    return _write_json(os.path.join(out_dir, "04_record.json"), record)


def write_seo_aeo_content(
    out_dir: str, content_status: str, reconciliation: dict, content: dict
) -> str:
    """Write 05_seo_aeo_content.json — generated content + reconciliation."""
    return _write_json(
        os.path.join(out_dir, "05_seo_aeo_content.json"),
        {
            "content_status": content_status,
            "reconciliation": reconciliation,
            "content": content,
        },
    )


# ---------------------------------------------------------------------------
# HTML view of the generated content (06)
# ---------------------------------------------------------------------------

_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="__META__">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #f4f5f7; color: #1b1f24;
         font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         line-height: 1.6; }
  .container { max-width: 860px; margin: 0 auto; padding: 32px 20px 64px; }
  .card { background: #ffffff; border: 1px solid #e3e6ea; border-radius: 12px;
          padding: 24px 28px; margin-bottom: 24px;
          box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
  h1 { font-size: 1.4rem; margin: 0 0 4px; }
  .muted { color: #5c6773; font-size: 0.9rem; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px;
           font-size: 0.8rem; font-weight: 600; }
  .badge.ok { background: #e5f5ea; color: #1a7f43; }
  .badge.review { background: #fdf1dc; color: #9a6400; }
  .metabox { font-size: 0.9rem; background: #f7f9fb; border-left: 3px solid #c7ced6;
             padding: 10px 14px; border-radius: 6px; margin-top: 12px; }
  h2.section { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.06em;
               color: #7a8593; margin: 0 0 14px; font-weight: 700; }
  h3 { font-size: 1.12rem; margin: 22px 0 6px; color: #10233b; }
  section > h3:first-child, article > section > h3:first-child { margin-top: 0; }
  p { margin: 6px 0 10px; }
  ul { margin: 6px 0 14px; padding-left: 22px; }
  li { margin: 3px 0; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0 18px;
          font-size: 0.94rem; }
  th, td { border: 1px solid #dde2e8; padding: 8px 12px; text-align: left; }
  thead th { background: #f0f3f6; }
  caption { text-align: left; font-weight: 700; padding: 10px 0 6px; color: #10233b; }
  .tables-wrap { overflow-x: auto; }
  @media (prefers-color-scheme: dark) {
    body { background: #14181d; color: #e6e9ee; }
    .card { background: #1d232b; border-color: #2b333d; box-shadow: none; }
    .metabox { background: #232b34; border-left-color: #3a4552; }
    h3, caption { color: #cfe0f2; } thead th { background: #262f39; }
    th, td { border-color: #313a45; }
    .badge.ok { background: #17331f; color: #6fd694; }
    .badge.review { background: #3a2e15; color: #e0b357; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <h1>__PROJECT__</h1>
    <div class="muted">Payment Plan, SEO and AEO content &nbsp;|&nbsp; project id: __PID__</div>
    <div style="margin-top:10px">
      <span class="badge __STATUS_CLASS__">__STATUS__</span>
      <span class="muted">numeric reconciliation: __RECON__</span>
    </div>
    <div class="metabox"><strong>Meta description:</strong> __META__</div>
  </div>

  <div class="card">
    <h2 class="section">Content (SEO and AEO)</h2>
    <article>__PROSE__</article>
  </div>
__EXTRA_TABLES__
</div>
</body>
</html>
"""


def _esc(text: str) -> str:
    """Minimal escape for text placed in an attribute or text node."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_seo_aeo_html(
    out_dir: str,
    project_id: str,
    project_name: str | None,
    content_status: str,
    reconciliation: dict,
    content: dict,
) -> str:
    """Write 06_seo_aeo_content.html — a standalone, browsable rendering of the
    generated content package (prose plus the standalone table blocks)."""
    prose = content.get("seo_prose_html") or ""
    schedule = content.get("payment_schedule_table_html") or ""
    pricing = content.get("pricing_table_html") or ""
    meta = content.get("meta_description") or ""

    # The standalone table blocks exist as separate keys so they can be injected
    # into their own PDP slots; show them here under a clearly labelled section.
    extra_blocks = ""
    if schedule.strip():
        extra_blocks += f'<h2 class="section">Payment schedule table (standalone block)</h2><div class="tables-wrap">{schedule}</div>'
    if pricing.strip():
        extra_blocks += f'<h2 class="section">Pricing table (standalone block)</h2><div class="tables-wrap">{pricing}</div>'
    extra_tables = (
        f'<div class="card">{extra_blocks}</div>' if extra_blocks else ""
    )

    title = f"{project_name or project_id} — Payment Plan Content"
    status_class = "ok" if content_status == "publishable" else "review"

    html = (
        _HTML_PAGE
        .replace("__META__", _esc(meta))
        .replace("__TITLE__", _esc(title))
        .replace("__PROJECT__", _esc(project_name or project_id))
        .replace("__PID__", _esc(project_id))
        .replace("__STATUS_CLASS__", status_class)
        .replace("__STATUS__", _esc(content_status))
        .replace("__RECON__", _esc(reconciliation.get("result", "n/a")))
        .replace("__PROSE__", prose)
        .replace("__EXTRA_TABLES__", extra_tables)
    )

    path = os.path.join(out_dir, "06_seo_aeo_content.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
