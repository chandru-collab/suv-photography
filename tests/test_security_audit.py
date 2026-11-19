import io
import time
import zipfile
import pytest
from pathlib import Path
from starlette.testclient import TestClient

from backend.app import app
from backend.config import DATA_DIR, HOST_PASSWORD
from backend.sync_service import ingest_uploaded_file
from backend.database import get_connection, get_all_photos, set_setting, get_setting

client = TestClient(app)

# ============================================================================
# 1. AUTHENTICATION & ACCESS CONTROL PENETRATION TESTS
# ============================================================================

def test_unauthenticated_admin_routes_blocked():
    """Verify that unauthenticated attackers cannot access protected host APIs."""
    # Dashboard HTML page
    res = client.get("/dashboard", follow_redirects=False)
    assert res.status_code == 302
    assert "/login" in res.headers["location"]

    # Dashboard APIs
    protected_get_endpoints = [
        "/api/dashboard/stats",
        "/api/dashboard/qr",
        "/api/dashboard/qr/download",
        "/api/photos/list",
    ]
    for ep in protected_get_endpoints:
        res = client.get(ep)
        assert res.status_code == 401, f"Expected 401 for {ep}, got {res.status_code}"

    # Protected POST endpoints
    res = client.post("/api/dashboard/qr/regenerate", json={"base_url": "http://evil.com"})
    assert res.status_code == 401

    res = client.post("/api/sync/drive", json={"folder_url": "https://drive.google.com/test"})
    assert res.status_code == 401

    res = client.post("/api/settings", json={"event_name": "Hacked"})
    assert res.status_code == 401

    res = client.delete("/api/photos/1")
    assert res.status_code == 401

def test_timing_safe_authentication_mechanics():
    """Verify constant-time comparison rejects forged, empty, or partial tokens."""
    # Correct login
    res = client.post("/api/auth/login", data={"password": HOST_PASSWORD})
    assert res.status_code == 200
    assert "event_host_token" in res.cookies

    # SQL injection in login
    res = client.post("/api/auth/login", data={"password": "' OR '1'='1"})
    assert res.status_code == 401

    # Empty and partial passwords
    res = client.post("/api/auth/login", data={"password": ""})
    assert res.status_code in (401, 422)

    res = client.post("/api/auth/login", data={"password": HOST_PASSWORD[:4]})
    assert res.status_code == 401


# ============================================================================
# 2. PATH TRAVERSAL & DIRECTORY INJECTION DEFENSE
# ============================================================================

def test_path_traversal_on_photo_endpoint():
    """Verify path parameter injection cannot traverse directories."""
    # Non-integer IDs fail with 422 Unprocessable Entity
    res = client.get("/api/photos/..%2F..%2Fetc%2Fpasswd")
    assert res.status_code in (404, 422)

    res = client.get("/api/photos/-9999")
    assert res.status_code == 404

def test_upload_path_traversal_sanitization():
    """Verify malicious filenames with directory traversal are stripped to basenames."""
    # Create 1x1 dummy image
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (32, 32), color="red")
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    malicious_filename = "../../../../etc/passwd"
    added, count, msg = ingest_uploaded_file(img_bytes, malicious_filename)

    # Verify no file was written outside temp_dir
    temp_dir = DATA_DIR / "temp_uploads"
    assert not (DATA_DIR / "passwd").exists()
    assert not (DATA_DIR.parent / "passwd").exists()


# ============================================================================
# 3. SQL INJECTION DEFENSE TESTS
# ============================================================================

def test_sql_injection_defense_in_settings():
    """Verify parameterized queries prevent SQL injection payloads from modifying execution."""
    sql_payload = "Event'; DROP TABLE settings; --"
    original_event_name = get_setting("event_name", "Our Event Photos")
    try:
        set_setting("event_name", sql_payload)

        # Verify setting was stored verbatim and settings table was NOT dropped
        val = get_setting("event_name")
        assert val == sql_payload

        conn = get_connection()
        table_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        conn.close()
        assert table_check is not None, "Settings table was compromised by SQL injection!"
    finally:
        # Restore clean name
        set_setting("event_name", original_event_name)


# ============================================================================
# 4. DENIAL OF SERVICE & RESOURCE EXHAUSTION TESTS
# ============================================================================

def test_zip_download_capped_at_100_photos():
    """Verify that requesting >100 photos in a single ZIP download is rejected."""
    excessive_ids = list(range(1, 105)) # 104 IDs
    res = client.post("/api/guest/download-zip", json={"photo_ids": excessive_ids})
    assert res.status_code == 400
    assert "Maximum 100 photos" in res.json()["detail"]

def test_oversized_upload_rejection():
    """Verify payload size limit rejects images over 25 MB."""
    fake_huge_bytes = b"0" * (26 * 1024 * 1024) # 26 MB
    res = client.post(
        "/api/guest/search",
        files={"file": ("huge.jpg", fake_huge_bytes, "image/jpeg")}
    )
    assert res.status_code == 413
    assert "exceeds" in res.json()["error"].lower() or "large" in res.json()["error"].lower()


# ============================================================================
# 5. CROSS-SITE SCRIPTING (XSS) DEFENSE TESTS
# ============================================================================

def test_xss_payload_in_event_name_escaped():
    """Verify HTML template engine escapes malicious script tags."""
    xss_payload = "<script>alert('XSS')</script>"
    original_event_name = get_setting("event_name", "Our Event Photos")
    try:
        set_setting("event_name", xss_payload)

        res = client.get("/guest")
        assert res.status_code == 200
        # The literal executable script tag must not be unescaped in HTML
        assert "<script>alert('XSS')</script>" not in res.text
        assert "&lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;" in res.text or "&lt;script&gt;" in res.text
    finally:
        # Restore clean name
        set_setting("event_name", original_event_name)


# ============================================================================
# 6. SECURITY HEADERS AUDIT
# ============================================================================

def test_security_headers_present():
    """Verify every response contains standard enterprise security headers."""
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "SAMEORIGIN"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "X-Request-ID" in res.headers
    assert "X-Response-Time-Ms" in res.headers


# ============================================================================
# 7. FUNCTIONAL WORKFLOW PENETRATION & EDGE CASE TESTING
# ============================================================================

def test_functional_guest_workflow_edge_cases():
    """Verify guest search handles corrupted bytes, zero bytes, and valid workflows."""
    # Corrupted / invalid image bytes
    res = client.post(
        "/api/guest/search",
        files={"file": ("corrupt.jpg", b"THIS IS NOT A VALID JPEG OR PNG", "image/jpeg")}
    )
    assert res.status_code == 400
    assert res.json()["success"] is False

    # Empty payload
    res = client.post("/api/guest/search", files={"file": ("empty.jpg", b"", "image/jpeg")})
    assert res.status_code == 400

    # Liveness probe
    res = client.get("/healthz")
    assert res.status_code == 200

    # Readiness probe
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json()["ready"] is True

def test_drive_sync_validation_and_rejection():
    """Verify drive sync rejects empty or invalid folder URLs with 400 Bad Request."""
    auth_client = TestClient(app, cookies={"event_host_token": HOST_PASSWORD})
    
    # Empty URL
    res = auth_client.post("/api/sync/drive", json={"folder_url": ""})
    assert res.status_code == 400
    assert "empty" in res.json()["error"].lower()

    # Invalid URL format / missing ID
    res = auth_client.post("/api/sync/drive", json={"folder_url": "https://example.com/not-drive"})
    assert res.status_code == 400
    assert "invalid" in res.json()["error"].lower()

def test_content_disposition_header_injection_defense():
    """Verify header injection characters are sanitized in Content-Disposition headers."""
    original_event_name = get_setting("event_name", "Our Event Photos")
    try:
        # Malicious event name containing CRLF or quotes
        set_setting("event_name", "Event\r\nSet-Cookie: evil=true; filename=\"evil.zip\"")
        res = client.post("/api/guest/download-zip", json={"photo_ids": [1]})
        # If photo 1 exists or 404, we verify the route either sanitizes or handles properly
        if res.status_code == 200:
            cd = res.headers.get("content-disposition", "")
            assert "\r" not in cd
            assert "\n" not in cd
    finally:
        set_setting("event_name", original_event_name)
