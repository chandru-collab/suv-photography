import sqlite3
import json
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from backend.config import DB_PATH, DEFAULT_MATCH_THRESHOLD
from backend.cache import vector_cache, metadata_cache

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # Photos table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        filepath TEXT NOT NULL,
        thumbnail_path TEXT,
        file_hash TEXT UNIQUE NOT NULL,
        source TEXT DEFAULT 'drive',
        file_size INTEGER DEFAULT 0,
        width INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        face_count INTEGER DEFAULT 0,
        drive_file_id TEXT,
        drive_folder_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Non-breaking migration: ensure drive columns exist in photos table
    cursor.execute("PRAGMA table_info(photos)")
    existing_cols = [r["name"] for r in cursor.fetchall()]
    if "drive_file_id" not in existing_cols:
        cursor.execute("ALTER TABLE photos ADD COLUMN drive_file_id TEXT")
    if "drive_folder_id" not in existing_cols:
        cursor.execute("ALTER TABLE photos ADD COLUMN drive_folder_id TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_drive_file_id ON photos(drive_file_id)")

    # Faces table (storing 128-D embedding as 512-byte float32 blob)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS faces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        photo_id INTEGER NOT NULL,
        bbox_x REAL NOT NULL,
        bbox_y REAL NOT NULL,
        bbox_w REAL NOT NULL,
        bbox_h REAL NOT NULL,
        score REAL NOT NULL,
        embedding BLOB NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (photo_id) REFERENCES photos (id) ON DELETE CASCADE
    );
    """)

    # Sync logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sync_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT,
        message TEXT,
        photos_found INTEGER DEFAULT 0,
        photos_added INTEGER DEFAULT 0,
        photos_skipped INTEGER DEFAULT 0,
        faces_detected INTEGER DEFAULT 0,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    );
    """)

    # Default settings
    defaults = {
        "event_name": "Our Event Photos",
        "drive_folder_url": "",
        "match_threshold": str(DEFAULT_MATCH_THRESHOLD),
        "guest_base_url": "",
        "couple_title": "Bride & Groom",
        "event_date": "17 NOV 2026",
        "event_location": "CHENNAI",
        "cover_photo_path": "/static/img/default_wedding_cover_clean.jpg"
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Upgrade old low-precision default threshold to new high-accuracy threshold
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'match_threshold' AND value IN ('0.363', '0.36')", (str(DEFAULT_MATCH_THRESHOLD),))

    conn.commit()
    conn.close()

def get_setting(key: str, default: str = "") -> str:
    cached = metadata_cache.get(f"setting:{key}")
    if cached is not None:
        return cached
    try:
        conn = get_connection()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        conn.close()
        val = row["value"] if row else default
        metadata_cache.set(f"setting:{key}", val)
        return val
    except sqlite3.OperationalError:
        # Auto-initialize database schema if tables do not exist yet
        try:
            init_db()
            conn = get_connection()
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            conn.close()
            val = row["value"] if row else default
            metadata_cache.set(f"setting:{key}", val)
            return val
        except Exception:
            return default
    except Exception:
        return default

def set_setting(key: str, value: str):
    try:
        conn = get_connection()
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()
        conn.close()
        metadata_cache.invalidate()
    except sqlite3.OperationalError:
        init_db()
        conn = get_connection()
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        conn.commit()
        conn.close()
        metadata_cache.invalidate()

def get_all_settings() -> Dict[str, str]:
    try:
        conn = get_connection()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {row["key"]: row["value"] for row in rows}
    except sqlite3.OperationalError:
        try:
            init_db()
            conn = get_connection()
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            conn.close()
            return {row["key"]: row["value"] for row in rows}
        except Exception:
            return {}
    except Exception:
        return {}

def photo_exists_by_hash(file_hash: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT id FROM photos WHERE file_hash = ?", (file_hash,)).fetchone()
    conn.close()
    return row is not None

def photo_exists_by_drive_id(drive_file_id: str) -> bool:
    if not drive_file_id:
        return False
    conn = get_connection()
    row = conn.execute("SELECT id FROM photos WHERE drive_file_id = ?", (drive_file_id,)).fetchone()
    conn.close()
    return row is not None

def get_photo_by_drive_id(drive_file_id: str) -> Optional[Dict[str, Any]]:
    if not drive_file_id:
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM photos WHERE drive_file_id = ?", (drive_file_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def insert_photo(
    filename: str,
    filepath: str,
    thumbnail_path: str,
    file_hash: str,
    source: str,
    file_size: int,
    width: int,
    height: int,
    face_count: int = 0,
    drive_file_id: Optional[str] = None,
    drive_folder_id: Optional[str] = None
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO photos (filename, filepath, thumbnail_path, file_hash, source, file_size, width, height, face_count, drive_file_id, drive_folder_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (filename, filepath, thumbnail_path, file_hash, source, file_size, width, height, face_count, drive_file_id, drive_folder_id))
    photo_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return photo_id

def update_photo_face_count(photo_id: int, face_count: int):
    conn = get_connection()
    conn.execute("UPDATE photos SET face_count = ? WHERE id = ?", (face_count, photo_id))
    conn.commit()
    conn.close()

def update_photo_drive_folder(photo_id: int, folder_id: str, drive_file_id: Optional[str] = None):
    conn = get_connection()
    if drive_file_id:
        conn.execute("UPDATE photos SET drive_folder_id = ?, drive_file_id = ? WHERE id = ?", (folder_id, drive_file_id, photo_id))
    else:
        conn.execute("UPDATE photos SET drive_folder_id = ? WHERE id = ?", (folder_id, photo_id))
    conn.commit()
    conn.close()

def delete_faces_for_photo(photo_id: int):
    """Remove existing face records for a photo prior to high-accuracy re-indexing."""
    conn = get_connection()
    conn.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
    conn.execute("UPDATE photos SET face_count = 0 WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()

def insert_face(photo_id: int, bbox: Tuple[float, float, float, float], score: float, embedding: np.ndarray) -> int:
    # Ensure embedding is 1D float32
    emb_bytes = embedding.astype(np.float32).tobytes()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO faces (photo_id, bbox_x, bbox_y, bbox_w, bbox_h, score, embedding)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (photo_id, float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]), float(score), emb_bytes))
    face_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return face_id

def get_all_face_embeddings(folder_ids: Optional[List[str]] = None) -> Tuple[List[int], List[int], np.ndarray]:
    """
    Load face embeddings for fast vectorized comparison.
    If folder_ids is provided, strictly filters to photos belonging to the active Google Drive event.
    """
    conn = get_connection()
    if folder_ids:
        placeholders = ",".join("?" for _ in folder_ids)
        query = f"""
        SELECT f.id, f.photo_id, f.embedding
        FROM faces f
        JOIN photos p ON f.photo_id = p.id
        WHERE p.drive_folder_id IN ({placeholders}) OR p.source = 'upload'
        """
        rows = conn.execute(query, folder_ids).fetchall()
    else:
        rows = conn.execute("SELECT id, photo_id, embedding FROM faces").fetchall()
    conn.close()

    if not rows:
        return [], [], np.empty((0, 128), dtype=np.float32)

    face_ids = []
    photo_ids = []
    embs = []
    for r in rows:
        face_ids.append(r["id"])
        photo_ids.append(r["photo_id"])
        arr = np.frombuffer(r["embedding"], dtype=np.float32)
        embs.append(arr)

    embeddings_matrix = np.vstack(embs)
    return face_ids, photo_ids, embeddings_matrix

def get_photos_by_ids(photo_ids: List[int], folder_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if not photo_ids:
        return []
    conn = get_connection()
    placeholders = ",".join("?" for _ in photo_ids)
    if folder_ids:
        folder_placeholders = ",".join("?" for _ in folder_ids)
        query = f"""
        SELECT * FROM photos
        WHERE id IN ({placeholders}) AND (drive_folder_id IN ({folder_placeholders}) OR source = 'upload')
        """
        rows = conn.execute(query, (*photo_ids, *folder_ids)).fetchall()
    else:
        rows = conn.execute(f"SELECT * FROM photos WHERE id IN ({placeholders})", photo_ids).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_photos_not_in_folders(active_folder_ids: List[str]):
    """
    Purge photos and face embeddings that belong to old Google Drive links,
    ensuring guest face search ONLY returns photos from the active Drive folder.
    """
    if not active_folder_ids:
        return
    conn = get_connection()
    placeholders = ",".join("?" for _ in active_folder_ids)
    conn.execute(f"""
    DELETE FROM photos
    WHERE source = 'drive' AND (drive_folder_id IS NULL OR drive_folder_id NOT IN ({placeholders}))
    """, active_folder_ids)
    conn.commit()
    conn.close()
    vector_cache.invalidate()
    metadata_cache.invalidate()

def clear_all_drive_photos():
    """Purge all Google Drive photos to start with a fresh event."""
    conn = get_connection()
    conn.execute("DELETE FROM photos WHERE source = 'drive'")
    conn.commit()
    conn.close()
    vector_cache.invalidate()
    metadata_cache.invalidate()

def get_photo_by_id(photo_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_photos(limit: int = 500, offset: int = 0) -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM photos ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stats() -> Dict[str, Any]:
    cached = metadata_cache.get("stats")
    if cached is not None:
        return cached
    conn = get_connection()
    total_photos = conn.execute("SELECT COUNT(*) as c FROM photos").fetchone()["c"]
    total_faces = conn.execute("SELECT COUNT(*) as c FROM faces").fetchone()["c"]
    zero_face_photos = conn.execute("SELECT COUNT(*) as c FROM photos WHERE face_count = 0").fetchone()["c"]
    multi_face_photos = conn.execute("SELECT COUNT(*) as c FROM photos WHERE face_count > 1").fetchone()["c"]
    last_log = conn.execute("SELECT * FROM sync_logs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    res = {
        "total_photos": total_photos,
        "total_faces": total_faces,
        "zero_face_photos": zero_face_photos,
        "multi_face_photos": multi_face_photos,
        "last_sync": dict(last_log) if last_log else None
    }
    metadata_cache.set("stats", res)
    return res

def delete_photo(photo_id: int) -> Optional[str]:
    conn = get_connection()
    photo = conn.execute("SELECT filepath, thumbnail_path FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if photo:
        conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        conn.commit()
    conn.close()
    vector_cache.invalidate()
    metadata_cache.invalidate()
    return photo["filepath"] if photo else None

def start_sync_log(message: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sync_logs (status, message) VALUES ('RUNNING', ?)", (message,))
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id

def finish_sync_log(log_id: int, status: str, message: str, found: int, added: int, skipped: int, faces: int):
    conn = get_connection()
    conn.execute("""
    UPDATE sync_logs
    SET status = ?, message = ?, photos_found = ?, photos_added = ?, photos_skipped = ?, faces_detected = ?, completed_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """, (status, message, found, added, skipped, faces, log_id))
    conn.commit()
    conn.close()
