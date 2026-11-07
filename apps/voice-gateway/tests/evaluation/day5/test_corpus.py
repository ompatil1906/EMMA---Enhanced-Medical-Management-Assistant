"""Day 5 evaluation corpus tests.

Runs the full conversation pipeline against the mock LLM provider for each
synthetic case in the evaluation corpus. Reports intent classification
accuracy, field extraction accuracy, emergency guard recall, and false
positive count on the defined synthetic corpus only.

No real credentials, no live provider calls, no real patient data.
"""

from __future__ import annotations

import pytest

from app.integrations.llm.mock_provider import MockLLMProvider
from tests.evaluation.day5 import CASES, run_case


@pytest.mark.asyncio
async def test_evaluation_corpus() -> None:
    """Run all evaluation cases through the mock pipeline."""
    provider = MockLLMProvider()
    results = []

    for case in CASES:
        result = await run_case(case, provider)
        results.append(result)

    # --- Summary metrics -------------------------------------------------
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    emergency_cases = [r for r in results if "emergency" in r["name"].lower()]
    emergency_recall = (
        sum(1 for r in emergency_cases if r["passed"]) / len(emergency_cases)
        if emergency_cases
        else 0.0
    )
    emergency_false_positives = sum(
        1 for r in results if r["emergency_matched"] and "emergency" not in r["name"].lower()
    )

    # --- Print report ----------------------------------------------------
    print("\n\n=== Day 5 Evaluation Corpus Report ===")
    print(f"Total cases: {total}")
    print(f"Passed: {passed}/{total} ({passed / total * 100:.1f}%)")
    print(f"Emergency cases: {len(emergency_cases)}")
    print(f"Emergency recall: {emergency_recall * 100:.1f}%")
    print(f"Emergency false positives: {emergency_false_positives}")
    print()

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        error = f" (error: {r['error']})" if r.get("error") else ""
        print(
            f"  [{status}] {r['name']}: intent={r['intent']}, "
            f"requests={r['request_types']}, emergency={r['emergency_matched']}{error}"
        )
    print()

    # --- Assertions ------------------------------------------------------
    assert passed == total, f"{total - passed} of {total} cases failed"
    assert emergency_false_positives == 0, (
        f"{emergency_false_positives} false positive emergency detections"
    )
