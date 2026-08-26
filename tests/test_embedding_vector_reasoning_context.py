import math

import pytest

from services.embedding_service import EmbeddingService
from services.vector_service import VectorService, VectorCollectionConflictError
from services.reasoning_context_service import ReasoningContextService


def fake_embed(texts):
    # EmbeddingService can call either one string or a batch depending on config.
    if isinstance(texts, str):
        texts = [texts]

    output = []
    for text in texts:
        if "grid" in text.casefold():
            vector = [1.0, 0.0, 0.0]
        elif "power" in text.casefold():
            vector = [0.8, 0.6, 0.0]
        else:
            vector = [0.0, 0.0, 1.0]
        output.append(vector)
    return output


def test_embedding_service_and_vector_service_roundtrip(tmp_path):
    embedding = EmbeddingService(
        embed_callable=fake_embed,
        model_name="fake-embed",
        normalize=True,
        expected_dimension=3,
        prompt_version="embedding-v1",
    )

    records = embedding.embed_records(
        [
            {"record_id": "FACT-1", "text": "grid constraint"},
            {"record_id": "FACT-2", "text": "power contract"},
            {"record_id": "FACT-3", "text": "software release"},
        ],
        id_field="record_id",
        text_field="text",
    )

    vectors = VectorService(
        db_path=str(tmp_path / "vectors.sqlite3")
    )

    write = vectors.upsert_embedding_records(
        collection_name="evidence",
        records=records,
    )

    assert write["input_record_count"] == 3

    result = vectors.search_text(
        collection_name="evidence",
        query_text="grid access",
        embedding_service=embedding,
        top_k=2,
    )

    assert result[0]["record_id"] == "FACT-1"
    assert result[0]["candidate_retrieval_only"] is True
    assert result[0]["is_grounding_evidence"] is False

    vectors.close()


def test_vector_collection_rejects_mixed_embedding_space(tmp_path):
    vectors = VectorService(
        db_path=str(tmp_path / "vectors.sqlite3")
    )

    vectors.create_collection(
        collection_name="events",
        embedding_model="model-a",
        embedding_dimension=2,
        normalized=True,
        prompt_version="embedding-v1",
    )

    with pytest.raises(VectorCollectionConflictError):
        vectors.upsert_embedding_records(
            collection_name="events",
            records=[
                {
                    "record_id": "E1",
                    "text": "x",
                    "embedding": [1.0, 0.0],
                    "dimension": 2,
                    "normalized": True,
                    "input_fingerprint": "fp",
                    "model_name": "model-b",
                    "prompt_version": "embedding-v1",
                }
            ],
        )

    vectors.close()


def test_reasoning_context_preserves_all_accepted_evidence():
    service = ReasoningContextService(
        default_evidence_batch_max_characters=1000,
        default_structured_batch_max_characters=1000,
        default_preference_batch_max_characters=1000,
    )

    accepted = [
        {
            "evidence_id": f"FACT-{i}",
            "claim": f"Evidence claim {i}",
            "source_id": f"SOURCE-{i}",
        }
        for i in range(6)
    ]

    context = service.build_context(
        research_question="What changed?",
        accepted_evidence=accepted,
        current_events=[],
        historical_events=[],
        constraints={},
        graph_context={},
        prior_reports=[],
        blueprint={
            "reasoning_context_policy": {
                "evidence_batch_max_characters": 1000,
                "structured_batch_max_characters": 1000,
                "preference_batch_max_characters": 1000,
                "batching_strategy": "complete_records_no_silent_truncation",
                "oversized_single_record_policy": "emit_one_record_batch_untruncated",
            }
        },
        user_preferences={},
        run_id="RUN-1",
    )

    evidence_ids = {
        item["evidence_id"]
        for batch in service.iter_batches(
            context,
            collection="accepted_evidence",
        )
        for item in batch["records"]
    }

    assert evidence_ids == {f"FACT-{i}" for i in range(6)}