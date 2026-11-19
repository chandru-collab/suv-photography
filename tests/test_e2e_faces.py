import io
import uuid
import pytest
import numpy as np
import cv2
from PIL import Image
from starlette.testclient import TestClient

from backend.app import app
from backend.database import (
    insert_photo,
    insert_face,
    get_all_face_embeddings,
    get_photos_by_ids,
    get_photo_by_id,
    get_stats
)
from backend.face_engine import face_engine

client = TestClient(app)

def test_full_pipeline_mock_face_search():
    """
    Test indexing 3 photos (one matching guest A, one matching guest B, one with zero faces).
    Verify that when guest A searches with their selfie embedding, only their photo is returned!
    """
    # Create 3 unique normalized 128-D embeddings
    rng = np.random.RandomState(int(uuid.uuid4().int % 100000))
    run_id = uuid.uuid4().hex[:8]

    emb_guest_a = rng.randn(128).astype(np.float32)
    emb_guest_a /= np.linalg.norm(emb_guest_a)

    emb_guest_b = rng.randn(128).astype(np.float32)
    emb_guest_b /= np.linalg.norm(emb_guest_b)

    # Photo 1: Contains Guest A
    p1_id = insert_photo(
        filename=f"wedding_dance_{run_id}.jpg",
        filepath="photos/test_dance.jpg",
        thumbnail_path="thumbnails/thumb_dance.jpg",
        file_hash=f"test_hash_p1_{run_id}",
        source="upload",
        file_size=204800,
        width=1920,
        height=1080,
        face_count=1
    )
    insert_face(p1_id, (100, 100, 150, 150), 0.95, emb_guest_a)

    # Photo 2: Contains Guest B
    p2_id = insert_photo(
        filename=f"dinner_toast_{run_id}.jpg",
        filepath="photos/test_toast.jpg",
        thumbnail_path="thumbnails/thumb_toast.jpg",
        file_hash=f"test_hash_p2_{run_id}",
        source="upload",
        file_size=184320,
        width=1920,
        height=1080,
        face_count=1
    )
    insert_face(p2_id, (200, 150, 140, 140), 0.92, emb_guest_b)

    # Photo 3: Landscape (zero faces)
    p3_id = insert_photo(
        filename=f"venue_sunset_{run_id}.jpg",
        filepath="photos/test_sunset.jpg",
        thumbnail_path="thumbnails/thumb_sunset.jpg",
        file_hash=f"test_hash_p3_{run_id}",
        source="upload",
        file_size=312000,
        width=1920,
        height=1080,
        face_count=0
    )

    # Check stats
    stats = get_stats()
    assert stats["total_photos"] >= 3
    assert stats["zero_face_photos"] >= 1

    # Load all face embeddings
    face_ids, photo_ids, embeddings_matrix = get_all_face_embeddings()
    assert len(photo_ids) >= 2

    # Query with Guest A (add slight noise simulating real-world selfie variation)
    noisy_query_a = emb_guest_a + 0.05 * rng.randn(128).astype(np.float32)
    noisy_query_a /= np.linalg.norm(noisy_query_a)

    matches = face_engine.match_query_vector(noisy_query_a, embeddings_matrix, threshold=0.363)
    matched_photo_ids = [photo_ids[idx] for idx, _ in matches]

    # Guest A's photo should match, Guest B's photo should NOT match!
    assert p1_id in matched_photo_ids, "Guest A's photo must be matched"
    assert p2_id not in matched_photo_ids, "Guest B's photo must NOT be matched"
    assert p3_id not in matched_photo_ids, "Zero-face photo must NOT be matched"

def test_zip_generation_for_matches(tmp_path):
    """Test generating a zip file containing matched photos."""
    # Create dummy photo on disk in data/photos
    from backend.config import PHOTOS_DIR
    run_id = uuid.uuid4().hex[:8]
    dummy_file = PHOTOS_DIR / f"dummy_test_photo_{run_id}.jpg"
    with open(dummy_file, "wb") as f:
        f.write(b"\xFF\xD8\xFF\xE0\x00\x10JFIF" + b"\x00" * 100) # minimal JPEG header

    p_id = insert_photo(
        filename=f"dummy_test_photo_{run_id}.jpg",
        filepath=f"photos/dummy_test_photo_{run_id}.jpg",
        thumbnail_path="",
        file_hash=f"dummy_hash_for_zip_{run_id}",
        source="test",
        file_size=108,
        width=100,
        height=100,
        face_count=1
    )

    res = client.post("/api/guest/download-zip", json={"photo_ids": [p_id]})
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert len(res.content) > 0

    # Clean up test file
    if dummy_file.exists():
        dummy_file.unlink()
