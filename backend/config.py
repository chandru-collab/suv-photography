import os
from pathlib import Path
import socket
from dotenv import load_dotenv

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if present
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
PHOTOS_DIR = DATA_DIR / "photos"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
QR_DIR = DATA_DIR / "qr"
MODELS_DIR = BASE_DIR / "models"
DB_PATH = DATA_DIR / "events.db"

# Ensure directories exist
for d in [DATA_DIR, PHOTOS_DIR, THUMBNAILS_DIR, QR_DIR, MODELS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ONNX Face Models
YUNET_MODEL_PATH = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_MODEL_PATH = MODELS_DIR / "face_recognition_sface_2021dec.onnx"
YOLO_MODEL_PATH = MODELS_DIR / "yolov8n-face.onnx"

YUNET_DOWNLOAD_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
SFACE_DOWNLOAD_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
YOLO_DOWNLOAD_URL = "https://github.com/lindevs/yolov8-face/releases/latest/download/yolov8n-face-lindevs.onnx"

# Host & Security
HOST_PASSWORD = os.environ.get("HOST_PASSWORD", "photo123")
SECRET_KEY = os.environ.get("SECRET_KEY", "photo-sharing-secret-key-change-in-prod")
SESSION_COOKIE_NAME = "event_host_token"

# Face Recognition Settings
# OpenCV SFace high-precision cosine similarity threshold is 0.48.
# At 0.48, false positive rate drops to ~0 while true matches (0.60-0.75) are preserved.
DEFAULT_MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.48"))
FACE_DETECTOR_SCORE_THRESH = 0.32
FACE_DETECTOR_NMS_THRESH = 0.35
YOLO_CONF_THRESH = 0.28
YOLO_NMS_THRESH = 0.45
USE_YOLO_DETECTOR = True
MAX_IMAGE_DIMENSION = 2560

# Server Settings
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("PORT", os.environ.get("SERVER_PORT", "8000")))

# Google Cloud & Google Drive Integration
# 1. First check if raw JSON credentials were provided as an environment variable (Render / Cloud deployment)
_raw_creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
if _raw_creds_json:
    _creds_target = DATA_DIR / "google_credentials.json"
    try:
        _creds_target.write_text(_raw_creds_json, encoding="utf-8")
        GOOGLE_CREDENTIALS_PATH = str(_creds_target)
    except Exception:
        GOOGLE_CREDENTIALS_PATH = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", str(BASE_DIR / "suv-photography-514705-7a2bae36b793.json"))
else:
    GOOGLE_CREDENTIALS_PATH = os.environ.get(
        "GOOGLE_APPLICATION_CREDENTIALS",
        str(BASE_DIR / "suv-photography-514705-7a2bae36b793.json")
    )
DEFAULT_DRIVE_FOLDER_ID = os.environ.get(
    "DEFAULT_DRIVE_FOLDER_ID",
    "1hX6TEFSM5ZqlXapIA25dKAEq9jCDd5LO"
)

def get_local_ip() -> str:
    """Find the best local LAN IP of the host machine so guests on the same Wi-Fi can connect."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to an external address (does not actually send packets)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
