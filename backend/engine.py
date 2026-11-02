"""Deterministic reconciliation and evidence-linked investigation."""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from uuid import uuid4


MONEY_FIELDS = ("taxable_value", "igst", "cgst", "sgst")
LABELS = {"taxable_value": "Taxable value", "igst": "IGST", "cgst": "CGST", "sgst": "SGST"}


def key(record: dict) -> tuple[str, str]:
    return record["supplier_gstin"], record["invoice_key"]


def _evidence_for(record: dict, documents: list[dict]) -> list[dict]:
    invoice = record["invoice_number"]
    normalized_invoice = record["invoice_key"]
    gstin = record["supplier_gstin"]
    found = []
    for document in documents:
        for page in document["pages"]:
            text = page["text"]
            compact = re.sub(r"[^A-Z0-9]", "", text.upper())
            if not normalized_invoice or normalized_invoice not in compact:
                continue
            if gstin and gstin not in compact and document["kind"] not in {"email", "other"}:
                continue
            match = re.search(re.escape(invoice), text, re.IGNORECASE)
            if match:
                snippet = text[max(0, match.start() - 100) : match.end() + 180]
            else:
                snippet = text[:240]
            found.append({"file": document["filename"], "page": page["page"], "kind": document["kind"], "excerpt": " ".join(snippet.split())})
    return found[:10]


def _case(record: dict, other: dict | None, category: str, differences: list[str], evidence: list[dict], finding: str, confidence: str, action: str) -> dict:
    return {
        "id": str(uuid4()),
        "invoice_number": record["invoice_number"],
        "supplier_gstin": record["supplier_gstin"],
        "supplier_name": record["supplier_name"] or (other or {}).get("supplier_name", ""),
        "invoice_date": record["invoice_date"],
        "category": category,
        "differences": differences,
        "purchase_record": record if record.get("side") == "purchase" else None,
        "gstr_record": other if other and other.get("side") == "gstr" else (record if record.get("side") == "gstr" else None),
        "evidence": evidence,
        "finding": finding,
        "confidence": confidence,
        "recommended_action": action,
        "review_status": "Needs review",
        "review_note": "",
        "record_ids": [item["record_id"] for item in (record, other) if item and item.get("record_id")],
    }


def _investigate(record: dict, category: str, evidence: list[dict], later: dict | None) -> tuple[str, str, str]:
    if category == "Missing in GSTR-2B":
        if later:
            return (
                f"An entry with the same GSTIN and invoice number appears in a later GSTR-2B: {later['source']}. This supports later-period reporting; verify dates and amounts before deciding on ITC treatment.",
                "High (record match)",
                "Check the later statement and original invoice; have an accountant confirm the period and treatment.",
            )
        emails = [item for item in evidence if item["kind"] == "email"]
        if emails:
            return (
                "Supplier correspondence mentions this invoice, but no matching entry was found in the supplied GSTR-2B statements. The reporting reason remains unverified.",
                "Low (document mention)",
                "Ask the supplier to verify filing details and check a later GSTR-2B.",
            )
        return (
            "No corresponding entry was found in the supplied GSTR-2B statements. The cause cannot be established from the available records.",
            "Unresolved",
            "Verify the invoice and GSTIN with the supplier; check subsequent GSTR-2B statements.",
        )
    if category in {"Value or tax mismatch", "Invoice date mismatch"}:
        notes = [item for item in evidence if item["kind"] in {"credit note", "debit note", "amendment"}]
        if notes:
            names = ", ".join(sorted({item["file"] for item in notes}))
            return (
                f"A related note or amendment mentions this invoice ({names}). It may explain the difference, but its amounts and filing period require verification.",
                "Medium (related document)",
                "Compare the note or amendment with the original invoice and both ledgers.",
            )
        return (
            "The records differ, and the supplied evidence does not establish the cause.",
            "Unresolved",
            "Compare the original invoice, any credit or debit notes, and supplier amendments.",
        )
    if category == "Duplicate entry":
        return (
            "The same GSTIN and normalized invoice number occurs more than once in one source. Confirm whether these are duplicate postings or distinct amendments.",
            "High (duplicate key)",
            "Inspect the original invoice and amendment history before correcting the register.",
        )
    if category == "Invoice number formatting difference":
        return (
            "The invoice numbers match only after punctuation and case normalization. Verify that both entries refer to the same original invoice.",
            "Medium (normalized match)",
            "Compare the source invoice numbers and confirm the match before closing this case.",
        )
    return (
        "The invoice number matches, but supplier GSTIN differs. This requires manual verification.",
        "High (field mismatch)",
        "Check the original invoice and both GSTINs; correct the source only after review.",
    )


def reconcile(purchase: list[dict], gstr: list[dict], later_gstr: list[dict], documents: list[dict]) -> dict:
    purchase = [{**record, "side": "purchase"} for record in purchase]
    gstr = [{**record, "side": "gstr"} for record in gstr]
    by_purchase = defaultdict(list)
    by_gstr = defaultdict(list)
    by_later = {key(record): record for record in later_gstr}
    for record in purchase:
        by_purchase[key(record)].append(record)
    for record in gstr:
        by_gstr[key(record)].append(record)

    cases = []
    matched = 0
    used_gstr = set()
    for invoice_key, purchase_rows in by_purchase.items():
        record = purchase_rows[0]
        related = _evidence_for(record, documents)
        if len(purchase_rows) > 1:
            sources = [item["source"] for item in purchase_rows]
            finding, confidence, action = _investigate(record, "Duplicate entry", related, None)
            duplicate_case = _case(record, None, "Duplicate entry", sources, related, finding, confidence, action)
            duplicate_case["record_ids"] = [item["record_id"] for item in purchase_rows if item.get("record_id")]
            cases.append(duplicate_case)
        gstr_rows = by_gstr.get(invoice_key, [])
        if gstr_rows:
            used_gstr.add(invoice_key)
            other = gstr_rows[0]
            if len(gstr_rows) > 1:
                sources = [item["source"] for item in gstr_rows]
                finding, confidence, action = _investigate(other, "Duplicate entry", related, None)
                duplicate_case = _case(other, None, "Duplicate entry", sources, related, finding, confidence, action)
                duplicate_case["record_ids"] = [item["record_id"] for item in gstr_rows if item.get("record_id")]
                cases.append(duplicate_case)
            differences = []
            for field in MONEY_FIELDS:
                if Decimal(record[field]) != Decimal(other[field]):
                    differences.append(f"{LABELS[field]}: purchase ₹{record[field]} vs GSTR-2B ₹{other[field]}")
            date_difference = record["invoice_date"] and other["invoice_date"] and record["invoice_date"] != other["invoice_date"]
            if date_difference:
                differences.append(f"Invoice date: purchase {record['invoice_date']} vs GSTR-2B {other['invoice_date']}")
            if record["invoice_number"] != other["invoice_number"]:
                differences.append(f"Invoice number formatting: purchase {record['invoice_number']} vs GSTR-2B {other['invoice_number']}")
            if differences:
                category = "Value or tax mismatch" if any(item.startswith(tuple(LABELS.values())) for item in differences) else "Invoice date mismatch" if date_difference else "Invoice number formatting difference"
                finding, confidence, action = _investigate(record, category, related, None)
                cases.append(_case(record, other, category, differences, related, finding, confidence, action))
            elif len(purchase_rows) == 1 and len(gstr_rows) == 1:
                matched += 1
            continue

        candidates = [(k, rows[0]) for k, rows in by_gstr.items() if k not in used_gstr and k not in by_purchase and k[1] == record["invoice_key"] and k[0] != record["supplier_gstin"]]
        if len(candidates) == 1:
            candidate_key, other = candidates[0]
            used_gstr.add(candidate_key)
            differences = [f"GSTIN: purchase {record['supplier_gstin']} vs GSTR-2B {other['supplier_gstin']}"]
            finding, confidence, action = _investigate(record, "GSTIN mismatch", related, None)
            cases.append(_case(record, other, "GSTIN mismatch", differences, related, finding, confidence, action))
        else:
            finding, confidence, action = _investigate(record, "Missing in GSTR-2B", related, by_later.get(invoice_key))
            if invoice_key in by_later:
                later = by_later[invoice_key]
                related = related + [{"file": later["source"].split(" (")[0], "page": 1, "kind": "later GSTR-2B", "excerpt": f"{later['supplier_gstin']} {later['invoice_number']} taxable ₹{later['taxable_value']}"}]
            cases.append(_case(record, None, "Missing in GSTR-2B", ["No matching GSTIN + invoice number in current GSTR-2B"], related, finding, confidence, action))

    for invoice_key, rows in by_gstr.items():
        if invoice_key in used_gstr:
            continue
        record = rows[0]
        related = _evidence_for(record, documents)
        finding = "This GSTR-2B entry has no matching purchase-register invoice in the supplied period. Check whether it belongs in the register or another period."
        cases.append(_case(record, None, "Missing in purchase register", ["No matching GSTIN + invoice number in purchase register"], related, finding, "Unresolved", "Verify the original invoice, receipt, and accounting period."))
        if len(rows) > 1:
            duplicate_finding, confidence, action = _investigate(record, "Duplicate entry", related, None)
            duplicate_case = _case(record, None, "Duplicate entry", [item["source"] for item in rows], related, duplicate_finding, confidence, action)
            duplicate_case["record_ids"] = [item["record_id"] for item in rows if item.get("record_id")]
            cases.append(duplicate_case)

    counts = defaultdict(int)
    for case in cases:
        counts[case["category"]] += 1
    return {"matched": matched, "purchase_count": len(purchase), "gstr_count": len(gstr), "case_count": len(cases), "categories": dict(counts), "cases": cases}
