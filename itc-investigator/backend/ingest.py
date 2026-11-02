"""Extract invoice rows and searchable evidence from uploaded files."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from openpyxl import load_workbook


ALIASES = {
    "gstin": {"gstin", "supplier gstin", "supplier gst no", "gst no", "gstin of supplier", "gstin of supplier recipient", "gstin uin of supplier"},
    "supplier": {"supplier", "supplier name", "trade name", "legal name", "name of supplier"},
    "invoice": {"invoice", "invoice no", "invoice number", "invoice no.", "document no", "document number", "invoice details invoice number", "invoice details invoice no"},
    "date": {"invoice date", "document date", "date of invoice", "invoice details invoice date"},
    "taxable": {"taxable value", "taxable amount", "taxable value rs", "taxable value inr"},
    "igst": {"igst", "igst inr", "integrated tax", "integrated tax amount", "igst amount"},
    "cgst": {"cgst", "cgst inr", "central tax", "central tax amount", "cgst amount"},
    "sgst": {"sgst", "sgst inr", "state ut tax", "state tax", "state ut tax amount", "sgst amount"},
}


def clean_header(value: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def column_map(row: list[object]) -> dict[str, int]:
    found = {}
    for index, value in enumerate(row):
        label = clean_header(value)
        for field, aliases in ALIASES.items():
            if label in {clean_header(alias) for alias in aliases} and field not in found:
                found[field] = index
    return found


def amount(value: object) -> str:
    if value is None or str(value).strip() in {"", "-", "—"}:
        return "0.00"
    text = str(value).replace(",", "").replace("₹", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return str(Decimal(text).quantize(Decimal("0.01")))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {value!r}") from exc


def normalize_invoice(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_date(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return text


def records_from_rows(rows: list[list[object]], filename: str, location: str) -> list[dict]:
    header_index = next((i for i, row in enumerate(rows[:30]) if {"gstin", "invoice", "taxable"} <= column_map(row).keys()), None)
    if header_index is None:
        raise ValueError(f"{filename}: could not find GSTIN, invoice number, and taxable value columns. Use the sample CSV headers in the README.")
    columns = column_map(rows[header_index])
    result = []
    for row_number, row in enumerate(rows[header_index + 1 :], header_index + 2):
        def get(field: str) -> object:
            index = columns.get(field)
            return row[index] if index is not None and index < len(row) else ""

        invoice = str(get("invoice") or "").strip()
        gstin = re.sub(r"\s+", "", str(get("gstin") or "")).upper()
        if not invoice and not gstin:
            continue
        if not invoice or not gstin:
            raise ValueError(f"{filename}, {location} row {row_number}: GSTIN and invoice number are required.")
        if get("taxable") is None or str(get("taxable")).strip() == "":
            raise ValueError(f"{filename}, {location} row {row_number}: taxable value is required.")
        result.append({
            "record_id": str(uuid4()),
            "human_edits": [],
            "supplier_gstin": gstin,
            "supplier_name": str(get("supplier") or "").strip(),
            "invoice_number": invoice,
            "invoice_key": normalize_invoice(invoice),
            "invoice_date": normalize_date(get("date")),
            "taxable_value": amount(get("taxable")),
            "igst": amount(get("igst")),
            "cgst": amount(get("cgst")),
            "sgst": amount(get("sgst")),
            "source": f"{filename} ({location}, row {row_number})",
        })
    if not result:
        raise ValueError(f"{filename}: no invoice rows found.")
    return result


def read_register(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw = path.read_bytes()
        content = raw.decode("utf-8-sig") if not raw.startswith(b"\xff\xfe") else raw.decode("utf-16")
        return records_from_rows(list(csv.reader(io.StringIO(content))), path.name, "CSV")
    if suffix in {".xlsx", ".xlsm"}:
        workbook = load_workbook(path, read_only=True, data_only=True)
        records = []
        for sheet in workbook:
            rows = [list(row) for row in sheet.values]
            if rows and any({"gstin", "invoice", "taxable"} <= column_map(row).keys() for row in rows[:30]):
                records.extend(records_from_rows(rows, path.name, f"sheet {sheet.title}"))
        workbook.close()
        if not records:
            raise ValueError(f"{path.name}: no sheet has GSTIN, invoice number, and taxable value columns.")
        return records
    if suffix == ".pdf":
        import pdfplumber

        records = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                for table in page.extract_tables():
                    rows = [list(row) for row in table if row]
                    if rows and any({"gstin", "invoice", "taxable"} <= column_map(row).keys() for row in rows[:30]):
                        records.extend(records_from_rows(rows, path.name, f"page {page_number}"))
        if not records:
            raise ValueError(f"{path.name}: no readable invoice table found. Export this register as CSV or XLSX.")
        return records
    raise ValueError(f"{path.name}: register must be CSV, XLSX, or a text-based table PDF.")


def read_evidence(path: Path, kind: str) -> dict:
    suffix = path.suffix.lower()
    pages = []
    if suffix == ".pdf":
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                content = page.extract_text() or ""
                if not content.strip():
                    try:
                        import pypdfium2 as pdfium
                        import pytesseract

                        document = pdfium.PdfDocument(str(path))
                        content = pytesseract.image_to_string(document[number - 1].render(scale=2).to_pil_image())
                    except Exception as exc:
                        raise ValueError(f"{path.name} page {number}: OCR could not run. Install Tesseract or provide a text-based PDF.") from exc
                pages.append({"page": number, "text": content[:30000]})
    elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        from PIL import Image
        import pytesseract

        try:
            content = pytesseract.image_to_string(Image.open(path))
        except pytesseract.TesseractNotFoundError as exc:
            raise ValueError("Tesseract OCR is required for image evidence. See README installation steps.") from exc
        pages.append({"page": 1, "text": content[:30000]})
    elif suffix in {".csv", ".xlsx", ".xlsm"}:
        try:
            rows = read_register(path)
            pages.append({"page": 1, "text": "\n".join(f"{r['supplier_gstin']} {r['invoice_number']} taxable {r['taxable_value']} IGST {r['igst']} CGST {r['cgst']} SGST {r['sgst']}" for r in rows)})
        except ValueError:
            if suffix != ".csv":
                raise
            pages.append({"page": 1, "text": path.read_text(encoding="utf-8-sig")[:30000]})
    else:
        raise ValueError(f"Unsupported evidence format: {path.name}")
    if not any(page["text"].strip() for page in pages):
        raise ValueError(f"{path.name}: no readable text found in supporting document.")
    return {"filename": path.name, "kind": kind, "pages": pages}
