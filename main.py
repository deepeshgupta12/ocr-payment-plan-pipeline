#!/usr/bin/env python3
"""
Payment Plan Content Pipeline — CLI.

Usage:
    export OPENAI_API_KEY=sk-...
    python main.py <path-to-pdf-or-image> --project-id divine-green-leaf
    python main.py plan.pdf --project-id my-project --force-generate

Pipeline: Ingest -> Extract (OpenAI vision, structured outputs) -> Structure
          -> Validate -> Save JSON to datacenter -> Generate SEO/AEO content
          -> Post-generation numeric reconciliation.
"""

import argparse
import sys

from payment_pipeline import config
from payment_pipeline.pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Payment plan -> SEO/AEO content pipeline")
    parser.add_argument("input", help="Path to the payment plan / price sheet (PDF or image)")
    parser.add_argument("--project-id", required=True, help="Project identifier for the datacenter record")
    parser.add_argument("--output-dir", default="./output", help="Directory for generated content")
    parser.add_argument(
        "--force-generate", action="store_true",
        help="Generate content even when the record is review_required (post-review re-run)",
    )
    args = parser.parse_args()

    if not config.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    try:
        result = run_pipeline(
            input_path=args.input,
            project_id=args.project_id,
            output_dir=args.output_dir,
            force_generate=args.force_generate,
        )
    except Exception as exc:  # surfaced cleanly for ops
        print(f"PIPELINE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("\n".join(result.messages))
    print(f"\nRecord:            {result.record_path}")
    print(f"Validation status: {result.validation_status}")
    if result.content_status:
        print(f"Content status:    {result.content_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
