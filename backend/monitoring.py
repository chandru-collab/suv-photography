import time
import os
import shutil
import threading
from typing import Dict, Any
from fastapi import Request, Response
from fastapi.responses import PlainTextResponse, JSONResponse
from backend.config import DB_PATH, MODELS_DIR, YUNET_MODEL_PATH, SFACE_MODEL_PATH, DATA_DIR
from backend.cache import vector_cache

class MetricsCollector:
    """
    Prometheus-compatible in-memory metrics collector.
    Exposes metrics for Prometheus, Grafana, Datadog, or cloud monitoring.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.requests_total: Dict[str, int] = {}
        self.request_duration_sum: Dict[str, float] = {}
        self.request_duration_count: Dict[str, int] = {}
        self.active_requests: int = 0
        self.face_search_count: int = 0
        self.face_search_duration_sum: float = 0.0

    def record_request(self, method: str, endpoint: str, status_code: int, duration_sec: float):
        key = f'{method}:{endpoint}:{status_code}'
        with self._lock:
            self.requests_total[key] = self.requests_total.get(key, 0) + 1
            dur_key = f'{method}:{endpoint}'
            self.request_duration_sum[dur_key] = self.request_duration_sum.get(dur_key, 0.0) + duration_sec
            self.request_duration_count[dur_key] = self.request_duration_count.get(dur_key, 0) + 1

    def record_face_search(self, duration_sec: float):
        with self._lock:
            self.face_search_count += 1
            self.face_search_duration_sum += duration_sec

    def generate_prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP http_requests_total Total number of HTTP requests processed",
                "# TYPE http_requests_total counter"
            ]
            for key, count in sorted(self.requests_total.items()):
                method, endpoint, status_code = key.split(":")
                lines.append(f'http_requests_total{{method="{method}",endpoint="{endpoint}",status="{status_code}"}} {count}')

            lines.append("")
            lines.append("# HELP http_request_duration_seconds Total request latency in seconds")
            lines.append("# TYPE http_request_duration_seconds summary")
            for dur_key, sum_val in sorted(self.request_duration_sum.items()):
                method, endpoint = dur_key.split(":")
                count = self.request_duration_count.get(dur_key, 1)
                avg = sum_val / max(count, 1)
                lines.append(f'http_request_duration_seconds_sum{{method="{method}",endpoint="{endpoint}"}} {sum_val:.4f}')
                lines.append(f'http_request_duration_seconds_count{{method="{method}",endpoint="{endpoint}"}} {count}')
                lines.append(f'http_request_duration_seconds_avg{{method="{method}",endpoint="{endpoint}"}} {avg:.4f}')

            lines.append("")
            lines.append("# HELP event_active_requests Currently executing HTTP requests")
            lines.append("# TYPE event_active_requests gauge")
            lines.append(f'event_active_requests {self.active_requests}')

            lines.append("")
            lines.append("# HELP event_cached_faces_total Total number of face embeddings cached in memory")
            lines.append("# TYPE event_cached_faces_total gauge")
            lines.append(f'event_cached_faces_total {vector_cache.count}')

            lines.append("")
            lines.append("# HELP face_search_duration_seconds Total time spent in ONNX face matching")
            lines.append("# TYPE face_search_duration_seconds summary")
            lines.append(f'face_search_duration_seconds_sum {self.face_search_duration_sum:.4f}')
            lines.append(f'face_search_duration_seconds_count {self.face_search_count}')

            return "\n".join(lines) + "\n"

metrics = MetricsCollector()

def check_readiness() -> Dict[str, Any]:
    """Verify all system dependencies: database, models, disk storage, memory cache."""
    checks = {}
    is_ready = True

    # 1. Database check
    try:
        from backend.database import get_connection
        conn = get_connection()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        checks["database"] = {"status": "ok", "path": str(DB_PATH)}
    except Exception as e:
        checks["database"] = {"status": "error", "error": str(e)}
        is_ready = False

    # 2. ONNX Face Models check
    yunet_ok = YUNET_MODEL_PATH.exists() and YUNET_MODEL_PATH.stat().st_size > 100_000
    sface_ok = SFACE_MODEL_PATH.exists() and SFACE_MODEL_PATH.stat().st_size > 10_000_000
    if yunet_ok and sface_ok:
        checks["face_models"] = {"status": "ok", "yunet": True, "sface": True}
    else:
        checks["face_models"] = {"status": "error", "yunet": yunet_ok, "sface": sface_ok}
        is_ready = False

    # 3. Storage / Disk space check
    try:
        total, used, free = shutil.disk_usage(DATA_DIR)
        free_mb = free // (1024 * 1024)
        checks["storage"] = {
            "status": "ok" if free_mb > 500 else "warning",
            "free_mb": free_mb,
            "total_gb": round(total / (1024 ** 3), 1)
        }
    except Exception as e:
        checks["storage"] = {"status": "unknown", "error": str(e)}

    # 4. In-memory cache status
    checks["vector_cache"] = {
        "status": "ok",
        "cached_faces": vector_cache.count,
        "is_warm": vector_cache.is_warm
    }

    return {"ready": is_ready, "checks": checks, "timestamp": time.time()}
