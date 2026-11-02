"""Generate a portable CSV report with cited findings and review state."""

import csv
import io
import re

from openpyxl import Workbook
from openpyxl.styles import Font

from backend.ai import MODEL_ID


COLUMNS = ["Invoice", "Supplier GSTIN", "Supplier", "Category", "Differences", "Finding", "Confidence", "Evidence", "Recommended action", "Review status", "Review note", "Human edited", "AI explanation", "AI assessment", "AI action", "AI evidence", "AI model"]


def safe_cell(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def active_ai(case: dict) -> dict:
    analysis = case.get("ai_analysis") or {}
    return analysis if analysis.get("model") == MODEL_ID else {}


def case_rows(result: dict):
    for case in result["cases"]:
        ai = active_ai(case)
        edited = any((case.get(side) or {}).get("human_edits") for side in ("purchase_record", "gstr_record"))
        yield [safe_cell(value) for value in [
            case["invoice_number"], case["supplier_gstin"], case["supplier_name"], case["category"],
            "; ".join(case["differences"]), case["finding"], case["confidence"],
            "; ".join(f"{item['file']} p.{item['page']}: {item['excerpt']}" for item in case["evidence"]),
            case["recommended_action"], case["review_status"], case["review_note"],
            "Yes" if edited else "No", ai.get("explanation", ""), ai.get("assessment", ""),
            ai.get("recommended_action", ""), "; ".join(f"{item['id']}: {item['source']}" for item in ai.get("sources", [])), ai.get("model", ""),
        ]]


def summary_text(result: dict) -> str:
    edited = sum(bool(record.get("human_edits")) for group in ("purchase_records", "gstr_records", "later_records") for record in result.get(group, []))
    ai_count = sum(bool(active_ai(case)) for case in result["cases"])
    categories = ", ".join(f"{name}: {count}" for name, count in sorted(result["categories"].items())) or "none"
    overview = (
        f"ITC discrepancy investigation summary\n"
        f"Purchase records: {result['purchase_count']}\nGSTR-2B records: {result['gstr_count']}\n"
        f"Matched invoices: {result['matched']}\nInvestigation cases: {result['case_count']}\n"
        f"Case categories: {categories}\nHuman-edited records: {edited}\n"
        f"Cases with optional AI analysis: {ai_count}\n"
        "All cases require accountant verification. AI explanations are suggestions, not ITC eligibility decisions.\n"
    )
    details = []
    for case in result["cases"]:
        provenance = "human edited" if any((case.get(side) or {}).get("human_edits") for side in ("purchase_record", "gstr_record")) else "extracted"
        details.append(f"\n{case['invoice_number']} | {case['category']} | {case['review_status']} | {provenance}\n")
        details.append(f"Finding: {case['finding']}\n")
        details.append(f"Next step: {case['recommended_action']}\n")
        analysis = active_ai(case)
        if analysis:
            refs = ", ".join(f"{item['id']} {item['source']}" for item in analysis["sources"])
            details.append(f"AI ({analysis['model']}, {analysis['assessment']}): {analysis['explanation']}\n")
            details.append(f"AI references: {refs}\n")
    return overview + "".join(details)


def csv_report(result: dict) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    writer.writerows(case_rows(result))
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def xlsx_report(result: dict) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    for label, value in (
        ("Purchase invoices", result["purchase_count"]),
        ("GSTR-2B invoices", result["gstr_count"]),
        ("Matched invoices", result["matched"]),
        ("Investigation cases", result["case_count"]),
    ):
        summary.append([label, value])
    for category, count in sorted(result["categories"].items()):
        summary.append([category, count])
    summary.append(["Human-edited records", sum(bool(record.get("human_edits")) for group in ("purchase_records", "gstr_records", "later_records") for record in result.get(group, []))])
    summary.append(["Cases with AI analysis", sum(bool(active_ai(case)) for case in result["cases"])])
    summary.column_dimensions["A"].width = 34

    cases = workbook.create_sheet("Cases")
    cases.append(COLUMNS)
    for cell in cases[1]:
        cell.font = Font(bold=True)
    for row in case_rows(result):
        cases.append(row)
    cases.freeze_panes = "A2"
    cases.auto_filter.ref = cases.dimensions
    for column in "ABCDEFGHIJKLMNOPQ":
        cases.column_dimensions[column].width = 28 if column not in "FEI" else 60

    corrections = workbook.create_sheet("Human corrections")
    corrections.append(["Source", "Record ID", "Invoice", "Edited at UTC", "Field", "Before", "After"])
    for group in ("purchase_records", "gstr_records", "later_records"):
        for record in result.get(group, []):
            for edit in record.get("human_edits", []):
                for field, values in edit["changes"].items():
                    corrections.append([safe_cell(value) for value in (group, record.get("record_id", ""), record["invoice_number"], edit["at"], field, values["before"], values["after"])])
    corrections.freeze_panes = "A2"
    for column in "ABCDEFG":
        corrections.column_dimensions[column].width = 26

    ai_sheet = workbook.create_sheet("AI analyses")
    ai_sheet.append(["Invoice", "Category", "Model", "Assessment", "Explanation", "Recommended action", "Evidence references", "Generated at UTC"])
    for case in result["cases"]:
        analysis = active_ai(case)
        if analysis:
            ai_sheet.append([safe_cell(value) for value in (
                case["invoice_number"], case["category"], analysis["model"], analysis["assessment"],
                analysis["explanation"], analysis["recommended_action"],
                "; ".join(f"{item['id']}: {item['source']}" for item in analysis["sources"]),
                analysis["generated_at"],
            )])
    ai_sheet.freeze_panes = "A2"
    for column in "ABCDEFGH":
        ai_sheet.column_dimensions[column].width = 26 if column not in "EFG" else 60

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
