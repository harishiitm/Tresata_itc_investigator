"""Streamlit interface for the ITC investigation service."""

from __future__ import annotations

import shutil
import os

import streamlit as st
from dotenv import load_dotenv

from backend.ai import MODEL_ID
from backend.engine import reconcile
from backend.ingest import read_evidence, read_register
from backend.report import csv_report, summary_text, xlsx_report
from backend.service import analyze_run
from backend.store import ROOT, RECORD_GROUPS, create_run, list_runs, load_result, save_upload, set_analysis_mode, update_record, update_review, write_result


def render_correction_editor(result: dict, run_id: str) -> None:
    st.subheader("Verify / correct extracted fields")
    st.caption("Compare each field with the original file. Saving a changed value records its previous value and marks the record as human edited. Findings are recalculated.")
    if not all(group in result for group in RECORD_GROUPS.values()):
        st.info("This run was created before field editing was added. Upload its source files again to enable corrections.")
        return
    source = st.selectbox("Source", list(RECORD_GROUPS), format_func=lambda value: {"purchase": "Purchase register", "gstr": "Current GSTR-2B", "later": "Later GSTR-2B"}[value])
    records = result[RECORD_GROUPS[source]]
    if not records:
        st.info("No records in this source.")
        return
    st.dataframe([
        {"Invoice": row["invoice_number"], "GSTIN": row["supplier_gstin"], "Taxable": row["taxable_value"], "Source row": row["source"], "Provenance": "Human edited" if row.get("human_edits") else "Extracted"}
        for row in records
    ], width="stretch", hide_index=True)
    by_id = {row["record_id"]: row for row in records}
    selected_id = st.selectbox("Record to correct", list(by_id), format_func=lambda item: f"{by_id[item]['invoice_number']} · {by_id[item]['source']} · {'Human edited' if by_id[item].get('human_edits') else 'Extracted'}")
    record = by_id[selected_id]
    with st.form(f"correction_{run_id}_{selected_id}"):
        left, right = st.columns(2)
        with left:
            gstin = st.text_input("Supplier GSTIN", value=record["supplier_gstin"])
            supplier = st.text_input("Supplier name", value=record["supplier_name"])
            invoice = st.text_input("Invoice number", value=record["invoice_number"])
            date = st.text_input("Invoice date", value=record["invoice_date"])
        with right:
            taxable = st.text_input("Taxable value", value=record["taxable_value"])
            igst = st.text_input("IGST", value=record["igst"])
            cgst = st.text_input("CGST", value=record["cgst"])
            sgst = st.text_input("SGST", value=record["sgst"])
        if st.form_submit_button("Save corrected fields", type="primary"):
            try:
                changed = update_record(run_id, source, selected_id, {
                    "supplier_gstin": gstin, "supplier_name": supplier, "invoice_number": invoice,
                    "invoice_date": date, "taxable_value": taxable, "igst": igst, "cgst": cgst, "sgst": sgst,
                })
                if changed:
                    st.rerun()
                else:
                    st.info("No fields changed.")
            except ValueError as exc:
                st.error(str(exc))
    if record.get("human_edits"):
        st.write("**Human correction history**")
        for edit in record["human_edits"]:
            for field, values in edit["changes"].items():
                st.caption(f"{edit['at']} UTC · {field}: {values['before']} → {values['after']}")


def analyze_all_in_ui(run_id: str, api_key: str) -> bool:
    progress = st.progress(0, text="Preparing OpenRouter analysis...")

    def show_progress(index: int, total: int, invoice: str) -> None:
        progress.progress((index - 1) / total, text=f"Analyzing {index} of {total}: {invoice}")

    try:
        status = analyze_run(run_id, api_key, on_progress=show_progress)
    except (ValueError, OSError) as exc:
        st.error(f"OpenRouter batch could not complete: {exc}")
        return False
    finally:
        progress.empty()
    if status["requested"] == 0:
        st.info("All current discrepancy cases already have OpenRouter feedback.")
    else:
        st.success(f"OpenRouter feedback saved for {status['completed']} of {status['requested']} cases.")
        if status["failed"]:
            st.warning(f"{status['failed']} cases failed. Their errors are shown with the cases; retry the batch to analyze cases still missing feedback.")
        if status.get("remaining"):
            st.warning(f"{status['remaining']} cases were left pending after a rate limit response.")
    return True


load_dotenv()
st.set_page_config(page_title="ITC Discrepancy Investigator", page_icon="📋", layout="wide")
st.title("ITC Discrepancy Investigator")
st.caption("Investigate purchase register and GSTR-2B differences with source references. Accountant review is required.")

with st.sidebar:
    st.header("Investigation runs")
    runs = list_runs()
    options = {f"{result['created_at'][:16].replace('T', ' ')} UTC · {run_id[:8]} · {result['case_count']} cases": run_id for run_id, result in runs}
    selected = st.selectbox("Open saved run", ["New investigation", *options], index=0)
    st.caption("Files and reports are stored locally in data/runs/.")

if selected == "New investigation":
    st.subheader("1. Upload records")
    left, right = st.columns(2)
    with left:
        purchase_file = st.file_uploader("Purchase register *", type=["csv", "xlsx", "xlsm", "pdf"], key="purchase")
        current_file = st.file_uploader("GSTR-2B for the same period *", type=["csv", "xlsx", "xlsm", "pdf"], key="current")
    with right:
        later_file = st.file_uploader("Later-period GSTR-2B (optional)", type=["csv", "xlsx", "xlsm", "pdf"], key="later")
        evidence_files = st.file_uploader("Supporting invoices, notes, amendments, or emails (optional)", type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "csv", "xlsx", "xlsm"], accept_multiple_files=True, key="evidence")
    evidence_types = {}
    for index, uploaded in enumerate(evidence_files or []):
        evidence_types[index] = st.selectbox(f"Document type · {uploaded.name}", ["invoice", "credit note", "debit note", "amendment", "email", "other"], key=f"kind_{index}_{uploaded.name}")
    st.info("CSV/XLSX or readable PDF tables work for the two registers. Scanned PDFs and images are supported as evidence when Tesseract OCR is installed.")
    new_mode = st.radio("Investigation mode", ["Local only", "OpenRouter"], horizontal=True, key="new_mode")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    consent_new = False
    if new_mode == "OpenRouter":
        st.caption(f"After reconciliation, all discrepancy cases will be sent one by one to {MODEL_ID}. Only case fields and short source excerpts are sent.")
        st.caption("NVIDIA's free endpoint asks users not to upload confidential or personal data.")
        consent_new = st.checkbox("I confirm these case details are synthetic or non-confidential and may be sent to the free endpoint", key="new_consent")
        if not api_key:
            st.info("Add OPENROUTER_API_KEY to a local .env file and restart the app to use OpenRouter.")
    can_start = bool(purchase_file and current_file) and (new_mode == "Local only" or bool(api_key and consent_new))
    if st.button("Investigate discrepancies", type="primary", disabled=not can_start):
        st.session_state.pop("active_run", None)
        run_id, folder = create_run()
        try:
            purchase_path = save_upload(folder, purchase_file, 0)
            current_path = save_upload(folder, current_file, 1)
            purchase = read_register(purchase_path)
            current = read_register(current_path)
            later = read_register(save_upload(folder, later_file, 2)) if later_file else []
            documents = [read_evidence(save_upload(folder, uploaded, index + 3), evidence_types[index]) for index, uploaded in enumerate(evidence_files or [])]
            result = reconcile(purchase, current, later, documents)
            result["purchase_records"] = purchase
            result["gstr_records"] = current
            result["later_records"] = later
            result["documents"] = documents
            result["analysis_mode"] = new_mode
            result["files"] = [path.name for path in folder.iterdir() if path.is_file()]
            write_result(folder, result)
            st.session_state["active_run"] = run_id
            st.success(f"Investigation saved: {result['case_count']} cases found.")
            if new_mode == "OpenRouter" and result["cases"]:
                analyze_all_in_ui(run_id, api_key)
        except (ValueError, OSError, UnicodeError) as exc:
            if not (folder / "result.json").exists():
                shutil.rmtree(folder, ignore_errors=True)
            st.error(str(exc))
            st.caption("Correct the file and run the investigation again.")
    run_id = st.session_state.get("active_run")
else:
    run_id = options[selected]

if run_id:
    try:
        result = load_result(run_id)
    except (OSError, ValueError) as exc:
        st.error(f"Could not open run: {exc}")
        st.stop()
    findings_tab, correct_tab = st.tabs(["Findings and reports", "Verify / correct"])
    with findings_tab:
        st.divider()
        st.subheader("2. Discrepancy cases")
        metrics = st.columns(4)
        metrics[0].metric("Purchase invoices", result["purchase_count"])
        metrics[1].metric("GSTR-2B invoices", result["gstr_count"])
        metrics[2].metric("Matched", result["matched"])
        metrics[3].metric("Cases to investigate", result["case_count"])
        if result["categories"]:
            st.write(" · ".join(f"{category}: **{count}**" for category, count in sorted(result["categories"].items())))
        mode_default = result.get("analysis_mode", "Local only")
        mode = st.radio("Investigation mode for this run", ["Local only", "OpenRouter"], index=0 if mode_default == "Local only" else 1, horizontal=True, key=f"mode_{run_id}")
        if mode != mode_default:
            set_analysis_mode(run_id, mode)
            result["analysis_mode"] = mode
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if mode == "OpenRouter" and result["cases"]:
            pending = sum((case.get("ai_analysis") or {}).get("model") != MODEL_ID for case in result["cases"])
            st.caption(f"{pending} of {result['case_count']} cases still need feedback from {MODEL_ID}. One action processes every pending case.")
            st.caption("NVIDIA's free endpoint asks users not to upload confidential or personal data.")
            consent = st.checkbox("I confirm these case details are synthetic or non-confidential and may be sent to the free endpoint", key=f"consent_{run_id}")
            if not api_key:
                st.info("Add OPENROUTER_API_KEY to a local .env file and restart the app. Do not post your key in chat.")
            if st.button(f"Analyze all {pending} pending cases with OpenRouter", disabled=not (pending and api_key and consent), key=f"batch_{run_id}"):
                if analyze_all_in_ui(run_id, api_key):
                    st.rerun()

        if not result["cases"]:
            st.success("No discrepancies found in the supplied records.")
        for case in result["cases"]:
            edited = any((case.get(side) or {}).get("human_edits") for side in ("purchase_record", "gstr_record"))
            heading = f"{case['invoice_number']} · {case['category']} · {case['review_status']}" + (" · Human edited" if edited else "")
            with st.expander(heading):
                st.write(f"**Supplier:** {case['supplier_name'] or 'Unknown'} · **GSTIN:** {case['supplier_gstin']} · **Invoice date:** {case['invoice_date'] or 'Not supplied'}")
                for side, label in (("purchase_record", "Purchase register"), ("gstr_record", "GSTR-2B")):
                    record = case.get(side)
                    if record:
                        provenance = "Human edited" if record.get("human_edits") else "Extracted"
                        st.write(f"**{label} ({provenance}):** taxable ₹{record['taxable_value']} · IGST ₹{record['igst']} · CGST ₹{record['cgst']} · SGST ₹{record['sgst']} · {record['source']}")
                st.write("**Differences:** " + "; ".join(case["differences"]))
                st.write("**Finding:** " + case["finding"])
                st.write("**Evidence strength:** " + case["confidence"])
                st.write("**Suggested next step:** " + case["recommended_action"])
                if case["evidence"]:
                    st.write("**Source references**")
                    for item in case["evidence"]:
                        st.caption(f"{item['file']} · page {item['page']} · {item['kind']}: {item['excerpt']}")
                else:
                    st.caption("No supporting document matched this invoice number.")
                ai = case.get("ai_analysis")
                if ai and ai.get("model") == MODEL_ID:
                    st.write(f"**OpenRouter feedback ({ai['assessment']}):** {ai['explanation']}")
                    st.write("**AI suggested action:** " + ai["recommended_action"])
                    st.caption("AI-cited sources: " + "; ".join(f"{item['id']} {item['source']}" for item in ai["sources"]))
                elif case.get("ai_error"):
                    st.warning("OpenRouter feedback unavailable: " + case["ai_error"])
                elif mode == "OpenRouter":
                    st.caption("OpenRouter feedback pending for this case.")
                with st.form(f"review_{case['id']}"):
                    statuses = ["Needs review", "Reviewed", "Needs follow-up"]
                    status = st.selectbox("Accountant review status", statuses, index=statuses.index(case["review_status"]))
                    note = st.text_area("Review note", value=case["review_note"], max_chars=2000)
                    if st.form_submit_button("Save review"):
                        update_review(run_id, case["id"], status, note)
                        st.rerun()

        with st.expander("Original uploaded files"):
            for filename in result.get("files", []):
                path = ROOT / run_id / filename
                if path.is_file():
                    st.download_button(filename, data=path.read_bytes(), file_name=filename, key=f"file_{run_id}_{filename}")

        st.divider()
        st.subheader("3. Summary and downloads")
        st.text(summary_text(result))
        report_buttons = st.columns(3)
        with report_buttons[0]:
            st.download_button("Download summary", summary_text(result), file_name=f"itc-summary-{run_id[:8]}.txt", mime="text/plain")
        with report_buttons[1]:
            st.download_button("Download CSV report", csv_report(result), file_name=f"itc-investigation-{run_id[:8]}.csv", mime="text/csv")
        with report_buttons[2]:
            st.download_button("Download Excel report", xlsx_report(result), file_name=f"itc-investigation-{run_id[:8]}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    with correct_tab:
        render_correction_editor(result, run_id)
