import pytest

from services.vector_service import (
    VectorService,
    VectorValidationError,
)


def test_vector_similarity_is_candidate_retrieval_only(
    tmp_path,
):
    service = VectorService(
        db_path=str(
            tmp_path
            / "vectors.sqlite3"
        )
    )

    service.create_collection(
        collection_name="evidence",
        embedding_model="fake",
        embedding_dimension=3,
        normalized=True,
        distance_metric="cosine",
        prompt_version="embedding-v1",
    )

    service.upsert_vector(
        collection_name="evidence",
        record_id="FACT-1",
        vector=[
            1.0,
            0.0,
            0.0,
        ],
        text="grid queue",
    )

    result = service.search(
        collection_name="evidence",
        query_vector=[
            1.0,
            0.0,
            0.0,
        ],
        top_k=1,
    )[0]

    assert (
        result[
            "candidate_retrieval_only"
        ]
        is True
    )

    assert (
        result[
            "is_grounding_evidence"
        ]
        is False
    )

    assert (
        result[
            "source_independence_assessed_here"
        ]
        is False
    )

    service.close()


def test_normalized_collection_rejects_non_normalized_vector(
    tmp_path,
):
    service = VectorService(
        db_path=str(
            tmp_path
            / "vectors.sqlite3"
        )
    )

    service.create_collection(
        collection_name="events",
        embedding_model="fake",
        embedding_dimension=2,
        normalized=True,
        distance_metric="cosine",
        prompt_version="embedding-v1",
    )

    with pytest.raises(
        VectorValidationError
    ):
        service.upsert_vector(
            collection_name="events",
            record_id="EVENT-1",
            vector=[
                2.0,
                0.0,
            ],
        )

    service.close()