"""Day 6 evaluation corpus tests (RAG + knowledge base).

Runs the full conversation pipeline against the mock LLM provider and mock
providers for each synthetic case in the evaluation corpus. Reports retrieval
recall, citation validity, grounded answer accuracy, abstention quality,
tenant isolation, and emergency bypass on the defined synthetic corpus only.

No real credentials, no live provider calls, no real patient data.
"""

from __future__ import annotations

import pytest

from app.integrations.embeddings.mock_provider import MockEmbeddingProvider
from app.integrations.llm.mock_provider import MockLLMProvider
from app.integrations.vector_store.mock_store import MockVectorStoreProvider
from app.services.retrieval import KnowledgeAnswerService, Retriever
from tests.evaluation.day6 import CASES, run_case


@pytest.mark.asyncio
async def test_evaluation_corpus() -> None:
    """Run all evaluation cases through the mock pipeline."""
    embedding_provider = MockEmbeddingProvider()
    vector_store = MockVectorStoreProvider()
    retriever = Retriever(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        default_top_k=5,
        default_min_score=0.3,
    )
    knowledge_answer_service = KnowledgeAnswerService(
        llm_provider=MockLLMProvider(),
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        prompt_version="v1",
    )

    results = []
    for case in CASES:
        result = await run_case(case, retriever, knowledge_answer_service)
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
    retrieval_recall = (
        sum(1 for r in results if r.get("retrieval_recall", False)) / total if total else 0.0
    )
    citation_validity = (
        sum(1 for r in results if r.get("citation_validity", False)) / total if total else 0.0
    )
    grounded_answer_accuracy = (
        sum(1 for r in results if r.get("grounded_answer_accuracy", False)) / total
        if total
        else 0.0
    )
    tenant_isolation_violations = sum(
        1 for r in results if r.get("tenant_isolation_violation", False)
    )
    stale_document_leakage = sum(1 for r in results if r.get("stale_document_leakage", False))
    emergency_bypass = sum(1 for r in results if r.get("emergency_bypass", False))

    # --- Print report ----------------------------------------------------
    print("\n\n=== Day 6 Evaluation Corpus Report ===")
    print(f"Total cases: {total}")
    print(f"Passed: {passed}/{total} ({passed / total * 100:.1f}%)")
    print(f"Emergency cases: {len(emergency_cases)}")
    print(f"Emergency recall: {emergency_recall * 100:.1f}%")
    print(f"Emergency false positives: {emergency_false_positives}")
    print(f"Retrieval recall: {retrieval_recall * 100:.1f}%")
    print(f"Citation validity: {citation_validity * 100:.1f}%")
    print(f"Grounded answer accuracy: {grounded_answer_accuracy * 100:.1f}%")
    print(f"Tenant isolation violations: {tenant_isolation_violations}")
    print(f"Stale document leakage: {stale_document_leakage}")
    print(f"Emergency bypass: {emergency_bypass}")
    print()

    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        error = f" (error: {r['error']})" if r.get("error") else ""
        print(
            f"  [{status}] {r['name']}: intent={r['intent']}, "
            f"retrieval={r.get('retrieval_recall', False)}, "
            f"citations={r.get('citation_validity', False)}, "
            f"emergency={r['emergency_matched']}{error}"
        )
    print()

    # --- Assertions ------------------------------------------------------
    assert passed == total, f"{total - passed} of {total} cases failed"
    assert emergency_false_positives == 0, (
        f"{emergency_false_positives} false positive emergency detections"
    )
    assert tenant_isolation_violations == 0, (
        f"{tenant_isolation_violations} tenant isolation violations"
    )
    assert stale_document_leakage == 0, f"{stale_document_leakage} stale document leakage cases"
    assert emergency_bypass == 0, (
        f"{emergency_bypass} emergency bypass cases (LLM called on emergency)"
    )
