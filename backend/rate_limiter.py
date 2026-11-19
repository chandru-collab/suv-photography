import time
import threading
from typing import Dict, Tuple, Optional
from fastapi import Request, HTTPException, status

class SlidingWindowRateLimiter:
    """
    High-performance, in-memory sliding window rate limiter.
    Limits request rates per IP address.
    Protects CPU-heavy ONNX face extraction endpoints from denial-of-service.
    """
    def __init__(self, requests_per_window: int = 60, window_seconds: int = 60):
        self.limit = requests_per_window
        self.window = window_seconds
        self._lock = threading.Lock()
        # storage: ip -> list of timestamps
        self._clients: Dict[str, list] = {}
        self._last_cleanup = time.time()

    def _get_client_ip(self, request: Request) -> str:
        # Check standard reverse proxy headers
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # First IP in the comma-separated chain is the client
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        if request.client and request.client.host:
            return request.client.host
        return "127.0.0.1"

    def check(self, request: Request, custom_limit: Optional[int] = None, custom_window: Optional[int] = None) -> Tuple[bool, int, float]:
        """
        Check if request from client IP is allowed.
        Returns: (allowed, remaining_requests, reset_seconds)
        """
        ip = self._get_client_ip(request)
        now = time.time()
        limit = custom_limit or self.limit
        window = custom_window or self.window

        with self._lock:
            # Periodic background cleanup of expired clients every 60s
            if now - self._last_cleanup > 60:
                self._cleanup(now)
                self._last_cleanup = now

            timestamps = self._clients.setdefault(ip, [])
            cutoff = now - window

            # Discard timestamps older than window
            timestamps = [t for t in timestamps if t > cutoff]
            self._clients[ip] = timestamps

            count = len(timestamps)
            if count >= limit:
                oldest = timestamps[0]
                reset_seconds = max(0.0, (oldest + window) - now)
                return False, 0, reset_seconds

            timestamps.append(now)
            remaining = limit - len(timestamps)
            return True, remaining, window

    def _cleanup(self, now: float):
        cutoff = now - self.window
        dead_ips = [ip for ip, times in self._clients.items() if not times or times[-1] <= cutoff]
        for ip in dead_ips:
            del self._clients[ip]

# Pre-configured rate limiters
# Search limiter: up to 45 searches per minute per IP (heavy ONNX inference)
search_rate_limiter = SlidingWindowRateLimiter(requests_per_window=45, window_seconds=60)
# General API limiter: 300 requests per minute per IP
general_rate_limiter = SlidingWindowRateLimiter(requests_per_window=300, window_seconds=60)

async def require_search_rate_limit(request: Request):
    """FastAPI dependency for endpoints needing rate limiting."""
    allowed, remaining, reset_secs = search_rate_limiter.check(request)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Please wait {int(reset_secs) + 1} seconds before trying again.",
            headers={
                "Retry-After": str(int(reset_secs) + 1),
                "X-RateLimit-Limit": str(search_rate_limiter.limit),
                "X-RateLimit-Remaining": "0"
            }
        )
