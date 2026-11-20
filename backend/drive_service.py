import os
import re
import io
import json
import time
import logging
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from functools import lru_cache

# pyrefly: ignore[missing-import]
from google.oauth2 import service_account
# pyrefly: ignore[missing-import]
from google.auth.transport.requests import Request as GoogleAuthRequest
# pyrefly: ignore[missing-import]
from googleapiclient.discovery import build
# pyrefly: ignore[missing-import]
from googleapiclient.http import MediaIoBaseDownload
# pyrefly: ignore[missing-import]
from googleapiclient.errors import HttpError

from backend.config import BASE_DIR, DATA_DIR

logger = logging.getLogger("drive_service")

# Google Drive scopes
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Supported media mime types
IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/heic",
    "image/heif"
}

VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-msvideo",
    "video/mpeg",
    "video/3gpp"
}

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

def find_credentials_file() -> Optional[Path]:
    """Locate the Google Cloud Service Account JSON credentials file."""
    # 0. Check raw JSON string or base64 in environment variable (Render / Cloud deployment)
    raw_json = (os.environ.get("GOOGLE_CREDENTIALS_JSON") or "").strip()
    if raw_json:
        # Strip surrounding quotes if pasted with outer quotes
        if (raw_json.startswith("'") and raw_json.endswith("'")) or (raw_json.startswith('"') and raw_json.endswith('"')):
            raw_json = raw_json[1:-1].strip()

        # Check if base64 encoded
        if not raw_json.startswith("{"):
            try:
                import base64
                decoded = base64.b64decode(raw_json).decode("utf-8")
                if decoded.strip().startswith("{"):
                    raw_json = decoded.strip()
            except Exception:
                pass

        for target in [Path("/tmp/google_credentials.json"), DATA_DIR / "google_credentials.json"]:
            try:
                data = json.loads(raw_json)
                if data.get("type") == "service_account":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(raw_json, encoding="utf-8")
                    return target
            except Exception as e:
                logger.warning(f"Error saving GOOGLE_CREDENTIALS_JSON to {target}: {e}")

    # 1. Environment variable path
    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path and Path(env_path).exists():
        return Path(env_path)

    # 1.5. Cached credentials in tmp or data directory
    for cached_p in [Path("/tmp/google_credentials.json"), DATA_DIR / "google_credentials.json"]:
        if cached_p.exists():
            try:
                data = json.loads(cached_p.read_text(encoding="utf-8"))
                if data.get("type") == "service_account" and "private_key" in data:
                    return cached_p
            except Exception:
                pass

    # 1.6 Check SQLite settings database
    try:
        from backend.database import get_setting
        db_json = (get_setting("google_credentials_json", "") or "").strip()
        if db_json:
            data = json.loads(db_json)
            if data.get("type") == "service_account" and "private_key" in data:
                tgt = DATA_DIR / "google_credentials.json"
                tgt.parent.mkdir(parents=True, exist_ok=True)
                tgt.write_text(db_json, encoding="utf-8")
                return tgt
    except Exception:
        pass

    # 2. Render secret file locations
    for secret_path in [Path("/etc/secrets/google-credentials.json"), Path("/etc/secrets/suv-photography-514705-7a2bae36b793.json")]:
        if secret_path.exists():
            return secret_path

    # 3. Preferred specific credentials file
    preferred = BASE_DIR / "suv-photography-514705-7a2bae36b793.json"
    if preferred.exists():
        return preferred

    # 4. Any service account JSON in BASE_DIR or DATA_DIR
    for d in [BASE_DIR, DATA_DIR]:
        for json_file in d.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("type") == "service_account" and "private_key" in data:
                        return json_file
            except Exception:
                continue

    return None

class GoogleDriveManager:
    """Manages secure communication with Google Drive API v3 using Service Account credentials."""

    def __init__(self):
        self._service = None
        self._credentials_path = None
        self._local = threading.local()
        self._cached_folder_trees: Dict[str, Any] = {}
        self._initialize_service()

    def _initialize_service(self):
        creds_file = find_credentials_file()
        if not creds_file:
            logger.warning("No Google Cloud Service Account credentials file found.")
            return

        try:
            self._credentials_path = creds_file
            creds = service_account.Credentials.from_service_account_file(
                str(creds_file),
                scopes=SCOPES
            )
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
            logger.info(f"Google Drive service initialized successfully using credentials from {creds_file.name}")
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive service: {e}", exc_info=True)
            self._service = None

    @property
    def is_configured(self) -> bool:
        return self._service is not None

    @property
    def service(self):
        # Thread-local service instance to ensure httplib2 SSL socket safety across concurrent threads
        if not hasattr(self._local, "service") or self._local.service is None:
            if self._credentials_path and self._credentials_path.exists():
                try:
                    creds = service_account.Credentials.from_service_account_file(
                        str(self._credentials_path),
                        scopes=SCOPES
                    )
                    self._local.service = build("drive", "v3", credentials=creds, cache_discovery=False)
                except Exception as e:
                    logger.warning(f"Error creating thread-local Drive service: {e}")
                    if self._service is not None:
                        return self._service
                    raise
            elif self._service is not None:
                return self._service
            else:
                self._initialize_service()

        svc = getattr(self._local, "service", self._service)
        if svc is None:
            raise RuntimeError(
                "Google Drive service is not configured. "
                "Please ensure your Service Account JSON credentials file is present or set 'GOOGLE_CREDENTIALS_JSON'."
            )
        return svc

    def get_access_token(self) -> Optional[str]:
        """Obtain a valid OAuth2 access token for direct authenticated video and media streaming."""
        if not hasattr(self._local, "token_creds") or self._local.token_creds is None:
            if self._credentials_path and self._credentials_path.exists():
                try:
                    self._local.token_creds = service_account.Credentials.from_service_account_file(
                        str(self._credentials_path),
                        scopes=SCOPES
                    )
                except Exception as e:
                    logger.error(f"Failed to create token credentials: {e}")
                    return None
            else:
                return None

        creds = self._local.token_creds
        if not creds.valid:
            try:
                creds.refresh(GoogleAuthRequest())
            except Exception as e:
                logger.error(f"Failed to refresh Drive token: {e}")
                return None
        return creds.token

    @staticmethod
    def extract_folder_id(url_or_id: str) -> Optional[str]:
        """Extract Google Drive folder ID from link or raw alphanumeric string."""
        if not url_or_id:
            return None
        text = str(url_or_id).strip()

        m1 = re.search(r"folders/([a-zA-Z0-9_\-]+)", text)
        if m1:
            return m1.group(1)

        m_d = re.search(r"/(?:file/)?d/([a-zA-Z0-9_\-]+)", text)
        if m_d:
            return m_d.group(1)

        m2 = re.search(r"[?&]id=([a-zA-Z0-9_\-]+)", text)
        if m2:
            return m2.group(1)

        if re.fullmatch(r"[a-zA-Z0-9_\-]{20,}", text):
            return text

        return None

    def get_folder_metadata(self, folder_id: str) -> Dict[str, Any]:
        """Retrieve folder details like name, id, parents."""
        try:
            return self.service.files().get(
                fileId=folder_id,
                fields="id, name, mimeType, parents, modifiedTime, webViewLink",
                supportsAllDrives=True
            ).execute()
        except HttpError as e:
            logger.error(f"Error fetching folder metadata for {folder_id}: {e}")
            raise

    def get_descendant_folder_ids(self, root_folder_id: str, max_depth: int = 3, ttl: float = 300.0) -> List[str]:
        """
        Recursively discover all child and descendant folder IDs for an event folder.
        Uses in-memory cache with 5-minute TTL to ensure search executes in milliseconds.
        """
        now = time.time()
        cache_key = f"{root_folder_id}_{max_depth}"
        if not hasattr(self, "_descendants_cache"):
            self._descendants_cache = {}

        if cache_key in self._descendants_cache:
            ts, cached_ids = self._descendants_cache[cache_key]
            if now - ts < ttl:
                return cached_ids

        result_ids = {root_folder_id}
        queue = [(root_folder_id, 0)]

        while queue:
            curr_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue

            try:
                q = f"'{curr_id}' in parents and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
                res = self.service.files().list(
                    q=q,
                    pageSize=100,
                    fields="files(id)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True
                ).execute()

                for sub in res.get("files", []):
                    sub_id = sub["id"]
                    if sub_id not in result_ids:
                        result_ids.add(sub_id)
                        queue.append((sub_id, depth + 1))
            except Exception as e:
                logger.warning(f"Error fetching subfolders for {curr_id}: {e}")

        final_list = list(result_ids)
        self._descendants_cache[cache_key] = (now, final_list)
        return final_list

    def invalidate_descendants_cache(self):
        """Invalidate cached folder IDs when an event or folder is changed."""
        self._descendants_cache = {}

    def get_folder_breadcrumbs(self, folder_id: str, stop_at_folder_id: Optional[str] = None) -> List[Dict[str, str]]:
        """Construct ancestor breadcrumb path for navigation."""
        breadcrumbs = []
        curr_id = folder_id
        visited = set()

        while curr_id and curr_id not in visited and curr_id != "root":
            visited.add(curr_id)
            try:
                info = self.service.files().get(
                    fileId=curr_id,
                    fields="id, name, parents",
                    supportsAllDrives=True
                ).execute()
                breadcrumbs.insert(0, {"id": info["id"], "name": info.get("name", "Folder")})
                if stop_at_folder_id and curr_id == stop_at_folder_id:
                    break
                parents = info.get("parents")
                curr_id = parents[0] if parents else None
            except Exception:
                break

        return breadcrumbs

    def list_folder_structure(self, root_folder_id: str, max_depth: int = 3, current_depth: int = 0) -> Dict[str, Any]:
        """
        Recursively map the folder hierarchy to organize event-wise and client-wise collections.
        Returns a tree structure:
        {
            "id": root_folder_id,
            "name": "TECHATHONX 2K26",
            "children": [
                {"id": "...", "name": "Client A", "children": [...]},
                ...
            ]
        }
        """
        try:
            root_meta = self.get_folder_metadata(root_folder_id)
            node = {
                "id": root_meta["id"],
                "name": root_meta.get("name", "Root Folder"),
                "children": []
            }

            if current_depth >= max_depth:
                return node

            q = f"'{root_folder_id}' in parents and mimeType = '{FOLDER_MIME_TYPE}' and trashed = false"
            res = self.service.files().list(
                q=q,
                pageSize=100,
                orderBy="name",
                fields="files(id, name, mimeType, modifiedTime)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()

            for sub in res.get("files", []):
                child_node = self.list_folder_structure(
                    sub["id"],
                    max_depth=max_depth,
                    current_depth=current_depth + 1
                )
                node["children"].append(child_node)

            return node
        except Exception as e:
            logger.error(f"Error building folder structure for {root_folder_id}: {e}")
            return {"id": root_folder_id, "name": "Folder", "children": [], "error": str(e)}

    def list_gallery_contents(
        self,
        folder_id: str,
        page_size: int = 150,
        page_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch media and subfolders directly from Google Drive for gallery viewing.
        Zero downloading to local disk or Supabase.
        """
        try:
            folder_info = self.get_folder_metadata(folder_id)
        except Exception as e:
            logger.error(f"Folder not found or accessible: {folder_id}: {e}")
            return {
                "success": False,
                "error": f"Could not access Google Drive folder ({folder_id}). Please ensure it is shared with the service account."
            }

        q = f"'{folder_id}' in parents and trashed = false"
        fields = (
            "nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime, "
            "thumbnailLink, webViewLink, webContentLink, imageMediaMetadata, videoMediaMetadata)"
        )

        try:
            response = self.service.files().list(
                q=q,
                pageSize=page_size,
                pageToken=page_token,
                orderBy="folder, name",
                fields=fields,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
        except Exception as e:
            logger.error(f"Failed to list files for folder {folder_id}: {e}")
            return {"success": False, "error": str(e)}

        items = response.get("files", [])
        next_token = response.get("nextPageToken")

        subfolders = []
        photos = []
        videos = []

        for item in items:
            mime = item.get("mimeType", "")
            file_id = item["id"]
            name = item.get("name", "Untitled")
            raw_thumb = item.get("thumbnailLink")

            ext = Path(name).suffix.lower()
            is_image = mime in IMAGE_MIME_TYPES or mime.startswith("image/") or ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heic"}
            is_video = mime in VIDEO_MIME_TYPES or mime.startswith("video/") or ext in {".mp4", ".mov", ".avi", ".webm", ".mkv", ".3gp"}

            if mime == FOLDER_MIME_TYPE:
                subfolders.append({
                    "id": file_id,
                    "name": name,
                    "mimeType": mime,
                    "modifiedTime": item.get("modifiedTime")
                })
            elif is_image:
                img_meta = item.get("imageMediaMetadata", {}) or {}
                # Build optimized thumbnail and high-res preview URLs
                # Note: raw_thumb format is usually ...=s220
                thumb_url = f"/api/drive/thumbnail/{file_id}?size=400"
                preview_url = f"/api/drive/media/{file_id}"
                cdn_thumb = self.build_cdn_thumbnail(thumb_link=raw_thumb, file_id=file_id, size=400)
                cdn_preview = self.build_cdn_thumbnail(thumb_link=raw_thumb, file_id=file_id, size=1600)

                photos.append({
                    "id": file_id,
                    "name": name,
                    "mimeType": mime,
                    "size": int(item.get("size", 0) or 0),
                    "createdTime": item.get("createdTime"),
                    "modifiedTime": item.get("modifiedTime"),
                    "thumbnail_url": thumb_url,
                    "preview_url": preview_url,
                    "cdn_thumb": cdn_thumb or thumb_url,
                    "cdn_preview": cdn_preview or preview_url,
                    "webViewLink": item.get("webViewLink"),
                    "webContentLink": item.get("webContentLink"),
                    "width": img_meta.get("width"),
                    "height": img_meta.get("height")
                })
            elif is_video:
                vid_meta = item.get("videoMediaMetadata", {}) or {}
                thumb_url = f"/api/drive/thumbnail/{file_id}?size=600"
                cdn_thumb = self.build_cdn_thumbnail(thumb_link=raw_thumb, file_id=file_id, size=600)
                preview_url = f"/api/drive/media/{file_id}"

                videos.append({
                    "id": file_id,
                    "name": name,
                    "mimeType": mime,
                    "size": int(item.get("size", 0) or 0),
                    "createdTime": item.get("createdTime"),
                    "modifiedTime": item.get("modifiedTime"),
                    "thumbnail_url": thumb_url,
                    "preview_url": preview_url,
                    "cdn_thumb": cdn_thumb or thumb_url,
                    "webViewLink": item.get("webViewLink"),
                    "previewEmbed": f"https://drive.google.com/file/d/{file_id}/preview",
                    "durationMillis": vid_meta.get("durationMillis"),
                    "width": vid_meta.get("width"),
                    "height": vid_meta.get("height")
                })

        breadcrumbs = self.get_folder_breadcrumbs(folder_id)
        parents = folder_info.get("parents", [])
        parent_id = parents[0] if parents else None

        return {
            "success": True,
            "folder": {
                "id": folder_id,
                "name": folder_info.get("name", "Folder"),
                "webViewLink": folder_info.get("webViewLink"),
                "parent_id": parent_id
            },
            "parent_id": parent_id,
            "breadcrumbs": breadcrumbs,
            "subfolders": subfolders,
            "photos": photos,
            "videos": videos,
            "total_items": len(photos) + len(videos),
            "next_page_token": next_token
        }

    @staticmethod
    def build_cdn_thumbnail(thumb_link: Optional[str] = None, file_id: Optional[str] = None, size: int = 400) -> str:
        """Convert Google Drive thumbnail link or file ID to desired size parameter for instant CDN loading."""
        if thumb_link:
            if "=" in thumb_link:
                base = thumb_link.rsplit("=", 1)[0]
                return f"{base}=s{size}"
            return f"{thumb_link}=s{size}"
        if file_id:
            return f"https://lh3.googleusercontent.com/d/{file_id}=s{size}"
        return ""

    def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Get file metadata including size, mimeType, name."""
        return self.service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, size, modifiedTime, thumbnailLink, webViewLink, webContentLink",
            supportsAllDrives=True
        ).execute()

    def download_file_bytes(self, file_id: str) -> bytes:
        """Download file content into an in-memory byte buffer (never writing to disk or Supabase)."""
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024 * 2) # 2MB chunks
        done = False
        while not done:
            status, done = downloader.next_chunk()
        return fh.getvalue()

    def stream_file_chunks(self, file_id: str, chunk_size: int = 1024 * 512):
        """Generator to stream file chunks directly to FastAPI StreamingResponse."""
        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request, chunksize=chunk_size)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            fh.seek(0)
            yield fh.read()
            fh.seek(0)
            fh.truncate(0)

drive_manager = GoogleDriveManager()
