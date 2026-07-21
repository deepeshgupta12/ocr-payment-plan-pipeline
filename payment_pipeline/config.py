"""
Central configuration for the Payment Plan Content Pipeline.
All settings can be overridden via environment variables.

Requires: OPENAI_API_KEY set in the environment.
"""

import os

# Load variables from a project-root .env file into the environment, if present.
# Real environment variables take precedence over .env values (override=False),
# so container/CI-injected secrets are never clobbered by a local file.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:  # python-dotenv optional; env vars still work without it
    pass

# ---------------------------------------------------------------------------
# OpenAI settings
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Vision-capable model used for structured extraction from images/PDF pages.
EXTRACTION_MODEL = os.environ.get("PP_EXTRACTION_MODEL", "gpt-4o")

# Text model used for SEO/AEO content generation (can be the same model).
GENERATION_MODEL = os.environ.get("PP_GENERATION_MODEL", "gpt-4o")

# Deterministic-ish extraction; slight creativity allowed for content prose.
EXTRACTION_TEMPERATURE = 0.0
GENERATION_TEMPERATURE = 0.4

MAX_OUTPUT_TOKENS_EXTRACTION = 4096
# Generation can include several large tables; give it headroom so a big sheet
# (e.g. a many-row budget grid) does not truncate before the deterministic
# table injectors run.
MAX_OUTPUT_TOKENS_GENERATION = 8192

# ---------------------------------------------------------------------------
# Ingestion settings
# ---------------------------------------------------------------------------
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_PDF_EXTENSIONS = {".pdf"}

# DPI used when rasterising PDF pages to images for the vision model.
PDF_RENDER_DPI = int(os.environ.get("PP_PDF_RENDER_DPI", "180"))

# Hard cap on pages sent in a single extraction request. Payment plans /
# price sheets are short documents; anything longer needs manual triage.
MAX_PDF_PAGES = int(os.environ.get("PP_MAX_PDF_PAGES", "8"))

# ---------------------------------------------------------------------------
# Validation settings
# ---------------------------------------------------------------------------
# Tolerance (in INR) for arithmetic reconciliation checks. Developers round
# grand totals to the nearest rupee (e.g. 11,293,306.5 -> 11,293,307).
AMOUNT_TOLERANCE_INR = 1.0

# Tolerance (in percentage points) for milestone percentage sums.
PERCENTAGE_SUM_TOLERANCE = 0.5

# ---------------------------------------------------------------------------
# Datacenter (persistence) settings
# ---------------------------------------------------------------------------
# File-based store used by default. Swap DatacenterClient in datacenter.py
# with the real Square Yards datacenter API client in production.
DATACENTER_DIR = os.environ.get("PP_DATACENTER_DIR", "./datacenter_store")

# ---------------------------------------------------------------------------
# Content generation settings
# ---------------------------------------------------------------------------
# Numbers found in generated content must exist in the source JSON.
# The reconciliation pass ignores pure structural numbers below this value
# (sr numbers, heading levels etc. are 1-2 digit and always allowed).
POST_GEN_IGNORE_NUMBERS_BELOW = 13  # sr_no goes up to 12 in typical plans
