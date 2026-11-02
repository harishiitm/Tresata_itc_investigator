SYNTHETIC GST RECONCILIATION DATASET

All records are fabricated for prototype testing. They do not represent real taxpayers, suppliers, GSTINs, invoices, filings or tax advice.

Files:
- purchase_register_oct_2026.csv: Company's illustrative October Purchase Register.
- gstr2b_oct_2026.csv: Illustrative October GSTR-2B extract.
- gstr2b_nov_2026_optional_cross_period.csv: Optional subsequent-period row for test 9.
- test_cases_and_expected_results.csv: Ten test scenarios and expected outcomes.
- synthetic_gst_reconciliation_dataset.xlsx: Same data in workbook sheets.

Amounts are INR. This is a simplified schema, not the complete official GSTR-2B format. Keep original invoice numbers; create a separate normalized key for matching. Do not silently merge punctuation variants. Test 2 and test 8 have unexplained differences: flag them, do not invent a cause. Test 10 intentionally lacks enough evidence and must remain unresolved. Test 9 requires the optional November file.
