import os
import io
import re
import time
import secrets
import zipfile
import logging
import requests
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager
from pydantic import BaseModel

from fastapi import FastAPI, Request, Response, Form, File, UploadFile, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from backend.config import (
    BASE_DIR,
    DATA_DIR,
    PHOTOS_DIR,
    THUMBNAILS_DIR,
    QR_DIR,
    HOST_PASSWORD,
    SESSION_COOKIE_NAME,
    DEFAULT_MATCH_THRESHOLD,
    SERVER_PORT,
    DEFAULT_DRIVE_FOLDER_ID,
    get_local_ip
)
from backend.drive_service import drive_manager
from backend.database import (
    init_db,
    get_setting,
    set_setting,
    get_all_settings,
    get_all_face_embeddings,
    get_photos_by_ids,
    get_photo_by_id,
    get_all_photos,
    get_stats,
    delete_photo,
    clear_photos_not_in_folders,
    clear_all_drive_photos
)
from backend.face_engine import face_engine
from backend.qr_service import get_guest_url, generate_qr_code, get_qr_base64, DEFAULT_QR_PATH
from backend.sync_service import (
    start_background_drive_sync,
    cancel_current_sync,
    sync_status,
    ingest_uploaded_file,
    extract_drive_folder_id,
    VALID_EXTENSIONS
)
from backend.cache import vector_cache, metadata_cache
from backend.rate_limiter import require_search_rate_limit
from backend.monitoring import metrics, check_readiness
from backend.middleware import ProductionMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

# Initialize database tables on module load
init_db()

# Generate initial QR code if not already present
if not DEFAULT_QR_PATH.exists():
    generate_qr_code()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up in-memory vector embeddings cache on server startup."""
    logger.info("Production Startup: warming face embedding vector cache...")
    vector_cache.get_embeddings()
    logger.info(f"Production Startup: warmed {vector_cache.count} face embeddings in RAM.")
    if drive_manager.is_configured:
        try:
            root_url = get_setting("drive_folder_url", DEFAULT_DRIVE_FOLDER_ID)
            root_id = drive_manager.extract_folder_id(root_url)
            if root_id:
                drive_manager.get_descendant_folder_ids(root_id)
        except Exception:
            pass
    yield

app = FastAPI(title="Event Photo Sharing App", version="2.0.0 (Production)", lifespan=lifespan)

# Allow CORS for localhost, LAN IPs, and custom domains with credentials support
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Attach Production Middleware (tracing, latency metrics, security headers, structured JSON logging)
app.add_middleware(ProductionMiddleware)

# ==========================================
# HEALTH CHECKS & PROMETHEUS METRICS
# ==========================================

@app.get("/healthz", tags=["Monitoring"])
async def liveness_probe():
    """Liveness probe: verifies worker process is running and accepting traffic."""
    return {"status": "healthy", "timestamp": time.time()}

@app.get("/readyz", tags=["Monitoring"])
async def readiness_probe():
    """Readiness probe: verifies database, ONNX models, storage, and in-memory cache."""
    status_data = check_readiness()
    if not status_data["ready"]:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=status_data)
    return status_data

@app.get("/metrics", response_class=PlainTextResponse, tags=["Monitoring"])
async def prometheus_metrics():
    """Prometheus-compatible metrics endpoint for Grafana, Datadog, Prometheus."""
    return metrics.generate_prometheus()

# Mount static files and templates
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Constant-time timing-attack safe authentication
def is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not HOST_PASSWORD:
        return False
    return secrets.compare_digest(token, HOST_PASSWORD)

def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )

# ==========================================
# PUBLIC GUEST ROUTES
# ==========================================

@app.get("/", response_class=HTMLResponse)
@app.get("/guest", response_class=HTMLResponse)
async def guest_page(request: Request):
    """Guest-facing landing page with luxury cover screen and selfie search."""
    settings = get_all_settings()
    return templates.TemplateResponse(
        request=request,
        name="guest.html",
        context={
            "event_name": settings.get("event_name", "Event Photos"),
            "couple_title": settings.get("couple_title", "Bride & Groom"),
            "event_date": settings.get("event_date", "17 NOV 2026"),
            "event_location": settings.get("event_location", "CHENNAI"),
            "cover_photo_path": settings.get("cover_photo_path", "/static/img/default_wedding_cover_clean.jpg"),
            "settings": settings
        }
    )

@app.get("/gallery", response_class=HTMLResponse)
@app.get("/gallery/{folder_id}", response_class=HTMLResponse)
async def gallery_page(request: Request, folder_id: Optional[str] = None):
    """Google Drive Event & Client Gallery page (zero local file duplication)."""
    root_folder = get_setting("drive_folder_url", DEFAULT_DRIVE_FOLDER_ID)
    root_folder_id = drive_manager.extract_folder_id(root_folder) or DEFAULT_DRIVE_FOLDER_ID
    active_folder_id = folder_id or root_folder_id
    event_name = get_setting("event_name", "Event Gallery")
    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={
            "event_name": event_name,
            "folder_id": active_folder_id,
            "root_folder_id": root_folder_id
        }
    )

@app.post("/api/guest/detect-faces")
async def guest_detect_faces(
    file: UploadFile = File(...),
    rate_limit: None = Depends(require_search_rate_limit)
):
    """
    Fast pre-analysis of selfie: detects all faces and returns their cropped
    avatar thumbnails so guests can choose Solo Search or Duo/Couple Search.
    Rate-limited for production resilience.
    """
    contents = await file.read()
    if not contents:
        return JSONResponse(status_code=400, content={"success": False, "error": "No image uploaded."})
    if len(contents) > 25 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"success": False, "error": "Image file too large (max 25MB)."})

    img_bgr = face_engine.load_image_from_bytes(contents)
    if img_bgr is None:
        return JSONResponse(status_code=400, content={"success": False, "error": "Could not read image."})

    faces = face_engine.extract_faces_with_crops(img_bgr)
    if not faces:
        return {"success": False, "error": "No face detected in photo. Please ensure good lighting and face camera."}

    results = []
    for f in faces:
        results.append({
            "index": f["index"],
            "score": f["score"],
            "avatar_b64": f["avatar_b64"]
        })

    return {
        "success": True,
        "count": len(results),
        "faces": results,
        "is_couple": len(results) >= 2
    }

@app.post("/api/guest/search")
async def guest_search(
    file: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),
    mode: str = Form("single"),
    face_index: Optional[int] = Form(None),
    rate_limit: None = Depends(require_search_rate_limit)
):
    """
    Guest uploads or captures a selfie.
    Uses ultra-fast In-Memory Vector Cache to scale past 1000 RPS.
    Supports:
    - Single selfie search (standard solo)
    - Specific face selection from multi-face photo
    - Duo/Couple & Friends search:
      * 'together' (intersection: both people in the same frame)
      * 'either' (union: either person in the frame)
    """
    search_start = time.perf_counter()
    contents = await file.read()
    if not contents:
        return JSONResponse(status_code=400, content={"success": False, "error": "No image uploaded."})
    if len(contents) > 25 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"success": False, "error": "Image file too large (max 25MB)."})

    img_bgr = face_engine.load_image_from_bytes(contents)
    if img_bgr is None:
        return JSONResponse(status_code=400, content={"success": False, "error": "Could not read image file. Please take a clear photo."})

    query_embeddings = []
    note = ""

    # Check if a second companion file was uploaded
    if file2 is not None:
        c2 = await file2.read()
        if c2:
            img2 = face_engine.load_image_from_bytes(c2)
            if img2 is not None:
                e1, _ = face_engine.extract_single_face(img_bgr)
                e2, _ = face_engine.extract_single_face(img2)
                if e1 is not None and e2 is not None:
                    query_embeddings = [e1, e2]

    # Single file analysis if query_embeddings not already set
    if not query_embeddings:
        detected = face_engine.extract_faces_with_crops(img_bgr)
        if not detected:
            return JSONResponse(
                status_code=200,
                content={
                    "success": False,
                    "error": "No face was detected in your selfie. Please ensure good lighting and face the camera directly."
                }
            )

        if len(detected) >= 2 and mode in ("together", "either"):
            query_embeddings = [detected[0]["embedding"], detected[1]["embedding"]]
            note = f"Duo search ({mode}): Searching for photos of both people detected in your selfie."
        elif face_index is not None and 0 <= face_index < len(detected):
            query_embeddings = [detected[face_index]["embedding"]]
        else:
            query_embeddings = [detected[0]["embedding"]]
            if len(detected) > 1 and mode == "single":
                note = f"Note: {len(detected)} faces detected in selfie. Largest face was used. Try Couple Mode to search together!"

    if not query_embeddings:
        return JSONResponse(status_code=200, content={"success": False, "error": "Could not process faces for matching."})

    # Scope face search strictly to the active Google Drive folder and its subfolders
    active_folder_ids = None
    if drive_manager.is_configured:
        root_url = get_setting("drive_folder_url", DEFAULT_DRIVE_FOLDER_ID)
        root_id = drive_manager.extract_folder_id(root_url)
        if root_id:
            active_folder_ids = drive_manager.get_descendant_folder_ids(root_id)

    # High-performance In-Memory Vector Cache strictly scoped to active event
    face_ids, photo_ids, embeddings_matrix = vector_cache.get_embeddings(folder_ids=active_folder_ids)
    if len(photo_ids) == 0:
        # If active Drive folder has no indexed photos yet, automatically start parallel sync and wait briefly
        if root_id:
            logger.info(f"Active Drive folder {root_id} has 0 indexed faces. Starting auto-sync...")
            start_background_drive_sync(root_id, force=True, reindex=True)
            for _ in range(12):
                time.sleep(0.3)
                face_ids, photo_ids, embeddings_matrix = vector_cache.get_embeddings(folder_ids=active_folder_ids)
                if len(photo_ids) > 0:
                    break

    if len(photo_ids) == 0:
        return {
            "success": True,
            "count": 0,
            "photos": [],
            "message": "No photos have been indexed for this event yet."
        }

    # Match threshold
    try:
        thresh = float(get_setting("match_threshold", str(DEFAULT_MATCH_THRESHOLD)))
    except ValueError:
        thresh = DEFAULT_MATCH_THRESHOLD

    # Execute matching
    if len(query_embeddings) > 1 and mode in ("together", "either"):
        matched_results = face_engine.match_multi_person(
            query_embeddings,
            embeddings_matrix,
            photo_ids,
            threshold=thresh,
            mode=mode
        )
        photo_best_scores = {p_id: score for p_id, score in matched_results}
        matched_photo_ids = [p_id for p_id, _ in matched_results]
    else:
        # Standard single query vector search
        raw_matches = face_engine.match_query_vector(query_embeddings[0], embeddings_matrix, threshold=thresh)
        photo_best_scores = {}
        for idx, sim in raw_matches:
            p_id = photo_ids[idx]
            if p_id not in photo_best_scores or sim > photo_best_scores[p_id]:
                photo_best_scores[p_id] = sim
        matched_photo_ids = list(photo_best_scores.keys())

    matched_photos = get_photos_by_ids(matched_photo_ids, folder_ids=active_folder_ids)

    # Format result payload
    response_items = []
    for p in matched_photos:
        score = photo_best_scores.get(p["id"], 0.0)
        drive_file_id = p.get("drive_file_id") or (p["filepath"].replace("drive://", "") if p["filepath"].startswith("drive://") else None)

        if drive_file_id:
            cdn_thumb = f"https://lh3.googleusercontent.com/d/{drive_file_id}=s400"
            cdn_preview = f"https://lh3.googleusercontent.com/d/{drive_file_id}=s1600"
            thumb_url = cdn_thumb
            preview_url = cdn_preview
        else:
            thumb_url = f"/api/thumbnails/{p['id']}" if p["thumbnail_path"] else f"/api/photos/{p['id']}"
            preview_url = f"/api/photos/{p['id']}"

        response_items.append({
            "id": p["id"],
            "filename": p["filename"],
            "score": round(float(score), 3),
            "url": f"/api/photos/{p['id']}",
            "preview_url": preview_url,
            "thumbnail_url": thumb_url,
            "download_url": f"/api/photos/{p['id']}?download=true",
            "drive_file_id": drive_file_id,
            "width": p["width"],
            "height": p["height"]
        })

    # Sort descending by match score
    response_items.sort(key=lambda x: x["score"], reverse=True)

    metrics.record_face_search(time.perf_counter() - search_start)

    return {
        "success": True,
        "count": len(response_items),
        "photos": response_items,
        "note": note,
        "mode": mode,
        "persons_count": len(query_embeddings)
    }

@app.get("/api/photos/list", dependencies=[Depends(require_auth)])
async def list_photos(limit: int = 100, offset: int = 0):
    """Paginated photo list for dashboard table with instant thumbnail URLs."""
    photos = get_all_photos(limit=limit, offset=offset)
    formatted = []
    for p in photos:
        drive_file_id = p.get("drive_file_id") or (p["filepath"].replace("drive://", "") if p["filepath"].startswith("drive://") else None)
        if drive_file_id:
            thumb_url = f"https://lh3.googleusercontent.com/d/{drive_file_id}=s200"
        else:
            thumb_url = f"/api/thumbnails/{p['id']}" if p["thumbnail_path"] else f"/api/photos/{p['id']}"

        formatted.append({
            "id": p["id"],
            "filename": p["filename"],
            "source": p["source"],
            "face_count": p["face_count"],
            "file_size": p["file_size"],
            "created_at": p["created_at"],
            "url": f"/api/photos/{p['id']}",
            "thumbnail_url": thumb_url
        })
    return {"success": True, "photos": formatted}

@app.get("/api/photos/{photo_id}")
async def get_photo(photo_id: int, request: Request, download: bool = False):
    """Retrieve photo with instant Google Edge CDN preview or attachment download."""
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    if photo["filepath"].startswith("drive://") or photo.get("drive_file_id"):
        drive_file_id = photo.get("drive_file_id") or photo["filepath"].replace("drive://", "")
        if not download:
            return RedirectResponse(
                url=f"https://lh3.googleusercontent.com/d/{drive_file_id}=s1600",
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"}
            )
        return await get_drive_media(drive_file_id, request=request, download=download)

    full_path = DATA_DIR / photo["filepath"]
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Photo file not found on disk")

    # High-throughput ETag conditional check (avoids redundant bandwidth)
    etag = f'"{photo["id"]}-{photo["file_size"]}"'
    client_etag = request.headers.get("if-none-match")
    if client_etag == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    media_type = "image/jpeg"
    ext = full_path.suffix.lower()
    if ext == ".png":
        media_type = "image/png"
    elif ext == ".webp":
        media_type = "image/webp"

    filename = photo["filename"]
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"
    }
    if download:
        safe_download_name = re.sub(r"[^\w\-.]", "_", filename) or "photo.jpg"
        headers["Content-Disposition"] = f'attachment; filename="{safe_download_name}"'

    return FileResponse(str(full_path), media_type=media_type, headers=headers)

@app.get("/api/thumbnails/{photo_id}")
async def get_thumbnail(photo_id: int, request: Request, proxy: bool = False):
    """
    Retrieve lightweight photo thumbnail.
    Redirects to Google Edge CDN for instant delivery; transparently proxies and caches via
    Google Service Account if Google CDN access is restricted or fails in client browsers.
    """
    photo = get_photo_by_id(photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    # 1. Check local cached thumbnail file
    local_thumb = None
    if photo.get("thumbnail_path") and not photo["thumbnail_path"].startswith("drive_thumb://"):
        candidate = DATA_DIR / photo["thumbnail_path"]
        if candidate.exists():
            local_thumb = candidate
    cached_proxy = THUMBNAILS_DIR / f"thumb_{photo_id}.jpg"
    if cached_proxy.exists():
        local_thumb = cached_proxy

    if local_thumb and local_thumb.exists():
        etag = f'W/"thumb-{photo["id"]}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED)
        return FileResponse(
            str(local_thumb),
            media_type="image/jpeg",
            headers={"ETag": etag, "Cache-Control": "public, max-age=604800, immutable"}
        )

    # 2. Fast direct Google Edge CDN redirect (unless proxy mode requested)
    is_drive = photo["filepath"].startswith("drive://") or photo.get("drive_file_id")
    if is_drive and not proxy:
        drive_file_id = photo.get("drive_file_id") or photo["filepath"].replace("drive://", "")
        return RedirectResponse(
            url=f"https://lh3.googleusercontent.com/d/{drive_file_id}=s400",
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Cache-Control": "public, max-age=604800, immutable"}
        )

    # 3. Authenticated Drive Proxy: download via service account, cache locally, and stream
    if is_drive:
        drive_file_id = photo.get("drive_file_id") or photo["filepath"].replace("drive://", "")
        try:
            raw_thumb = drive_manager.build_cdn_thumbnail(f"https://drive.google.com/thumbnail?id={drive_file_id}", size=400)
            img_bytes = None
            try:
                r = requests.get(raw_thumb, timeout=8)
                if r.status_code == 200 and r.content:
                    img_bytes = r.content
            except Exception:
                pass

            if not img_bytes:
                img_bytes = drive_manager.download_file_bytes(drive_file_id)

            if img_bytes:
                THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
                face_engine.create_thumbnail(img_bytes, str(cached_proxy), max_size=400)
                if cached_proxy.exists():
                    return FileResponse(
                        str(cached_proxy),
                        media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800, immutable"}
                    )
        except Exception as e:
            logger.warning(f"Drive proxy thumbnail generation failed for photo {photo_id}: {e}")

    return await get_photo(photo_id, request=request, download=False)

class DownloadZipRequest(BaseModel):
    photo_ids: List[int]

@app.post("/api/guest/download-zip")
async def download_matched_zip(payload: DownloadZipRequest):
    """Download all matched photos as a single ZIP file."""
    if not payload.photo_ids:
        raise HTTPException(status_code=400, detail="No photos selected for download")

    if len(payload.photo_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 photos can be downloaded in a single ZIP.")

    photos = get_photos_by_ids(payload.photo_ids)
    if not photos:
        raise HTTPException(status_code=404, detail="No valid photos found")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for idx, p in enumerate(photos):
            arcname = f"{idx + 1:03d}_{p['filename']}"
            if p["filepath"].startswith("drive://") or p.get("drive_file_id"):
                fid = p.get("drive_file_id") or p["filepath"].replace("drive://", "")
                try:
                    data = drive_manager.download_file_bytes(fid)
                    zip_file.writestr(arcname, data)
                except Exception as e:
                    logger.warning(f"Could not include Drive file {fid} in zip: {e}")
            else:
                photo_file = DATA_DIR / p["filepath"]
                if photo_file.exists():
                    zip_file.write(str(photo_file), arcname=arcname)

    zip_buffer.seek(0)
    raw_event_name = get_setting("event_name", "Event")
    safe_event_name = re.sub(r"[^\w\-.]", "_", raw_event_name).strip("_") or "Event"
    zip_filename = f"{safe_event_name}_my_photos.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'}
    )

# ==========================================
# GOOGLE DRIVE API PRIMARY DATABASE ROUTES
# ==========================================

@app.get("/api/drive/folders/tree")
async def get_drive_folder_tree(root_id: Optional[str] = None):
    """Hierarchical organization of Client and Event folders directly from Drive."""
    if not root_id:
        root_url = get_setting("drive_folder_url", DEFAULT_DRIVE_FOLDER_ID)
        root_id = drive_manager.extract_folder_id(root_url) or DEFAULT_DRIVE_FOLDER_ID
    tree = drive_manager.list_folder_structure(root_id, max_depth=3)
    return {"success": True, "tree": tree}

@app.get("/api/drive/gallery/{folder_id}")
async def get_drive_gallery_items(folder_id: str, pageToken: Optional[str] = None, pageSize: int = 150):
    """Fetch photos and videos directly from Google Drive API with zero disk downloads."""
    if folder_id == "root" or not folder_id:
        root_url = get_setting("drive_folder_url", DEFAULT_DRIVE_FOLDER_ID)
        folder_id = drive_manager.extract_folder_id(root_url) or DEFAULT_DRIVE_FOLDER_ID
    items = drive_manager.list_gallery_contents(folder_id, page_size=pageSize, page_token=pageToken)
    return items

@app.get("/api/drive/thumbnail/{file_id}")
async def get_drive_thumbnail(file_id: str, size: int = 400, redirect: bool = True):
    """Return optimized Drive thumbnail using instant Google Edge CDN redirect."""
    cdn_url = f"https://lh3.googleusercontent.com/d/{file_id}=s{size}"
    if redirect:
        return RedirectResponse(
            url=cdn_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Cache-Control": "public, max-age=604800, immutable"}
        )
    try:
        data = drive_manager.download_file_bytes(file_id)
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800, immutable"}
        )
    except Exception:
        return RedirectResponse(
            url=cdn_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Cache-Control": "public, max-age=604800, immutable"}
        )

@app.api_route("/api/drive/media/{file_id}", methods=["GET", "HEAD"])
async def get_drive_media(file_id: str, request: Request, download: bool = False):
    """
    Stream full-res photo or video directly from Google Drive API with caching.
    - For images (not download): redirects instantly to Google Edge CDN (s1600).
    - For videos: streams with native HTTP 206 Partial Content & Range support so HTML5
      <video> can play, buffer, and seek without black screen errors.
    - For downloads: streams with Content-Disposition attachment.
    """
    try:
        meta = drive_manager.get_file_metadata(file_id)
        mime_type = meta.get("mimeType", "application/octet-stream")
        name = meta.get("name", f"drive_{file_id}")
        file_size = int(meta.get("size", 0) or 0)
        is_video = mime_type.startswith("video/") or name.lower().endswith((".mp4", ".mov", ".avi", ".webm", ".mkv"))

        # 1. Images for preview: Fast Google Edge CDN redirect
        if not download and not is_video:
            return RedirectResponse(
                url=f"https://lh3.googleusercontent.com/d/{file_id}=s1600",
                status_code=status.HTTP_307_TEMPORARY_REDIRECT,
                headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"}
            )

        # 2. Videos or Downloads: Direct authenticated streaming from Google Drive
        safe_name = re.sub(r"[^\w\-.]", "_", name) or "download"
        token = drive_manager.get_access_token()
        range_header = request.headers.get("range")

        headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"
        }
        if download:
            headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        else:
            headers["Content-Disposition"] = f'inline; filename="{safe_name}"'

        # If HEAD request (Safari/Edge send this to inspect range/size before play)
        if request.method == "HEAD":
            headers["Content-Type"] = mime_type
            if file_size > 0:
                headers["Content-Length"] = str(file_size)
            return Response(status_code=200, headers=headers)

        if token:
            drive_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            drive_req_headers = {"Authorization": f"Bearer {token}"}
            if range_header:
                drive_req_headers["Range"] = range_header

            r = requests.get(drive_url, headers=drive_req_headers, stream=True, timeout=60)
            status_code = r.status_code

            # Forward relevant response headers from Google Drive
            for h in ["Content-Range", "Content-Length", "Content-Type"]:
                if h in r.headers:
                    headers[h] = r.headers[h]

            if "Content-Type" not in headers:
                headers["Content-Type"] = mime_type

            def content_iterator():
                for chunk in r.iter_content(chunk_size=1024 * 256): # 256KB chunks
                    if chunk:
                        yield chunk

            return StreamingResponse(content_iterator(), status_code=status_code, headers=headers)

        # Fallback if no token: use MediaIoBaseDownload generator
        headers["Content-Type"] = mime_type
        return StreamingResponse(
            drive_manager.stream_file_chunks(file_id),
            media_type=mime_type,
            headers=headers
        )

    except Exception as e:
        logger.error(f"Error streaming Drive file {file_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to stream media from Google Drive")

class SelectEventRequest(BaseModel):
    folder_id: str
    event_name: Optional[str] = None
    trigger_sync: bool = False

@app.post("/api/drive/select-event", dependencies=[Depends(require_auth)])
async def select_drive_event(payload: SelectEventRequest):
    """Set an active event folder from Drive, optionally triggering face indexing."""
    folder_id = drive_manager.extract_folder_id(payload.folder_id)
    if not folder_id:
        raise HTTPException(status_code=400, detail="Invalid folder ID")
    folder_info = drive_manager.get_folder_metadata(folder_id)
    name = payload.event_name or folder_info.get("name", "Event Photos")
    set_setting("drive_folder_url", f"https://drive.google.com/drive/folders/{folder_id}")
    set_setting("event_name", name)
    if payload.trigger_sync:
        start_background_drive_sync(folder_id)
    return {"success": True, "event_name": name, "folder_id": folder_id}

# ==========================================
# HOST DASHBOARD & ADMIN ROUTES
# ==========================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Host login page."""
    if is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/api/auth/login")
async def handle_login(response: Response, password: str = Form(...)):
    """Authenticate host and set session cookie."""
    if password == HOST_PASSWORD:
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=password,
            httponly=True,
            samesite="lax",
            max_age=86400 * 30 # 30 days
        )
        return {"success": True, "redirect": "/dashboard"}
    return JSONResponse(status_code=401, content={"success": False, "error": "Incorrect password"})

@app.post("/api/auth/logout")
async def handle_logout(response: Response):
    """Log out host."""
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"success": True, "redirect": "/login"}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Host administration dashboard."""
    if not is_authenticated(request):
        return RedirectResponse(url="/login", status_code=302)

    settings = get_all_settings()
    stats = get_stats()
    guest_url = get_guest_url()
    qr_data = get_qr_base64()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "settings": settings,
            "stats": stats,
            "guest_url": guest_url,
            "qr_data": qr_data,
            "server_port": SERVER_PORT,
            "local_ip": get_local_ip()
        }
    )

@app.get("/api/dashboard/stats", dependencies=[Depends(require_auth)])
async def get_dashboard_stats():
    """Return dashboard analytics & counters."""
    stats = get_stats()
    settings = get_all_settings()
    return {"success": True, "stats": stats, "settings": settings}

@app.get("/api/dashboard/qr", dependencies=[Depends(require_auth)])
async def get_qr():
    """Return current QR code image base64 and guest URL."""
    return {
        "success": True,
        "qr_image": get_qr_base64(),
        "guest_url": get_guest_url(),
        "download_url": "/api/dashboard/qr/download"
    }

@app.get("/api/dashboard/qr/download", dependencies=[Depends(require_auth)])
async def download_qr_image():
    """Download default event QR code image."""
    if not DEFAULT_QR_PATH.exists():
        generate_qr_code()
    return FileResponse(
        str(DEFAULT_QR_PATH),
        media_type="image/png",
        headers={"Content-Disposition": 'attachment; filename="event_guest_qr.png"'}
    )

class RegenerateQRRequest(BaseModel):
    base_url: str

@app.post("/api/dashboard/qr/regenerate", dependencies=[Depends(require_auth)])
async def regenerate_qr(payload: RegenerateQRRequest):
    """Regenerate default QR code with custom base URL."""
    url = payload.base_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        return JSONResponse(status_code=400, content={"success": False, "error": "URL must start with http:// or https://"})

    set_setting("guest_base_url", url)
    generate_qr_code(url)
    return {
        "success": True,
        "guest_url": url,
        "qr_image": get_qr_base64()
    }

class SyncDriveRequest(BaseModel):
    folder_url: str
    force: bool = True

@app.get("/api/drive/check-access", dependencies=[Depends(require_auth)])
async def check_drive_access(folder_url: str):
    """Diagnostic endpoint to verify folder access and permissions."""
    folder_id = extract_drive_folder_id(folder_url)
    if not folder_id:
        return {"success": False, "error": "Invalid Google Drive folder link or folder ID."}

    if not drive_manager.is_configured:
        return {
            "success": False,
            "error": "Google Drive credentials not configured on server.",
            "hint": "Set GOOGLE_CREDENTIALS_JSON in Render Environment Variables."
        }

    try:
        meta = drive_manager.get_folder_metadata(folder_id)
        q = f"'{folder_id}' in parents and trashed = false"
        res = drive_manager.service.files().list(
            q=q,
            pageSize=20,
            fields="files(id, name, mimeType)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = res.get("files", [])
        return {
            "success": True,
            "folder_id": folder_id,
            "folder_name": meta.get("name"),
            "file_count": len(files),
            "sample_files": [f["name"] for f in files[:5]],
            "service_account": "suv-photography@suv-photography-514705.iam.gserviceaccount.com"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "hint": "Please share this folder with: suv-photography@suv-photography-514705.iam.gserviceaccount.com"
        }

@app.post("/api/sync/drive", dependencies=[Depends(require_auth)])
async def trigger_drive_sync(payload: SyncDriveRequest):
    """Trigger fast Google Drive synchronization in background (non-blocking, parallel)."""
    folder_url = payload.folder_url.strip()
    if not folder_url:
        return JSONResponse(status_code=400, content={"success": False, "error": "Folder URL cannot be empty."})

    folder_id = extract_drive_folder_id(folder_url)
    if not folder_id:
        return JSONResponse(status_code=400, content={"success": False, "error": "Invalid Google Drive folder link or folder ID."})

    # Clear previous drive photos from other folders so guest search only matches the active folder
    valid_ids = drive_manager.get_descendant_folder_ids(folder_id)
    clear_photos_not_in_folders(valid_ids)

    # If force=True or folder changed, cleanly cancel prior sync and start new one immediately
    start_background_drive_sync(folder_url, force=payload.force)
    return {"success": True, "message": "Fast Google Drive sync started.", "folder_id": folder_id}

@app.post("/api/sync/cancel", dependencies=[Depends(require_auth)])
async def cancel_sync_endpoint():
    """Immediately stop running sync."""
    cancel_current_sync()
    return {"success": True, "message": "Sync cancelled."}

class SwitchFolderRequest(BaseModel):
    folder_url: str
    auto_sync: bool = True
    clear_previous: bool = True

@app.post("/api/drive/switch-folder")
async def switch_drive_folder(payload: SwitchFolderRequest):
    """
    Instantly switch active Drive folder and return metadata for immediate gallery display.
    Zero waiting for sync; fetches directly from Google Drive API in milliseconds.
    If clear_previous=True, purges previous event photos so guest face recognition strictly returns the new event.
    """
    url_or_id = payload.folder_url.strip()
    if not url_or_id:
        raise HTTPException(status_code=400, detail="Folder link or ID is required")

    folder_id = drive_manager.extract_folder_id(url_or_id)
    if not folder_id:
        raise HTTPException(status_code=400, detail="Invalid Google Drive folder link or folder ID")

    try:
        folder_meta = drive_manager.get_folder_metadata(folder_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot access Google Drive folder: {e}")

    folder_name = folder_meta.get("name", "Event Photos")
    set_setting("drive_folder_url", f"https://drive.google.com/drive/folders/{folder_id}")
    set_setting("event_name", folder_name)

    # Purge old Google Drive event photos so guest search only matches the active folder
    if payload.clear_previous:
        valid_ids = drive_manager.get_descendant_folder_ids(folder_id)
        clear_photos_not_in_folders(valid_ids)

    if payload.auto_sync:
        start_background_drive_sync(folder_id, force=True)

    return {
        "success": True,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "redirect_url": f"/gallery/{folder_id}"
    }

@app.post("/api/photos/clear-previous", dependencies=[Depends(require_auth)])
async def clear_previous_photos():
    """Purge all photos not in the current active Google Drive folder."""
    root_url = get_setting("drive_folder_url", DEFAULT_DRIVE_FOLDER_ID)
    root_id = drive_manager.extract_folder_id(root_url)
    if root_id:
        valid_ids = drive_manager.get_descendant_folder_ids(root_id)
        clear_photos_not_in_folders(valid_ids)
    else:
        clear_all_drive_photos()
    return {"success": True, "message": "Cleared previous event photos."}

@app.get("/api/sync/status", dependencies=[Depends(require_auth)])
async def get_sync_status():
    """Poll live sync progress."""
    return sync_status

@app.post("/api/photos/upload", dependencies=[Depends(require_auth)])
async def upload_photos(files: List[UploadFile] = File(...)):
    """Direct upload of photos from host dashboard."""
    added_count = 0
    total_faces = 0
    messages = []

    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in VALID_EXTENSIONS:
            messages.append(f"Skipped {file.filename}: unsupported file type.")
            continue

        contents = await file.read()
        added, face_count, msg = ingest_uploaded_file(contents, file.filename)
        if added:
            added_count += 1
            total_faces += face_count
        messages.append(msg)

    return {
        "success": True,
        "added": added_count,
        "faces": total_faces,
        "messages": messages,
        "stats": get_stats()
    }

@app.delete("/api/photos/{photo_id}", dependencies=[Depends(require_auth)])
async def remove_photo(photo_id: int):
    """Delete a photo and its indexed faces."""
    filepath = delete_photo(photo_id)
    if filepath:
        full_path = DATA_DIR / filepath
        if full_path.exists():
            try:
                full_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete file {full_path}: {e}")
    return {"success": True}

class UpdateSettingsRequest(BaseModel):
    event_name: Optional[str] = None
    match_threshold: Optional[float] = None
    guest_base_url: Optional[str] = None
    couple_title: Optional[str] = None
    event_date: Optional[str] = None
    event_location: Optional[str] = None
    cover_photo_path: Optional[str] = None

@app.post("/api/settings", dependencies=[Depends(require_auth)])
async def update_settings(payload: UpdateSettingsRequest):
    """Update event configuration."""
    if payload.event_name is not None:
        set_setting("event_name", payload.event_name.strip())
    if payload.match_threshold is not None:
        set_setting("match_threshold", str(payload.match_threshold))
    if payload.guest_base_url is not None:
        url = payload.guest_base_url.strip()
        set_setting("guest_base_url", url)
        if url:
            generate_qr_code(url)
    if payload.couple_title is not None:
        set_setting("couple_title", payload.couple_title.strip())
    if payload.event_date is not None:
        set_setting("event_date", payload.event_date.strip())
    if payload.event_location is not None:
        set_setting("event_location", payload.event_location.strip())
    if payload.cover_photo_path is not None:
        set_setting("cover_photo_path", payload.cover_photo_path.strip())

    return {"success": True, "settings": get_all_settings()}

@app.post("/api/settings/cover-photo", dependencies=[Depends(require_auth)])
async def upload_cover_photo(file: UploadFile = File(...)):
    """Upload a custom cover photo for the guest landing page."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed.")
    
    upload_dir = STATIC_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Save as cover_photo.jpg (or timestamped)
    dest_path = upload_dir / "cover_photo.jpg"
    contents = await file.read()
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 25MB).")
        
    with open(dest_path, "wb") as f:
        f.write(contents)
        
    rel_path = f"/static/uploads/cover_photo.jpg?v={int(time.time())}"
    set_setting("cover_photo_path", rel_path)
    return {"success": True, "cover_photo_path": rel_path}
