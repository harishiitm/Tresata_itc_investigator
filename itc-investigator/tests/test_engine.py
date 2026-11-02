import unittest

from backend.engine import reconcile


def row(invoice, value="100.00", gstin="29ABCDE1234F1Z5", source="sample.csv (CSV, row 2)"):
    return {
        "supplier_gstin": gstin, "supplier_name": "Example Supplier", "invoice_number": invoice,
        "invoice_key": invoice.replace("-", ""), "invoice_date": "2026-10-05",
        "taxable_value": value, "igst": "18.00", "cgst": "0.00", "sgst": "0.00", "source": source,
    }


class ReconcileTests(unittest.TestCase):
    def test_missing_invoice_has_later_period_evidence(self):
        result = reconcile([row("INV-415")], [], [row("INV-415", source="november.csv (CSV, row 2)")], [])
        self.assertEqual(result["categories"], {"Missing in GSTR-2B": 1})
        self.assertIn("later GSTR-2B", result["cases"][0]["finding"])
        self.assertEqual(result["cases"][0]["review_status"], "Needs review")

    def test_mismatch_and_duplicate_are_separate_cases(self):
        purchase = [row("INV-205", "50000.00"), row("INV-310"), row("INV-310", source="sample.csv (CSV, row 4)")]
        gstr = [row("INV-205", "45000.00"), row("INV-310")]
        result = reconcile(purchase, gstr, [], [])
        self.assertEqual(result["categories"], {"Value or tax mismatch": 1, "Duplicate entry": 1})
        self.assertEqual(result["matched"], 0)
        self.assertIn("₹50000.00", result["cases"][0]["differences"][0])

    def test_identical_rows_match(self):
        result = reconcile([row("INV-500")], [row("INV-500")], [], [])
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["case_count"], 0)

    def test_different_supplier_is_review_case(self):
        result = reconcile([row("INV-100")], [row("INV-100", gstin="27PQRSX9876A1Z2")], [], [])
        self.assertEqual(result["categories"], {"GSTIN mismatch": 1})


if __name__ == "__main__":
    unittest.main()
