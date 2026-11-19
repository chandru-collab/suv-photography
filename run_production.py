import os
import sys
import multiprocessing
from pathlib import Path
import uvicorn

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from backend.config import SERVER_HOST, SERVER_PORT, HOST_PASSWORD, YUNET_MODEL_PATH, SFACE_MODEL_PATH
from backend.qr_service import get_guest_url

def main():
    # Production CPU Worker Auto-Tuning: respect WEB_CONCURRENCY, auto-scale based on cores
    default_workers = max(1, min(4, (os.cpu_count() or 1)))
    workers = int(os.environ.get("WEB_CONCURRENCY", default_workers))

    guest_url = get_guest_url()

    print("=" * 70)
    print("  PRODUCTION SERVER - HIGH THROUGHPUT MODE (1,000+ RPS READY)")
    print("=" * 70)
    print(f" • CPU Cores Detected      : {os.cpu_count()}")
    print(f" • Worker Processes Spawned : {workers}")
    print(f" • Socket Connection Backlog: 2048")
    print(f" • Concurrency Limit / Worker: 2000 connections")
    print(f" • In-Memory Vector Caching : ACTIVE")
    print(f" • Sliding Window Rate Limit: ACTIVE")
    print(f" • Prometheus Metrics       : http://localhost:{SERVER_PORT}/metrics")
    print(f" • Liveness Probe           : http://localhost:{SERVER_PORT}/healthz")
    print(f" • Readiness Probe          : http://localhost:{SERVER_PORT}/readyz")
    print("=" * 70)
    print(f" 🌐 Host Dashboard   : http://localhost:{SERVER_PORT}/dashboard")
    print(f" 📁 Drive Gallery    : http://localhost:{SERVER_PORT}/gallery")
    print(f" 🔑 Host Password    : {HOST_PASSWORD}")
    print(f" 📱 Guest Mobile URL : {guest_url}")
    print("=" * 70)
    print(" Starting production Uvicorn cluster...")
    print(" Press Ctrl+C to terminate.\n")

    uvicorn.run(
        "backend.app:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        workers=workers,
        backlog=2048,
        limit_concurrency=2000,
        timeout_keep_alive=30,
        access_log=False, # Structured JSON access log is emitted by ProductionMiddleware
        log_level="info"
    )

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
