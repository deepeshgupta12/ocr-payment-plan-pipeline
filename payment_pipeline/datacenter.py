"""
Stage 5 — Format & Save to the Project Datacenter.

The validated extraction becomes the CANONICAL record. Content generation
reads from the datacenter, never from the extraction step directly — so
content can be regenerated any time (charter revisions, new AEO formats)
without re-touching the source PDF, and other consumers (comparison modules,
chatbot answers, lead-funnel tooling) can read the same record.

The default implementation is a file-based store. In production, replace
`FileDatacenterClient` with a client for the real Square Yards datacenter
API — the pipeline only depends on the `DatacenterClient` interface.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Protocol


class DatacenterClient(Protocol):
    def save(self, project_id: str, record: dict) -> str: ...
    def load(self, project_id: str) -> dict | None: ...


def build_record(project_id: str, extraction: dict, validation_report_dict: dict,
                 source_path: str) -> dict:
    """Assemble the canonical datacenter record."""
    return {
        "record_type": "payment_plan_extraction",
        "record_version": "1.0",
        "project_id": project_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {
            "file_name": os.path.basename(source_path),
            "file_path": source_path,
        },
        "extraction": extraction,
        "validation": validation_report_dict,
    }


class FileDatacenterClient:
    """File-based stand-in for the real datacenter API."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _path(self, project_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_id)
        return os.path.join(self.base_dir, f"{safe}.payment_plan.json")

    def save(self, project_id: str, record: dict) -> str:
        path = self._path(project_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        return path

    def load(self, project_id: str) -> dict | None:
        path = self._path(project_id)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
