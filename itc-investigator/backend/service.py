"""Batch case analysis for an explicitly selected OpenRouter run."""

from __future__ import annotations

from collections.abc import Callable

from backend.ai import MODEL_ID, analyze_case
from backend.store import load_result, save_ai_analysis, save_ai_error


def analyze_run(
    run_id: str,
    api_key: str,
    *,
    analyzer: Callable = analyze_case,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Analyze every case missing feedback; keep successes if a later case fails."""
    if not api_key:
        raise ValueError("Add OPENROUTER_API_KEY to your local .env file first.")
    result = load_result(run_id)
    if result.get("analysis_mode") != "OpenRouter":
        raise ValueError("Choose OpenRouter mode before sending case data.")
    cases = [case for case in result["cases"] if (case.get("ai_analysis") or {}).get("model") != MODEL_ID]
    status = {"requested": len(cases), "completed": 0, "failed": 0}
    for index, case in enumerate(cases, 1):
        if on_progress:
            on_progress(index, len(cases), case["invoice_number"])
        try:
            analysis = analyzer(case, api_key)
            save_ai_analysis(run_id, case["id"], analysis)
            status["completed"] += 1
        except ValueError as exc:
            save_ai_error(run_id, case["id"], str(exc))
            status["failed"] += 1
            if "HTTP 429" in str(exc):
                status["remaining"] = len(cases) - index
                break
    return status
