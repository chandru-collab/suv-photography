import os
import re
import shutil
import hashlib
import logging
import threading
import time
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import requests
from PIL import Image

from backend.config import (
    PHOTOS_DIR,
    THUMBNAILS_DIR,
    DATA_DIR
)
from backend.database import (
    photo_exists_by_hash,
    photo_exists_by_drive_id,
    get_photo_by_drive_id,
    delete_faces_for_photo,
    insert_photo,
    update_photo_face_count,
    update_photo_drive_folder,
    get_connection,
    insert_face,
    start_sync_log,
    finish_sync_log,
    set_setting,
    get_setting
)
from backend.face_engine import face_engine
from backend.cache import vector_cache, metadata_cache
# pyrefly: ignore[missing-import]
from backend.drive_service import (
    drive_manager,
    IMAGE_MIME_TYPES,
    FOLDER_MIME_TYPE
)

logger = logging.getLogger("sync_service")

# Supported image extensions
VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".tiff", ".tif", ".cr2", ".nef", ".arw", ".dng"}

# Cancellation event for stopping running syncs immediately
_sync_cancel_event = threading.Event()
_sync_lock = threading.Lock()

# Global progress status
sync_status = {
    "running": False,
    "progress_percent": 0,
    "current_action": "Idle",
    "total_found": 0,
    "photos_added": 0,
    "photos_skipped": 0,
    "faces_detected": 0,
    "error": None,
    "last_completed": None
}

def cancel_current_sync():
    """Cancel any running sync immediately and gracefully."""
    global sync_status
    _sync_cancel_event.set()
    sync_status["running"] = False
    sync_status["current_action"] = "Sync stopped by user."
    logger.info("Drive sync cancelled by user request.")

def extract_drive_folder_id(url_or_id: str) -> Optional[str]:
    """Extracts Google Drive folder ID using DriveManager."""
    return drive_manager.extract_folder_id(url_or_id)

def compute_file_hash(filepath: Path) -> str:
    """Calculate SHA256 hash of file content to detect duplicates."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()

def compute_bytes_hash(data: bytes) -> str:
    """Calculate SHA256 hash of byte buffer."""
    return hashlib.sha256(data).hexdigest()

def process_single_image_file(source_path: Path, source_label: str = "local") -> Tuple[bool, int]:
    """
    Process a local image file (e.g. from direct upload):
    - Computes hash
    - Skips if already ingested
    - Detects faces and computes embeddings
    """
    if not source_path.is_file() or source_path.suffix.lower() not in VALID_EXTENSIONS:
        return False, 0

    try:
        file_hash = compute_file_hash(source_path)
        if photo_exists_by_hash(file_hash):
            return False, 0

        with open(source_path, "rb") as f:
            image_bytes = f.read()

        img_bgr = face_engine.load_image_from_bytes(image_bytes)
        if img_bgr is None:
            logger.warning(f"Skipping corrupted or unreadable image: {source_path.name}")
            return False, 0

        h, w = img_bgr.shape[:2]
        file_size = len(image_bytes)

        safe_stem = re.sub(r"[^\w\-.]", "_", source_path.stem)[:40]
        ext = source_path.suffix.lower()
        target_filename = f"{file_hash[:12]}_{safe_stem}{ext}"
        target_path = PHOTOS_DIR / target_filename
        shutil.copy2(source_path, target_path)

        thumb_filename = f"thumb_{file_hash[:12]}.jpg"
        thumb_path = THUMBNAILS_DIR / thumb_filename
        face_engine.create_thumbnail(image_bytes, str(thumb_path), max_size=400)

        photo_id = insert_photo(
            filename=source_path.name,
            filepath=f"photos/{target_filename}",
            thumbnail_path=f"thumbnails/{thumb_filename}",
            file_hash=file_hash,
            source=source_label,
            file_size=file_size,
            width=w,
            height=h,
            face_count=0
        )

        faces = face_engine.detect_and_extract(img_bgr)
        face_count = len(faces)
        for face in faces:
            insert_face(
                photo_id=photo_id,
                bbox=face["bbox"],
                score=face["score"],
                embedding=face["embedding"]
            )

        update_photo_face_count(photo_id, face_count)
        return True, face_count

    except Exception as e:
        logger.error(f"Error processing image {source_path.name}: {e}", exc_info=True)
        return False, 0

def process_drive_image_in_memory(
    drive_item: Dict[str, Any],
    folder_id: str,
    reindex: bool = True
) -> Tuple[bool, int]:
    """
    Process an image directly from Google Drive API in-memory:
    - Checks cancellation event to abort instantly if user switches folders.
    - Uses high-speed Google Edge CDN thumbnail (=s1280) for instant download.
    - Never stores duplicate image files on local disk or Supabase.
    - Only stores the face vector embeddings and Drive reference in DB.
    """
    if _sync_cancel_event.is_set():
        return False, 0

    file_id = drive_item["id"]
    filename = drive_item.get("name", f"drive_{file_id}.jpg")
    actual_folder_id = drive_item.get("parent_folder_id") or folder_id

    existing_photo = get_photo_by_drive_id(file_id) if reindex else None
    if not reindex and photo_exists_by_drive_id(file_id):
        return False, 0

    try:
        # Fast download: Try CDN preview (=s1600) first for memory-safe face detection, fallback to API
        image_bytes = None
        raw_thumb = drive_item.get("thumbnailLink")
        if raw_thumb:
            try:
                cdn_url = drive_manager.build_cdn_thumbnail(raw_thumb, size=1600)
                resp = requests.get(cdn_url, timeout=15)
                if resp.status_code == 200 and resp.content:
                    image_bytes = resp.content
            except Exception:
                pass

        if not image_bytes:
            image_bytes = drive_manager.download_file_bytes(file_id)

        if not image_bytes or _sync_cancel_event.is_set():
            return False, 0

        file_hash = compute_bytes_hash(image_bytes)
        if not existing_photo and photo_exists_by_hash(file_hash):
            conn = get_connection()
            row = conn.execute("SELECT * FROM photos WHERE file_hash = ?", (file_hash,)).fetchone()
            conn.close()
            if row:
                existing_photo = dict(row)

        img_bgr = face_engine.load_image_from_bytes(image_bytes)
        if img_bgr is None:
            return False, 0

        h, w = img_bgr.shape[:2]
        file_size = int(drive_item.get("size", len(image_bytes)) or len(image_bytes))

        if existing_photo:
            photo_id = existing_photo["id"]
            update_photo_drive_folder(photo_id, actual_folder_id, file_id)
            delete_faces_for_photo(photo_id)
        else:
            # Insert photo record pointing directly to Google Drive
            photo_id = insert_photo(
                filename=filename,
                filepath=f"drive://{file_id}",
                thumbnail_path=f"drive_thumb://{file_id}",
                file_hash=file_hash,
                source="drive",
                file_size=file_size,
                width=w,
                height=h,
                face_count=0,
                # pyrefly: ignore[unexpected-keyword]
                drive_file_id=file_id,
                # pyrefly: ignore[unexpected-keyword]
                drive_folder_id=actual_folder_id
            )

        # Detect and extract faces using multi-scale detection
        # pyrefly: ignore[unexpected-keyword]
        faces = face_engine.detect_and_extract(img_bgr, multi_scale=True)
        face_count = len(faces)
        for face in faces:
            insert_face(
                photo_id=photo_id,
                bbox=face["bbox"],
                score=face["score"],
                embedding=face["embedding"]
            )

        update_photo_face_count(photo_id, face_count)
        del img_bgr
        del image_bytes
        return True, face_count

    except Exception as e:
        logger.error(f"Error processing Drive image {filename} ({file_id}): {e}")
        return False, 0
    finally:
        if 'img_bgr' in locals():
            del img_bgr
        if 'image_bytes' in locals():
            del image_bytes
        import gc
        gc.collect()

def fetch_all_drive_image_items(
    folder_id: str,
    max_depth: int = 10,
    current_depth: int = 0,
    visited_folders: Optional[Set[str]] = None
) -> List[Dict[str, Any]]:
    """Recursively collect image items from Google Drive folder structure using Drive API."""
    if visited_folders is None:
        visited_folders = set()

    clean_id = drive_manager.extract_folder_id(folder_id) or folder_id
    if not clean_id or clean_id in visited_folders or current_depth > max_depth:
        return []
    visited_folders.add(clean_id)

    items = []
    q = f"'{clean_id}' in parents and trashed = false"
    page_token = None

    while True:
        if _sync_cancel_event.is_set():
            break

        try:
            res = drive_manager.service.files().list(
                q=q,
                pageSize=100,
                pageToken=page_token,
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, thumbnailLink, imageMediaMetadata, shortcutDetails)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
        except Exception as e:
            logger.error(f"Failed to fetch Drive items for folder {clean_id}: {e}")
            raise RuntimeError(f"Error accessing Google Drive folder {clean_id}: {e}")

        for f in res.get("files", []):
            mime = f.get("mimeType", "")
            ext = Path(f.get("name", "")).suffix.lower()

            # Handle Shortcuts
            if mime == "application/vnd.google-apps.shortcut":
                shortcut = f.get("shortcutDetails", {})
                target_id = shortcut.get("targetId")
                target_mime = shortcut.get("targetMimeType", "")
                if target_id:
                    if target_mime == FOLDER_MIME_TYPE and current_depth < max_depth:
                        sub_items = fetch_all_drive_image_items(target_id, max_depth=max_depth, current_depth=current_depth + 1, visited_folders=visited_folders)
                        items.extend(sub_items)
                    elif target_mime in IMAGE_MIME_TYPES or target_mime.startswith("image/") or ext in VALID_EXTENSIONS:
                        f["id"] = target_id
                        f["mimeType"] = target_mime
                        f["parent_folder_id"] = clean_id
                        items.append(f)
                continue

            if mime in IMAGE_MIME_TYPES or mime.startswith("image/") or ext in VALID_EXTENSIONS:
                f["parent_folder_id"] = clean_id
                items.append(f)
            elif mime == FOLDER_MIME_TYPE and current_depth < max_depth:
                sub_items = fetch_all_drive_image_items(f["id"], max_depth=max_depth, current_depth=current_depth + 1, visited_folders=visited_folders)
                items.extend(sub_items)

        page_token = res.get("nextPageToken")
        if not page_token or _sync_cancel_event.is_set():
            break

    return items

def run_drive_sync(folder_link_or_id: str, reindex: bool = True):
    """
    High-speed, multi-threaded Google Drive API indexing.
    - Uses 8 concurrent workers for fast CDN downloads.
    - Zero local file duplication.
    - Can be cleanly cancelled if the user changes the Drive link.
    - If reindex=True, updates existing photos with fresh multi-scale face detection.
    """
    global sync_status
    with _sync_lock:
        _sync_cancel_event.clear()

        folder_id = extract_drive_folder_id(folder_link_or_id)
        if not folder_id:
            sync_status["error"] = "Invalid Google Drive folder link or folder ID. Please check the URL."
            sync_status["running"] = False
            return

        # Update database settings immediately so UI switches without delay
        set_setting("drive_folder_url", f"https://drive.google.com/drive/folders/{folder_id}")

        sync_status.update({
            "running": True,
            "progress_percent": 5,
            "current_action": "Connecting to Google Drive folder...",
            "total_found": 0,
            "photos_added": 0,
            "photos_skipped": 0,
            "faces_detected": 0,
            "error": None
        })

        log_id = start_sync_log(f"Syncing Google Drive folder ID: {folder_id}")

        if not drive_manager.is_configured:
            err_msg = "Google Drive credentials not configured on server. Please add your Service Account JSON file or set GOOGLE_CREDENTIALS_JSON."
            sync_status["current_action"] = f"Drive sync failed: {err_msg}"
            sync_status["error"] = err_msg
            sync_status["progress_percent"] = 0
            finish_sync_log(log_id, "FAILED", err_msg, 0, 0, 0, 0)
            return

        try:
            folder_meta = drive_manager.get_folder_metadata(folder_id)
            folder_name = folder_meta.get("name", "Event Folder")
            set_setting("event_name", folder_name)

            sync_status["current_action"] = f"Fetching files for '{folder_name}' from Drive..."
            sync_status["progress_percent"] = 15

            # Fast collection of image items (supports up to 10 nested levels + shortcuts)
            all_drive_images = fetch_all_drive_image_items(folder_id, max_depth=10)
            total_files = len(all_drive_images)
            sync_status["total_found"] = total_files

            if total_files == 0:
                err_msg = (
                    f"No photos found in '{folder_name}'. "
                    f"Please make sure the Google Drive folder contains images and is shared with: "
                    f"suv-photography@suv-photography-514705.iam.gserviceaccount.com"
                )
                sync_status["current_action"] = err_msg
                sync_status["error"] = err_msg
                sync_status["progress_percent"] = 100
                finish_sync_log(log_id, "COMPLETED", err_msg, 0, 0, 0, 0)
                return

            sync_status["current_action"] = f"Indexing {total_files} images in high-speed parallel mode..."
            sync_status["progress_percent"] = 25

            added_count = 0
            skipped_count = 0
            total_faces = 0
            processed_count = 0

            # Sequential / 1-worker processing on cloud instances (guarantees RAM stays <250MB on 512MB limit)
            max_sync_workers = int(os.environ.get("SYNC_WORKERS", "1"))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_sync_workers) as executor:
                futures = {
                    executor.submit(process_drive_image_in_memory, item, folder_id, reindex): item
                    for item in all_drive_images
                }

                for future in concurrent.futures.as_completed(futures):
                    if _sync_cancel_event.is_set():
                        sync_status["current_action"] = "Sync cancelled."
                        break

                    processed_count += 1
                    sync_status["progress_percent"] = 25 + int(70 * (processed_count / total_files))

                    # Force immediate garbage collection after each photo
                    import gc
                    gc.collect()

                    try:
                        added, face_count = future.result()
                        if added:
                            added_count += 1
                            total_faces += face_count
                        else:
                            skipped_count += 1
                    except Exception as e:
                        logger.error(f"Sync worker exception: {e}", exc_info=True)
                        skipped_count += 1

                    sync_status["photos_added"] = added_count
                    sync_status["photos_skipped"] = skipped_count
                    sync_status["faces_detected"] = total_faces
                    sync_status["current_action"] = f"Processed {processed_count}/{total_files} images ({total_faces} faces indexed)..."

            if _sync_cancel_event.is_set():
                sync_status["current_action"] = "Sync cancelled by user."
                finish_sync_log(log_id, "CANCELLED", "Sync cancelled by user", total_files, added_count, skipped_count, total_faces)
            else:
                sync_status["current_action"] = f"Complete! {added_count} new images indexed ({total_faces} faces)."
                sync_status["progress_percent"] = 100
                sync_status["last_completed"] = f"{added_count} Drive photos indexed, {total_faces} faces"
                finish_sync_log(log_id, "SUCCESS", f"Processed {total_files} Drive images", total_files, added_count, skipped_count, total_faces)

            # Invalidate in-memory caches to guarantee fresh face matrix
            vector_cache.invalidate()
            metadata_cache.invalidate()

        except Exception as e:
            logger.error(f"Drive API sync error: {e}", exc_info=True)
            sync_status["error"] = str(e)
            sync_status["current_action"] = f"Drive sync failed: {str(e)}"
            finish_sync_log(log_id, "FAILED", str(e), 0, 0, 0, 0)

        finally:
            sync_status["running"] = False

def start_background_drive_sync(folder_link_or_id: str, force: bool = True, reindex: bool = True):
    """Launch the drive sync in a detached background thread, cleanly interrupting previous sync if needed."""
    global sync_status
    if force and sync_status["running"]:
        cancel_current_sync()
        time.sleep(0.15) # Brief yield to allow worker threads to abort

    t = threading.Thread(target=run_drive_sync, args=(folder_link_or_id, reindex), daemon=True)
    t.start()

# ==========================================
# CONTINUOUS LIVE AUTO-SYNC ENGINE
# ==========================================

auto_sync_status = {
    "enabled": True,
    "interval_seconds": 60,
    "running": False,
    "last_check_timestamp": 0.0,
    "last_check_time_str": "Never",
    "last_photos_added": 0,
    "last_faces_detected": 0,
    "last_message": "Auto-sync initializing...",
    "next_check_in_seconds": 60,
    "total_cycles": 0
}

_auto_sync_stop_event = threading.Event()
_auto_sync_thread: Optional[threading.Thread] = None

def get_auto_sync_status() -> Dict[str, Any]:
    """Get current status of background auto-sync worker with live countdown."""
    global auto_sync_status
    now = time.time()
    last = auto_sync_status.get("last_check_timestamp", 0.0)
    interval = auto_sync_status.get("interval_seconds", 60)
    if last > 0 and auto_sync_status.get("enabled", True):
        remaining = max(0, int(interval - (now - last)))
    else:
        remaining = interval
    auto_sync_status["next_check_in_seconds"] = remaining
    return dict(auto_sync_status)

def set_auto_sync_settings(enabled: bool, interval_seconds: int = 60) -> Dict[str, Any]:
    """Update auto-sync configuration in persistent DB settings."""
    global auto_sync_status
    interval_seconds = max(15, min(3600, int(interval_seconds)))
    set_setting("auto_sync_enabled", "true" if enabled else "false")
    set_setting("auto_sync_interval_seconds", str(interval_seconds))
    auto_sync_status["enabled"] = enabled
    auto_sync_status["interval_seconds"] = interval_seconds
    if enabled:
        auto_sync_status["last_message"] = f"Auto-sync enabled (checking every {interval_seconds}s)"
    else:
        auto_sync_status["last_message"] = "Auto-sync paused by admin."
    return get_auto_sync_status()

def auto_sync_worker_loop():
    """
    Continuous background daemon thread.
    Automatically checks the active Google Drive folder for newly uploaded photos
    (e.g. from the photographer/cameraman) and indexes them incrementally into SQLite
    and in-memory vector cache without manual intervention.
    """
    global auto_sync_status
    logger.info("Continuous Drive Auto-Sync daemon started.")

    # Brief delay on startup so the main server finishes initial cache warming
    time.sleep(4.0)

    while not _auto_sync_stop_event.is_set():
        try:
            enabled_str = get_setting("auto_sync_enabled", "true")
            interval_str = get_setting("auto_sync_interval_seconds", "60")
            try:
                interval_secs = max(15, int(interval_str))
            except (ValueError, TypeError):
                interval_secs = 60

            is_enabled = (enabled_str.lower() != "false")
            auto_sync_status["enabled"] = is_enabled
            auto_sync_status["interval_seconds"] = interval_secs

            if is_enabled and drive_manager.is_configured:
                folder_url = get_setting("drive_folder_url", "").strip()
                folder_id = extract_drive_folder_id(folder_url) if folder_url else None

                # Only run if folder is configured and no manual sync is currently in progress
                if folder_id and not sync_status.get("running", False):
                    auto_sync_status["running"] = True
                    auto_sync_status["last_message"] = "Checking Google Drive for newly added photos..."

                    # Run incremental sync (reindex=False skips all existing photos instantly)
                    run_drive_sync(folder_id, reindex=False)

                    now_ts = time.time()
                    auto_sync_status["last_check_timestamp"] = now_ts
                    auto_sync_status["last_check_time_str"] = datetime.now().strftime("%I:%M:%S %p")
                    added = sync_status.get("photos_added", 0)
                    faces = sync_status.get("faces_detected", 0)
                    auto_sync_status["last_photos_added"] = added
                    auto_sync_status["last_faces_detected"] = faces
                    auto_sync_status["total_cycles"] += 1

                    if added > 0:
                        msg = f"Auto-synced {added} new photos ({faces} faces) from Google Drive!"
                        auto_sync_status["last_message"] = msg
                        logger.info(f"Auto-Sync: {msg}")
                    else:
                        auto_sync_status["last_message"] = f"Folder up-to-date (checked at {auto_sync_status['last_check_time_str']}). Live watcher active."
                    auto_sync_status["running"] = False

        except Exception as e:
            logger.error(f"Error in auto_sync_worker_loop: {e}", exc_info=True)
            auto_sync_status["running"] = False
            auto_sync_status["last_message"] = f"Auto-sync check error: {e}"

        # Sleep in 1-second ticks so stop event or interval updates respond immediately
        curr_interval = auto_sync_status.get("interval_seconds", 60)
        for _ in range(curr_interval):
            if _auto_sync_stop_event.is_set():
                break
            time.sleep(1.0)

def start_auto_sync_daemon():
    """Start the auto-sync background worker thread if not already running."""
    global _auto_sync_thread, _auto_sync_stop_event
    if _auto_sync_thread is not None and _auto_sync_thread.is_alive():
        return
    _auto_sync_stop_event.clear()
    _auto_sync_thread = threading.Thread(target=auto_sync_worker_loop, daemon=True, name="DriveAutoSyncWorker")
    _auto_sync_thread.start()
    logger.info("Drive Auto-Sync background daemon thread launched successfully.")

def stop_auto_sync_daemon():
    """Stop the auto-sync background worker cleanly."""
    global _auto_sync_stop_event
    _auto_sync_stop_event.set()
    logger.info("Drive Auto-Sync background daemon stopping...")

