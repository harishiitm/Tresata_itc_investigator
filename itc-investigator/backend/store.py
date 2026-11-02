"""Local run storage and accountant review state."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.engine import reconcile
from backend.ingest import amount, normalize_date, normalize_invoice


ROOT = Path(__file__).resolve().parents[1] / "data" / "runs"


def create_run() -> tuple[str, Path]:
    run_id = str(uuid4())
    folder = ROOT / run_id
    folder.mkdir(parents=True, exist_ok=False)
    return run_id, folder


def save_upload(folder: Path, uploaded, index: int) -> Path:
    if uploaded.size > 15 * 1024 * 1024:
        raise ValueError(f"{uploaded.name}: maximum file size is 15 MB.")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(uploaded.name).name)
    if not name or name.startswith("."):
        name = "document"
    path = folder / f"{index:02d}_{name}"
    path.write_bytes(uploaded.getvalue())
    return path


def write_result(folder: Path, result: dict) -> None:
    result["created_at"] = result.get("created_at") or datetime.now(timezone.utc).isoformat()
    target = folder / "result.json"
    temporary = folder / "result.tmp"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def load_result(run_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        raise ValueError("Invalid run ID")
    return json.loads((ROOT / run_id / "result.json").read_text(encoding="utf-8"))


def list_runs() -> list[tuple[str, dict]]:
    if not ROOT.exists():
        return []
    runs = []
    for folder in ROOT.iterdir():
        if (folder / "result.json").exists():
            try:
                result = load_result(folder.name)
                runs.append((folder.name, result))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return sorted(runs, key=lambda item: item[1].get("created_at", ""), reverse=True)


def update_review(run_id: str, case_id: str, status: str, note: str) -> None:
    if status not in {"Needs review", "Reviewed", "Needs follow-up"}:
        raise ValueError("Invalid review status")
    result = load_result(run_id)
    for case in result["cases"]:
        if case["id"] == case_id:
            case["review_status"] = status
            case["review_note"] = note.strip()[:2000]
            write_result(ROOT / run_id, result)
            return
    raise ValueError("Case not found")


EDITABLE_FIELDS = ("supplier_gstin", "supplier_name", "invoice_number", "invoice_date", "taxable_value", "igst", "cgst", "sgst")
RECORD_GROUPS = {"purchase": "purchase_records", "gstr": "gstr_records", "later": "later_records"}


def _case_signature(case: dict) -> tuple:
    return (
        case["category"],
        (case.get("purchase_record") or {}).get("record_id"),
        (case.get("gstr_record") or {}).get("record_id"),
    )


def update_record(run_id: str, group: str, record_id: str, changes: dict) -> bool:
    """Apply a human correction, retain old values, and rerun reconciliation."""
    if group not in RECORD_GROUPS:
        raise ValueError("Invalid record group")
    result = load_result(run_id)
    if not all(name in result for name in (*RECORD_GROUPS.values(), "documents")):
        raise ValueError("This older run cannot be edited; upload its source files again.")
    record = next((item for item in result[RECORD_GROUPS[group]] if item.get("record_id") == record_id), None)
    if record is None:
        raise ValueError("Record not found")
    if set(changes) - set(EDITABLE_FIELDS):
        raise ValueError("Invalid correction field")
    clean = {}
    for field in EDITABLE_FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if field in {"taxable_value", "igst", "cgst", "sgst"}:
            clean[field] = amount(value)
        elif field == "invoice_date":
            clean[field] = normalize_date(value)
        elif field == "supplier_gstin":
            clean[field] = re.sub(r"\s+", "", str(value or "")).upper()
        else:
            clean[field] = str(value or "").strip()
    if not clean.get("supplier_gstin", record["supplier_gstin"]) or not clean.get("invoice_number", record["invoice_number"]):
        raise ValueError("GSTIN and invoice number cannot be blank")
    if not normalize_invoice(clean.get("invoice_number", record["invoice_number"])):
        raise ValueError("Invoice number needs at least one letter or digit")
    corrected_date = clean.get("invoice_date", record["invoice_date"])
    if corrected_date:
        try:
            datetime.fromisoformat(corrected_date)
        except ValueError as exc:
            raise ValueError("Invoice date must be YYYY-MM-DD or DD/MM/YYYY") from exc
    changed = {field: {"before": record[field], "after": value} for field, value in clean.items() if record[field] != value}
    if not changed:
        return False
    record.update(clean)
    record["invoice_key"] = normalize_invoice(record["invoice_number"])
    record.setdefault("human_edits", []).append({"at": datetime.now(timezone.utc).isoformat(), "changes": changed})

    old_cases = {_case_signature(case): case for case in result["cases"]}
    refreshed = reconcile(result["purchase_records"], result["gstr_records"], result["later_records"], result["documents"])
    for case in refreshed["cases"]:
        old = old_cases.get(_case_signature(case))
        affected = record_id in set(case.get("record_ids", [])) or record_id in set((old or {}).get("record_ids", []))
        if old and not affected:
            case["id"] = old["id"]
            case["review_status"] = old["review_status"]
            case["review_note"] = old["review_note"]
            if "ai_analysis" in old:
                case["ai_analysis"] = old["ai_analysis"]
    result.update(refreshed)
    result.pop("ai_summary", None)
    write_result(ROOT / run_id, result)
    return True


def save_ai_analysis(run_id: str, case_id: str, analysis: dict) -> None:
    result = load_result(run_id)
    for case in result["cases"]:
        if case["id"] == case_id:
            case["ai_analysis"] = analysis
            case.pop("ai_analyses", None)
            case.pop("ai_error", None)
            write_result(ROOT / run_id, result)
            return
    raise ValueError("Case not found")


def save_ai_error(run_id: str, case_id: str, message: str) -> None:
    result = load_result(run_id)
    for case in result["cases"]:
        if case["id"] == case_id:
            case["ai_error"] = message[:500]
            write_result(ROOT / run_id, result)
            return
    raise ValueError("Case not found")


def set_analysis_mode(run_id: str, mode: str) -> None:
    if mode not in {"Local only", "OpenRouter"}:
        raise ValueError("Invalid analysis mode")
    result = load_result(run_id)
    result["analysis_mode"] = mode
    write_result(ROOT / run_id, result)
