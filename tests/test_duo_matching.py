import numpy as np
import pytest
from starlette.testclient import TestClient
from backend.app import app
from backend.face_engine import face_engine

client = TestClient(app)

def test_multi_person_intersection_and_union():
    """Verify duo matching logic: together (intersection) vs either (union)."""
    # Create 3 synthetic unit vectors for 3 distinct identities
    rng = np.random.RandomState(42)
    v1 = rng.randn(128).astype(np.float32)
    v1 /= np.linalg.norm(v1)

    v2 = rng.randn(128).astype(np.float32)
    v2 /= np.linalg.norm(v2)

    v3 = rng.randn(128).astype(np.float32)
    v3 /= np.linalg.norm(v3)

    # Candidate gallery:
    # Photo 101 has Person 1 & Person 2 (Together photo!)
    # Photo 102 has only Person 1
    # Photo 103 has only Person 2
    # Photo 104 has only Person 3
    candidate_embs = np.vstack([
        v1, # photo 101, face 1
        v2, # photo 101, face 2
        v1, # photo 102, face 1
        v2, # photo 103, face 1
        v3  # photo 104, face 1
    ])
    candidate_photo_ids = [101, 101, 102, 103, 104]

    # Test TOGETHER (Intersection: both Person 1 and Person 2)
    together_matches = face_engine.match_multi_person(
        embeddings_list=[v1, v2],
        candidate_embeddings=candidate_embs,
        candidate_photo_ids=candidate_photo_ids,
        threshold=0.5,
        mode="together"
    )
    together_photo_ids = [p_id for p_id, score in together_matches]
    assert together_photo_ids == [101], f"Expected only photo 101, got {together_photo_ids}"

    # Test EITHER (Union: Person 1 OR Person 2)
    either_matches = face_engine.match_multi_person(
        embeddings_list=[v1, v2],
        candidate_embeddings=candidate_embs,
        candidate_photo_ids=candidate_photo_ids,
        threshold=0.5,
        mode="either"
    )
    either_photo_ids = set([p_id for p_id, score in either_matches])
    assert either_photo_ids == {101, 102, 103}, f"Expected {101, 102, 103}, got {either_photo_ids}"
    assert 104 not in either_photo_ids

def test_detect_faces_endpoint_validation():
    """Verify detect-faces returns appropriate response on blank image."""
    res = client.post("/api/guest/detect-faces", files={"file": ("blank.jpg", b"", "image/jpeg")})
    assert res.status_code == 400
