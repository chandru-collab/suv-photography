import time
import pytest
from starlette.testclient import TestClient
from backend.app import app
from backend.cache import vector_cache, metadata_cache
from backend.rate_limiter import SlidingWindowRateLimiter

client = TestClient(app)

def test_liveness_health_check():
    """Verify /healthz returns 200 and healthy status."""
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data

def test_readiness_probe():
    """Verify /readyz validates database, models, and cache."""
    res = client.get("/readyz")
    assert res.status_code == 200
    data = res.json()
    assert data["ready"] is True
    assert "checks" in data
    assert data["checks"]["database"]["status"] == "ok"
    assert data["checks"]["face_models"]["status"] == "ok"
    assert data["checks"]["vector_cache"]["status"] == "ok"

def test_prometheus_metrics():
    """Verify /metrics exports Prometheus text format."""
    # Send a request to generate traffic
    client.get("/guest")
    res = client.get("/metrics")
    assert res.status_code == 200
    text = res.text
    assert "http_requests_total" in text
    assert "event_active_requests" in text
    assert "event_cached_faces_total" in text

def test_vector_cache_in_memory():
    """Verify in-memory vector cache operates and invalidates correctly."""
    face_ids, photo_ids, matrix = vector_cache.get_embeddings()
    assert isinstance(face_ids, list)
    assert isinstance(photo_ids, list)
    assert matrix.shape[1] == 128 or matrix.shape[0] == 0
    assert vector_cache.is_warm is True

    # Test invalidation
    vector_cache.invalidate()
    assert vector_cache.is_warm is True

def test_rate_limiter_unit():
    """Verify sliding window rate limiter blocks traffic past threshold."""
    class DummyClient:
        host = "192.168.1.99"
    class DummyRequest:
        headers = {}
        client = DummyClient()

    limiter = SlidingWindowRateLimiter(requests_per_window=3, window_seconds=10)
    req = DummyRequest()

    # First 3 requests allowed
    assert limiter.check(req)[0] is True
    assert limiter.check(req)[0] is True
    assert limiter.check(req)[0] is True

    # 4th request blocked with 429
    allowed, remaining, reset_secs = limiter.check(req)
    assert allowed is False
    assert remaining == 0
    assert reset_secs > 0

def test_etag_caching_and_304():
    """Verify ETag and Cache-Control headers, and 304 response on matching ETag."""
    from backend.database import get_all_photos
    from backend.config import DATA_DIR
    photos = get_all_photos(limit=50)
    real_photo = next((p for p in photos if (DATA_DIR / p["filepath"]).exists()), None)
    if not real_photo:
        pytest.skip("No real photo file on disk to test ETag")

    p_id = real_photo["id"]
    res1 = client.get(f"/api/photos/{p_id}")
    assert res1.status_code == 200
    assert "etag" in res1.headers
    assert "cache-control" in res1.headers
    etag = res1.headers["etag"]

    # Send conditional GET with If-None-Match
    res2 = client.get(f"/api/photos/{p_id}", headers={"if-none-match": etag})
    assert res2.status_code == 304
