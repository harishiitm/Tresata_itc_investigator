"""Rebuild the four-page submission brief (requires python-docx and Pillow)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


OUT = Path(__file__).resolve().parent
DOCX = OUT / "ITC_Discrepancy_Investigation_Report.docx"
DIAGRAM = OUT / "architecture.png"
NAVY = "19384B"
BLUE = "24617B"
TEAL = "267A6F"
INK = "243746"
MUTED = "5F6E78"
PALE = "EAF3F5"
PALE2 = "F4F7F8"
ORANGE = "B06C27"


def rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color)


def shade(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    tc_pr.append(shd)


def set_cell_text(cell, value: str, *, bold=False, color=INK, size=8.4) -> None:
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.02
    run = p.add_run(value)
    run.bold = bold
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.color.rgb = rgb(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def table(doc, headers: list[str], rows: list[list[str]], widths: list[float] | None = None, size=8.2):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.autofit = False
    for i, heading in enumerate(headers):
        set_cell_text(t.rows[0].cells[i], heading, bold=True, color="FFFFFF", size=size)
        shade(t.rows[0].cells[i], BLUE)
    for index, values in enumerate(rows):
        cells = t.add_row().cells
        for col, value in enumerate(values):
            set_cell_text(cells[col], str(value), size=size)
            if index % 2 == 0:
                shade(cells[col], PALE2)
    if widths:
        for row in t.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = Inches(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return t


def para(doc, text: str = "", *, bold_prefix: str | None = None, style=None, size=None, color=None, after=4):
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.06
    if bold_prefix and text.startswith(bold_prefix):
        a = p.add_run(bold_prefix)
        a.bold = True
        p.add_run(text[len(bold_prefix):])
    else:
        p.add_run(text)
    if size or color:
        for run in p.runs:
            if size:
                run.font.size = Pt(size)
            if color:
                run.font.color.rgb = rgb(color)
    return p


def section(doc, title: str):
    p = doc.add_paragraph(style="Heading 2")
    p.add_run(title)
    p.paragraph_format.space_before = Pt(7)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.keep_with_next = True
    return p


def small_label(doc, text: str):
    p = para(doc, text.upper(), size=7.8, color=TEAL, after=2)
    p.runs[0].bold = True


def add_hyperlink(paragraph, text: str, url: str):
    part = paragraph.part
    rel_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), BLUE)
    props.append(color)
    run.append(props)
    value = OxmlElement("w:t")
    value.text = text
    run.append(value)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def field(paragraph, instruction: str):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    code = OxmlElement("w:instrText")
    code.set(qn("xml:space"), "preserve")
    code.text = instruction
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, code, end])


def make_diagram():
    im = Image.new("RGB", (2100, 690), "white")
    d = ImageDraw.Draw(im)
    bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    regular = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 29)
    small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 25)
    boxes = [
        (45, 100, 380, 420, "1  INPUT", ["Register + GSTR-2B", "Later statements", "Invoices / notes / email"], "#EAF3F5"),
        (465, 100, 800, 420, "2  EXTRACT", ["Normalize fields", "Keep file + row/page", "OCR only if needed"], "#EAF3F5"),
        (885, 100, 1220, 420, "3  RULES", ["Exact GSTIN / key", "Decimal differences", "Duplicates, period"], "#DFEFEA"),
        (1305, 100, 1640, 420, "4  AI (OPT-IN)", ["Case excerpts only", "Propose reason", "Cite source IDs"], "#FFF1E1"),
        (1725, 100, 2055, 420, "5  HUMAN", ["Verify source", "Correct fields", "Choose next step"], "#E8EDF4"),
    ]
    for x1, y1, x2, y2, title, lines, fill in boxes:
        d.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=fill, outline="#B7C8D0", width=3)
        d.text((x1 + 24, y1 + 26), title, font=bold, fill="#19384B")
        for i, line in enumerate(lines):
            d.text((x1 + 24, y1 + 100 + 58 * i), line, font=regular, fill="#243746")
    for x in (390, 810, 1230, 1650):
        d.line((x, 260, x + 60, 260), fill="#24617B", width=8)
        d.polygon((x + 60, 260, x + 38, 246, x + 38, 274), fill="#24617B")
    d.rounded_rectangle((300, 485, 1800, 610), radius=20, fill="#F4F7F8", outline="#B7C8D0", width=3)
    d.text((340, 508), "CASE FILE + REPORT", font=bold, fill="#267A6F")
    d.text((340, 555), "Difference | evidence | uncertainty | review history", font=regular, fill="#243746")
    d.line((1885, 430, 1885, 545), fill="#24617B", width=7)
    d.line((1885, 545, 1815, 545), fill="#24617B", width=7)
    d.polygon((1815, 545, 1838, 530, 1838, 560), fill="#24617B")
    d.text((45, 30), "Exact facts stay in records and code; explanations remain proposals until human review.", font=small, fill="#5F6E78")
    im.save(DIAGRAM, dpi=(300, 300))


def build():
    make_diagram()
    doc = Document()
    page = doc.sections[0]
    page.page_width = Inches(8.27)
    page.page_height = Inches(11.69)
    page.top_margin = Inches(0.63)
    page.bottom_margin = Inches(0.58)
    page.left_margin = Inches(0.63)
    page.right_margin = Inches(0.63)
    page.header_distance = Inches(0.27)
    page.footer_distance = Inches(0.28)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(10)
    normal.font.color.rgb = rgb(INK)
    normal.paragraph_format.space_after = Pt(4)
    for name, size, color in (("Title", 20, NAVY), ("Heading 1", 14, NAVY), ("Heading 2", 11.3, BLUE)):
        style = styles[name]
        style.font.name = "Aptos Display" if name == "Title" else "Aptos"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = rgb(color)

    header = page.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    header.add_run("ITC DISCREPANCY INVESTIGATION  |  INDIA GST").font.size = Pt(7)
    footer = page.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run("Draft for review · synthetic prototype   |   ").font.size = Pt(7)
    field(footer, "PAGE")
    footer.add_run(" / 4").font.size = Pt(7)

    # Page 1: problem.
    title = doc.add_paragraph(style="Title")
    title.add_run("Intelligent ITC discrepancy investigation")
    para(doc, "Written brief  |  3 November 2026  |  Jurisdiction: India", size=8.2, color=MUTED, after=8)
    table(doc, ["Core proposition"], [["Help an accountant explain why a purchase-register invoice differs from GSTR-2B, with traceable evidence and an explicit unresolved state. The product supports a decision; it does not make one."]], [7.0], size=9.5)

    section(doc, "1  The problem")
    para(doc, "Who and task. GST-registered businesses and CA teams reconcile purchase registers against GSTR-2B each tax period. Existing matching can flag a missing or unequal invoice; the time-consuming task is investigating the reason across the original invoice, notes, amendments, supplier communications and later periods. The GST portal describes GSTR-2B as a read-only, supplier-based ITC statement [1].")
    para(doc, "Cost today. Each unresolved case consumes accountant time and can delay a follow-up or increase the risk of an unsupported ITC decision. I have not measured minutes or rupee cost with practitioners, so the first pilot should record median investigation time and escalation rate rather than claim a savings figure.")
    section(doc, "Why now, and what surprised me")
    para(doc, "GSTR-2B is downloadable in Excel/JSON [1], and the GST portal's Invoice Management System (IMS), available from October 2024, lets recipients accept, reject or keep records pending [2]. These systems improve visibility and workflow, but a mismatch still may not reveal its cause. The useful gap is between detection and a defensible explanation. Another design consequence: a purchase order is contextual evidence, not the primary company-side proof of an issued tax invoice.")
    section(doc, "Scope boundary")
    table(doc, ["In scope", "Out of scope"], [["Invoice-level reconciliation; evidence retrieval; cautious explanations; suggested follow-up; human corrections; review history; downloadable reports.", "Filing returns, claiming ITC, changing GST portal or supplier records, deciding legal eligibility, automatically approving adjustments or contacting suppliers."]], [3.5, 3.5], size=9)
    section(doc, "Small example")
    para(doc, "Purchase register: taxable ₹50,000, GST ₹9,000. GSTR-2B: taxable ₹45,000, GST ₹8,100. The exact differences are ₹5,000 and ₹900. A note, amendment or later filing is a hypothesis until a source document and period support it. The case therefore remains open for an accountant even if an AI explanation sounds plausible.")

    doc.add_page_break()

    # Page 2: architecture.
    doc.add_paragraph("2  Solution architecture", style="Heading 1")
    para(doc, "A narrow case file is the unit of work. The system retrieves evidence for one invoice before asking an LLM to reason, so it can show exactly what the model saw.", after=3)
    doc.add_picture(str(DIAGRAM), width=Inches(7.0))
    small_label(doc, "Data in → work performed → data out")
    table(doc, ["Stage", "Design choice and authority"], [
        ["Input / extraction", "Purchase register and current/later GSTR-2B CSV, XLSX or readable PDF tables; invoices, notes, amendments and email as supporting files. Preserve raw file and row/page reference. OCR text is provisional."],
        ["Exact layer", "Python normalizes GSTIN/invoice key, retains original invoice text, compares Decimal amounts, and flags missing entries, duplicates, date/amount/GSTIN and formatting differences. The source record—not an LLM—owns the numbers."],
        ["Retrieval + AI", "Link only likely documents by invoice and supplier. If the user opts in, Nemotron receives a case's structured facts and short excerpts, returns a candidate explanation, uncertainty label, next step and cited source IDs. Invalid JSON or IDs are rejected."],
        ["Human + output", "An accountant checks source files, edits extracted fields when needed, and marks review status. Edits retain before/after values and trigger reconciliation. The report contains exact differences, source pointers, AI suggestions and unresolved cases."],
    ], [1.2, 5.8], size=8.7)
    section(doc, "Why not only rules, or only one LLM call?")
    para(doc, "Rules are sufficient for exact mismatch detection; an LLM is useful only for connecting unstructured notes, correspondence and amendments into a readable, evidence-bound hypothesis. One unrestricted call over every file risks mixing suppliers and inventing causes. The prototype therefore does deterministic matching first, retrieves per case, then makes one optional model call per discrepancy. Multiple autonomous agents would add cost and tracing complexity without solving the core trust problem.")
    section(doc, "Uncertainty, knowledge and weakest point")
    para(doc, "A document mention is not proof of filing or ITC eligibility. 'Supported' refers to the observed record link, not a tax conclusion; ambiguous evidence remains probable or unresolved. The model is not the rulebook: changing GST law must be reviewed against dated official sources by a qualified person before encoding any new rule. The weakest point is extraction/matching: a bad OCR field or same invoice number across suppliers can create a convincing false link. Field review, visible raw values and correction history mitigate this; semantic truth of AI prose is not yet machine-validated. The synthetic test below proves reconciliation, not yet the added value of AI on real document bundles.")

    doc.add_page_break()

    # Page 3: prototype, tests, risks, links.
    doc.add_paragraph("3  Prototype and evidence", style="Heading 1")
    para(doc, "Implemented: a Python Streamlit site and Python backend (ingest → deterministic engine → evidence lookup → optional OpenRouter batch → human review → CSV/XLSX/TXT export). Original uploads and run JSON stay local. A 'Verify / correct' tab marks human-edited values and recalculates cases. The prototype uses synthetic data only; no live OpenRouter response was evaluated without a key.", after=5)
    para(doc, "Method: parse the three supplied synthetic CSVs, run the deterministic engine, then compare each invoice's output with test_cases_and_expected_results.csv. The optional model was not called.", size=8.8, after=3)
    small_label(doc, "Ten synthetic scenarios · October/November 2026")
    rows = [
        ["1", "Exact match", "Matched", "✓"],
        ["2", "Taxable + tax difference", "Value/tax mismatch; cause unresolved", "✓"],
        ["3", "Absent from October 2B", "Missing in 2B; unresolved", "✓"],
        ["4", "Absent from purchase register", "Missing in register", "✓"],
        ["5", "Duplicate purchase row", "Duplicate case", "✓"],
        ["6", "INV/006 vs INV-006", "Formatting difference flagged", "✓"],
        ["7", "Supplier GSTIN differs", "GSTIN mismatch flagged", "✓"],
        ["8", "Tax only differs", "Broad value/tax category; fields exact", "△"],
        ["9", "Found in November 2B", "Missing October + later record cited", "△"],
        ["10", "Missing, no evidence", "Missing; cause unresolved", "✓"],
    ]
    table(doc, ["#", "Expected signal", "Prototype result", "Fit"], rows, [0.35, 2.2, 3.9, 0.55], size=8.1)
    para(doc, "Result: all 10 expected signals appeared (one clean match, nine cases); 8 were given a precise scenario label and 2 a broader category with the key detail still visible. This is a self-created synthetic check, not measured accuracy on real GST records. Root cause was not established for cases without supporting evidence; OCR quality, real statement formats, model reasoning and time savings remain untested.", size=8.4, after=3)
    section(doc, "Risks and next validation")
    table(doc, ["Risk", "Control now / next proof needed"], [
        ["Wrong AI answer", "Show model text separately; validate cited IDs; require human source review. Test claim-level factuality on labeled documents."],
        ["Outdated rules / liability", "No eligibility decision or portal write. A qualified reviewer owns treatment; version any future rule logic against dated official guidance."],
        ["Confidential data", "Local mode by default; synthetic-only opt-in for free endpoint. Prototype local storage lacks encryption/authentication; add both before real client data."],
        ["Matching / OCR", "Flag formatting variants; retain raw fields; allow human correction. Test false matches and scanned documents before deployment."],
    ], [1.35, 5.65], size=8.25)
    para(doc, "Before investing further: interview 3–5 accountants; time their current investigations; test against at least 50 varied, consented/public or synthetic document bundles with an adjudicated ground truth; then evaluate privacy, audit logging and integration with actual GSTR-2B exports.", size=8.3, after=3)
    para(doc, "Submission links: public GitHub repository [INSERT URL]   |   demo [OPTIONAL URL]   |   LLM chat log [ATTACH/INSERT LINK]", size=8.0, color=ORANGE, after=2)
    p = para(doc, "Sources: ", size=7.6, color=MUTED, after=1)
    add_hyperlink(p, "[1] GST portal GSTR-2B FAQ", "https://tutorial.gst.gov.in/userguide/returns/FAQ_gstr2b.htm")
    p.add_run("   ·   ")
    add_hyperlink(p, "[2] GSTN IMS FAQ (2024)", "https://tutorial.gst.gov.in/downloads/news/final_faqs_on_ims_22_09_2024.pdf")
    para(doc, "Project inputs: supplied Problem_statement.pdf and ITC_Discrepancy_Investigation_Proposal.docx; prototype sample-data/*.csv and code. No practitioner interview or measured ROI claimed.", size=7.4, color=MUTED, after=0)

    doc.add_page_break()

    # Page 4: one-page appendix.
    doc.add_paragraph("Appendix  |  What I added beyond AI", style="Heading 1")
    para(doc, "This is an honest account of the collaboration. I supplied the central problem and directed the implementation; the coding assistant helped turn those directions into an executable prototype and this draft. I should verify the final text and the repo before submission.", after=8)
    section(doc, "Where AI helped")
    para(doc, "The assistant helped organize the GST problem and proposal, implement the Python app, draft the architecture, write test code, run the supplied synthetic cases, and write this brief. OpenRouter is integrated but its live outputs were not tested in this evaluation. Three shortened prompts from my work:")
    table(doc, ["My direction", "What the assistant executed"], [
        ["“Under the problem statement, plan an architecture; keep the front end simple; put front end and backend in Python; create a README.”", "Created a Streamlit/Python prototype with install and run instructions."],
        ["“Allow the user to edit the extracted fields manually, and mark it as human edited.”", "Added field correction, before/after history and reconciliation after edits."],
        ["“Keep only Nemotron; analyze all discrepancy cases if the user chooses OpenRouter; put the summary at the end.”", "Changed model routing, batch workflow and report layout."],
    ], [3.45, 3.55], size=8.7)
    section(doc, "What I rejected or corrected")
    para(doc, "During implementation, we rejected treating a fluent LLM explanation as a tax decision. Exact matching and amount arithmetic stayed in code; the model supplies a cited investigation suggestion. INV/006 and INV-006 are flagged as a formatting difference rather than silently merged. The intentional duplicate test row was kept while overlapping older demo files were removed. The typed model ID had an '='; the documented OpenRouter ID uses '-'.")
    section(doc, "What came from me")
    para(doc, "I chose the investigation problem, supplied the problem/proposal documents and a richer ten-scenario synthetic dataset, and pushed for a simple Python site. I specifically requested editable extracted values with a human-edited marker, a single opt-in OpenRouter model, analysis of all discrepancy cases in one action, and the summary after the cases. Those choices shaped the workflow; the assistant implemented them.")
    section(doc, "One part I can defend without AI")
    para(doc, "The trust boundary: compare identifiers and tax amounts deterministically, retain the original file/row or page, and never convert a possible explanation into an ITC eligibility decision. If a field is wrong, a person corrects it, the system records the change and reruns matching. I can explain each resulting case and where human judgment begins.")
    para(doc, "Attribution note: the report does not claim an accountant interview, actual taxpayer data, live model accuracy, or measured time savings. Those are deliberate next steps, not completed evidence.", size=8.3, color=MUTED, after=0)

    doc.save(DOCX)
    print(DOCX)


if __name__ == "__main__":
    build()
