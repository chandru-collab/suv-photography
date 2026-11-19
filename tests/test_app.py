import pytest
import io
import os
import numpy as np
import cv2
from PIL import Image
from starlette.testclient import TestClient

from backend.config import DEFAULT_MATCH_THRESHOLD, YUNET_MODEL_PATH, SFACE_MODEL_PATH
from backend.app import app
from backend.sync_service import extract_drive_folder_id, compute_file_hash
from backend.qr_service import generate_qr_code, get_guest_url
from backend.face_engine import face_engine
from backend.database import (
    init_db,
    insert_photo,
    insert_face,
    photo_exists_by_hash,
    get_all_face_embeddings,
    get_photos_by_ids,
    get_stats
)

client = TestClient(app)

def test_models_exist_and_load():
    """Verify the 2 ONNX models exist and load in OpenCV."""
    assert YUNET_MODEL_PATH.exists(), "YuNet model file missing"
    assert SFACE_MODEL_PATH.exists(), "SFace model file missing"
    assert SFACE_MODEL_PATH.stat().st_size > 30 * 1024 * 1024, "SFace model should be ~38.7 MB"

    assert face_engine.detector is not None
    assert face_engine.recognizer is not None

def test_drive_url_extraction():
    """Test extracting Google Drive folder IDs from various link structures."""
    urls = [
        ("https://drive.google.com/drive/folders/1ABC_def-xyz1234567890", "1ABC_def-xyz1234567890"),
        ("https://drive.google.com/drive/u/0/folders/1ABC_def-xyz1234567890?usp=sharing", "1ABC_def-xyz1234567890"),
        ("https://drive.google.com/open?id=1ABC_def-xyz1234567890", "1ABC_def-xyz1234567890"),
        ("1ABC_def-xyz1234567890ABCDEF", "1ABC_def-xyz1234567890ABCDEF"),
    ]
    for url, expected_id in urls:
        extracted = extract_drive_folder_id(url)
        assert extracted == expected_id, f"Failed for {url}: expected {expected_id}, got {extracted}"

    # Invalid URL
    assert extract_drive_folder_id("not-a-valid-link") is None

def test_qr_code_generation():
    """Test generating QR code image."""
    url = get_guest_url()
    assert url.startswith("http://") or url.startswith("https://")
    qr_path = generate_qr_code()
    assert qr_path.exists()
    assert qr_path.stat().st_size > 0

def test_vectorized_matching():
    """Test SFace 128-D cosine similarity matching math."""
    # Generate random normalized 128-D vectors
    v1 = np.random.randn(128).astype(np.float32)
    v1 /= np.linalg.norm(v1)

    v2 = np.random.randn(128).astype(np.float32)
    v2 /= np.linalg.norm(v2)

    # Perfect match with itself
    matches = face_engine.match_query_vector(v1, np.array([v1, v2]), threshold=0.363)
    assert len(matches) >= 1
    assert matches[0][0] == 0  # Index 0 is v1
    assert abs(matches[0][1] - 1.0) < 1e-4

def test_guest_page_endpoint():
    """Test guest landing page route."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Take a Selfie" in response.text
    assert "Find My Photos" in response.text

def test_host_auth_and_dashboard():
    """Test authentication and dashboard access control."""
    # Unauthorized access to dashboard stats
    unauth_resp = client.get("/api/dashboard/stats")
    assert unauth_resp.status_code == 401

    # Invalid login
    login_fail = client.post("/api/auth/login", data={"password": "wrongpassword"})
    assert login_fail.status_code == 401

    # Valid login
    login_succ = client.post("/api/auth/login", data={"password": "photo123"})
    assert login_succ.status_code == 200

    # Dashboard page with cookie
    dash_resp = client.get("/dashboard", cookies={"event_host_token": "photo123"})
    assert dash_resp.status_code == 200
    assert "Host Dashboard" in dash_resp.text

def test_guest_search_no_face_handling():
    """Test search with an image containing no face (blank image)."""
    # Create 300x300 blank image
    img = Image.new("RGB", (300, 300), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)

    res = client.post(
        "/api/guest/search",
        files={"file": ("blank.jpg", buf, "image/jpeg")}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert "No face was detected" in data["error"]

def test_zip_download_empty():
    """Test ZIP download validation."""
    res = client.post("/api/guest/download-zip", json={"photo_ids": []})
    assert res.status_code == 400
