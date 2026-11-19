import time
import threading
import logging
from typing import List, Tuple, Dict, Any, Optional
import numpy as np

logger = logging.getLogger("cache")

class VectorCache:
    """
    High-throughput in-memory cache for 128-D face embeddings.
    Avoids re-querying SQLite and reconstructing NumPy matrices on every search request.
    Enables sub-millisecond vectorized cosine similarity search under massive concurrent load (1000+ RPS).
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._face_ids: List[int] = []
        self._photo_ids: List[int] = []
        self._embeddings_matrix: np.ndarray = np.empty((0, 128), dtype=np.float32)
        self._last_updated: float = 0.0
        self._is_warm: bool = False

    def get_embeddings(self, folder_ids: Optional[List[str]] = None) -> Tuple[List[int], List[int], np.ndarray]:
        """Return cached embeddings matrix, optionally scoped strictly to specific folder IDs."""
        with self._lock:
            if not self._is_warm:
                self._reload()

            if folder_ids:
                from backend.database import get_photos_by_ids
                valid_photos = get_photos_by_ids(self._photo_ids, folder_ids=folder_ids)
                valid_p_ids = {p["id"] for p in valid_photos}

                indices = [i for i, p_id in enumerate(self._photo_ids) if p_id in valid_p_ids]
                if not indices:
                    return [], [], np.empty((0, 128), dtype=np.float32)

                filtered_face_ids = [self._face_ids[i] for i in indices]
                filtered_photo_ids = [self._photo_ids[i] for i in indices]
                filtered_matrix = self._embeddings_matrix[indices]
                return filtered_face_ids, filtered_photo_ids, filtered_matrix

            return self._face_ids, self._photo_ids, self._embeddings_matrix

    def invalidate(self):
        """Invalidate the cache when new photos are synced, uploaded, or deleted."""
        with self._lock:
            logger.info("Invalidating in-memory face embedding cache...")
            self._reload()

    def _reload(self):
        """Internal load from SQLite into contiguous RAM."""
        from backend.database import get_all_face_embeddings
        try:
            face_ids, photo_ids, embeddings = get_all_face_embeddings()
            self._face_ids = face_ids
            self._photo_ids = photo_ids
            self._embeddings_matrix = embeddings
            self._last_updated = time.time()
            self._is_warm = True
            logger.info(f"VectorCache warmed: {len(face_ids)} faces across {len(set(photo_ids))} photos in RAM.")
        except Exception as e:
            logger.error(f"Error loading vector cache from DB: {e}")

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._face_ids)

    @property
    def is_warm(self) -> bool:
        with self._lock:
            return self._is_warm


class MetadataCache:
    """
    Thread-safe micro-cache for database stats, settings, and frequently accessed metadata.
    Reduces database lock contention under heavy concurrency.
    """
    def __init__(self, ttl_seconds: float = 3.0):
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            ts, val = item
            if time.time() - ts > self._ttl:
                del self._cache[key]
                return None
            return val

    def set(self, key: str, value: Any):
        with self._lock:
            self._cache[key] = (time.time(), value)

    def invalidate(self, key: Optional[str] = None):
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()

# Global singletons
vector_cache = VectorCache()
metadata_cache = MetadataCache(ttl_seconds=3.0)
