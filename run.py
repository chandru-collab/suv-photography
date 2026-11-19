import sys
import uvicorn
from pathlib import Path

# Add workspace directory to python path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from backend.config import (
    SERVER_HOST,
    SERVER_PORT,
    get_local_ip,
    HOST_PASSWORD,
    YUNET_MODEL_PATH,
    SFACE_MODEL_PATH
)
from backend.qr_service import generate_qr_code, get_guest_url
from backend.database import init_db

def main():
    init_db()
    local_ip = get_local_ip()
    guest_url = get_guest_url()
    qr_path = generate_qr_code()

    print("=" * 65)
    print(" 📸 EVENT PHOTO SHARING APPLICATION")
    print("=" * 65)
    print(f" • YuNet ONNX Detector : {YUNET_MODEL_PATH.name} ({YUNET_MODEL_PATH.stat().st_size // 1024} KB)")
    print(f" • SFace ONNX Recognizer: {SFACE_MODEL_PATH.name} ({SFACE_MODEL_PATH.stat().st_size // (1024 * 1024)} MB)")
    print("=" * 65)
    print(f" 🌐 Host Dashboard   : http://localhost:{SERVER_PORT}/dashboard")
    print(f" 📁 Drive Gallery    : http://localhost:{SERVER_PORT}/gallery")
    print(f" 🔑 Host Password    : {HOST_PASSWORD}")
    print(f" 📱 Guest Mobile URL : {guest_url}")
    print(f" 🏷️  Default QR Code  : {qr_path}")
    print("=" * 65)
    print(" Starting server on 0.0.0.0:8000 (accessible on local network)...")
    print(" Press Ctrl+C to stop.\n")

    uvicorn.run(
        "backend.app:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    main()
