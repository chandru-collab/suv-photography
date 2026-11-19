import time
import uuid
import json
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from backend.monitoring import metrics

# Configure structured production JSON logger
prod_logger = logging.getLogger("production_access")
prod_logger.setLevel(logging.INFO)
# Avoid duplicate handlers if reloaded
if not prod_logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    prod_logger.addHandler(handler)

class ProductionMiddleware(BaseHTTPMiddleware):
    """
    Production-grade middleware:
    1. Injects a unique X-Request-ID for end-to-end distributed tracing.
    2. Measures exact request latency with high-precision perf_counter.
    3. Records request metrics for Prometheus monitoring.
    4. Logs structured JSON access lines for observability (Datadog, ELK, CloudWatch).
    5. Injects standard security headers.
    """
    async def dispatch(self, request: Request, call_next):
        # 1. Unique Request ID
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        # 2. Timing
        start_time = time.perf_counter()
        metrics.active_requests += 1

        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip")
            or (request.client.host if request.client else "unknown")
        )

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            status_code = 500
            metrics.active_requests = max(0, metrics.active_requests - 1)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            prod_logger.error(json.dumps({
                "type": "error",
                "request_id": request_id,
                "client_ip": client_ip,
                "method": request.method,
                "path": request.url.path,
                "status": 500,
                "duration_ms": round(duration_ms, 2),
                "error": str(exc)
            }))
            raise exc

        metrics.active_requests = max(0, metrics.active_requests - 1)
        duration_sec = time.perf_counter() - start_time
        duration_ms = duration_sec * 1000.0

        # 3. Record Metrics
        # Normalize path to avoid high-cardinality endpoint explosion (e.g. /api/photos/123 -> /api/photos/:id)
        path = request.url.path
        if path.startswith("/api/photos/"):
            norm_path = "/api/photos/:id"
        elif path.startswith("/api/thumbnails/"):
            norm_path = "/api/thumbnails/:id"
        else:
            norm_path = path

        metrics.record_request(request.method, norm_path, status_code, duration_sec)

        # 4. Attach Production Headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # 5. Emit Structured JSON Access Log (skip health checks from filling logs)
        if not path.startswith("/healthz"):
            log_entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "request_id": request_id,
                "client_ip": client_ip,
                "method": request.method,
                "path": path,
                "status": status_code,
                "duration_ms": round(duration_ms, 2)
            }
            prod_logger.info(json.dumps(log_entry))

        return response
