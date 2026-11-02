import json
import shutil
import unittest
from pathlib import Path

from backend.ai import MODEL_ID, _parse_json, analyze_case, validate_analysis
from backend.engine import reconcile
from backend.ingest import read_register
from backend.report import summary_text, xlsx_report
from backend.service import analyze_run
from backend.store import create_run, load_result, save_ai_analysis, set_analysis_mode, update_record, write_result


SAMPLES = Path(__file__).resolve().parents[1] / "sample-data"


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.run_id, self.folder = create_run()
        purchase = read_register(SAMPLES / "purchase_register_oct_2026.csv")
        current = read_register(SAMPLES / "gstr2b_oct_2026.csv")
        later = read_register(SAMPLES / "gstr2b_nov_2026_optional_cross_period.csv")
        result = reconcile(purchase, current, later, [])
        result.update(purchase_records=purchase, gstr_records=current, later_records=later, documents=[], files=[])
        write_result(self.folder, result)

    def tearDown(self):
        shutil.rmtree(self.folder)

    def test_correction_recalculates_and_marks_human_edited(self):
        before = load_result(self.run_id)
        record = next(item for item in before["purchase_records"] if item["invoice_number"] == "INV-002")
        self.assertTrue(update_record(self.run_id, "purchase", record["record_id"], {
            "taxable_value": "18000", "cgst": "1620", "sgst": "1620",
        }))
        after = load_result(self.run_id)
        self.assertFalse(any(case["invoice_number"] == "INV-002" for case in after["cases"]))
        self.assertEqual(after["matched"], before["matched"] + 1)
        edited = next(item for item in after["purchase_records"] if item["record_id"] == record["record_id"])
        self.assertEqual(edited["human_edits"][0]["changes"]["taxable_value"]["before"], "20000.00")
        self.assertIn("Human-edited records: 1", summary_text(after))
        self.assertGreater(len(xlsx_report(after)), 1000)

    def test_invalid_value_does_not_save(self):
        before = load_result(self.run_id)
        record = before["purchase_records"][0]
        with self.assertRaises(ValueError):
            update_record(self.run_id, "purchase", record["record_id"], {"taxable_value": "wrong"})
        self.assertEqual(load_result(self.run_id)["purchase_records"][0]["taxable_value"], record["taxable_value"])

    def test_single_model_feedback_is_saved(self):
        before = load_result(self.run_id)
        case = before["cases"][0]
        base = {"explanation": "Difference remains unexplained.", "assessment": "unresolved", "recommended_action": "Check invoice.", "evidence_refs": ["E1"], "sources": [{"id": "E1", "source": "sample.csv"}], "generated_at": "2026-11-03T00:00:00Z"}
        save_ai_analysis(self.run_id, case["id"], {**base, "model": MODEL_ID})
        saved = load_result(self.run_id)["cases"][0]
        self.assertEqual(saved["ai_analysis"]["model"], MODEL_ID)
        self.assertNotIn("ai_analyses", saved)

    def test_batch_requires_openrouter_choice_and_analyzes_all_cases(self):
        called = []

        def analyzer(case, api_key):
            called.append(case["id"])
            return {"model": MODEL_ID, "explanation": "Review this discrepancy.", "assessment": "unresolved", "recommended_action": "Check the invoice.", "evidence_refs": ["E1"], "sources": [{"id": "E1", "source": "sample.csv"}], "generated_at": "2026-11-03T00:00:00Z"}

        with self.assertRaises(ValueError):
            analyze_run(self.run_id, "test-key", analyzer=analyzer)
        self.assertEqual(called, [])
        set_analysis_mode(self.run_id, "OpenRouter")
        status = analyze_run(self.run_id, "test-key", analyzer=analyzer)
        self.assertEqual(status, {"requested": 9, "completed": 9, "failed": 0})
        self.assertEqual(len(called), 9)
        self.assertTrue(all(case.get("ai_analysis") for case in load_result(self.run_id)["cases"]))
        self.assertEqual(analyze_run(self.run_id, "test-key", analyzer=analyzer)["requested"], 0)

    def test_batch_continues_after_one_case_fails(self):
        set_analysis_mode(self.run_id, "OpenRouter")
        count = 0

        def analyzer(case, api_key):
            nonlocal count
            count += 1
            if count == 1:
                raise ValueError("Temporary model error")
            return {"model": MODEL_ID, "explanation": "Review this discrepancy.", "assessment": "unresolved", "recommended_action": "Check the invoice.", "evidence_refs": ["E1"], "sources": [{"id": "E1", "source": "sample.csv"}], "generated_at": "2026-11-03T00:00:00Z"}

        status = analyze_run(self.run_id, "test-key", analyzer=analyzer)
        self.assertEqual((status["completed"], status["failed"]), (8, 1))
        self.assertEqual(load_result(self.run_id)["cases"][0]["ai_error"], "Temporary model error")
        retry = analyze_run(self.run_id, "test-key", analyzer=analyzer)
        self.assertEqual((retry["requested"], retry["completed"]), (1, 1))


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": json.dumps({"explanation": "The amount differs, but the cause is unknown.", "assessment": "unresolved", "recommended_action": "Check the invoice.", "evidence_refs": ["E1", "E2"]})}}]}).encode()


class AITests(unittest.TestCase):
    def test_only_case_sources_sent_and_response_validated(self):
        purchase = read_register(SAMPLES / "purchase_register_oct_2026.csv")
        current = read_register(SAMPLES / "gstr2b_oct_2026.csv")
        case = reconcile(purchase, current, [], [])["cases"][0]
        captured = {}

        def opener(request, timeout):
            captured.update(json.loads(request.data))
            return FakeResponse()

        result = analyze_case(case, "test-key", opener=opener)
        self.assertEqual(result["evidence_refs"], ["E1", "E2"])
        self.assertEqual(captured["model"], MODEL_ID)
        self.assertEqual(len(captured["messages"]), 2)
        self.assertNotIn("test-key", json.dumps(captured))

    def test_unknown_citation_rejected(self):
        with self.assertRaises(ValueError):
            validate_analysis({"explanation": "Claim", "recommended_action": "Check", "evidence_refs": ["E99"]}, [{"id": "E1"}], "test")

    def test_json_with_model_preamble_is_parsed(self):
        self.assertEqual(_parse_json('Here is the result: {"assessment":"unresolved"}')["assessment"], "unresolved")


if __name__ == "__main__":
    unittest.main()
