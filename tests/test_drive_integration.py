import pytest
from starlette.testclient import TestClient
from pathlib import Path

from backend.app import app
from backend.config import BASE_DIR, HOST_PASSWORD, SESSION_COOKIE_NAME, DEFAULT_DRIVE_FOLDER_ID
from backend.drive_service import drive_manager, find_credentials_file

client = TestClient(app)

def test_credentials_detection():
    """Verify backend securely finds and initializes the Google Cloud credentials."""
    creds_path = find_credentials_file()
    assert creds_path is not None
    assert creds_path.exists()
    assert creds_path.name.endswith(".json")
    assert drive_manager.is_configured is True

def test_folder_id_extraction():
    """Test parsing Google Drive folder links and raw IDs."""
    test_cases = [
        ("https://drive.google.com/drive/folders/1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO", "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"),
        ("https://drive.google.com/drive/u/0/folders/1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO?usp=sharing", "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"),
        ("https://drive.google.com/open?id=1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO", "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"),
        ("1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO", "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"),
    ]
    for url, expected in test_cases:
        assert drive_manager.extract_folder_id(url) == expected

def test_cdn_thumbnail_builder():
    """Verify thumbnail URL resizing for instant Google CDN loading."""
    sample = "https://lh3.googleusercontent.com/drive-storage/AJQWtBNcunq1iw=s220"
    res400 = drive_manager.build_cdn_thumbnail(sample, size=400)
    assert res400.endswith("=s400")
    res1600 = drive_manager.build_cdn_thumbnail(sample, size=1600)
    assert res1600.endswith("=s1600")

def test_gallery_html_pages():
    """Verify /gallery and /gallery/{folder_id} endpoints return HTML."""
    r1 = client.get("/gallery")
    assert r1.status_code == 200
    assert "Google Drive Event Gallery" in r1.text
    assert "Zero local duplication" in r1.text

    r2 = client.get("/gallery/1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO")
    assert r2.status_code == 200
    assert "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO" in r2.text

def test_drive_folder_tree_api():
    """Verify hierarchical folder tree API."""
    resp = client.get("/api/drive/folders/tree?root_id=1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "tree" in data
    assert data["tree"]["id"] == "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"
    # Subfolders like 'Techathon Participants ', 'Videos', etc.
    child_names = [c["name"] for c in data["tree"].get("children", [])]
    assert len(child_names) > 0

def test_drive_gallery_items_api():
    """Verify live retrieval of photos and videos from Drive API."""
    # Test Root folder (TECHATHONX 2K26)
    resp = client.get("/api/drive/gallery/1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "folder" in data
    assert "breadcrumbs" in data
    assert "subfolders" in data
    assert len(data["subfolders"]) >= 3

    # Test Techathon Participants subfolder which has photos
    sub_id = "1Xjq6Bs9TNmj0zoJaoHFOmtKM2ImH5_gg"
    resp_sub = client.get(f"/api/drive/gallery/{sub_id}")
    assert resp_sub.status_code == 200
    sub_data = resp_sub.json()
    assert sub_data["success"] is True
    assert len(sub_data["photos"]) > 0
    first_photo = sub_data["photos"][0]
    assert "id" in first_photo
    assert "cdn_thumb" in first_photo
    assert "preview_url" in first_photo

    # Test Videos subfolder which has videos
    vid_folder_id = "1bFBYiK8my5sEbF8r_f7CAlOaBmEj0scx"
    resp_vid = client.get(f"/api/drive/gallery/{vid_folder_id}")
    assert resp_vid.status_code == 200
    vid_data = resp_vid.json()
    assert vid_data["success"] is True
    assert len(vid_data["videos"]) > 0
    first_vid = vid_data["videos"][0]
    assert "id" in first_vid
    assert first_vid["mimeType"].startswith("video/")

def test_drive_thumbnail_endpoint():
    """Verify /api/drive/thumbnail/{file_id} returns a valid redirect or image."""
    photo_id = "1hQcDFmfy0DQoGxrul2xW_bVHkQ3izNEB"
    resp = client.get(f"/api/drive/thumbnail/{photo_id}?size=400", follow_redirects=False)
    # 307 redirect to Google CDN or 200 image
    assert resp.status_code in (200, 307)
    if resp.status_code == 307:
        assert "lh3.googleusercontent.com" in resp.headers.get("location", "")

def test_credentials_never_exposed():
    """Security audit: ensure credentials, private keys, or client emails are never leaked."""
    for path in ["/gallery", "/api/drive/folders/tree", "/api/drive/gallery/1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"]:
        resp = client.get(path)
        content = resp.text
        assert "private_key" not in content
        assert "BEGIN PRIVATE KEY" not in content
        assert "suv-photography-514705-7a2bae36b793" not in content
