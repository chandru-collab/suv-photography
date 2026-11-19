import os
import io
import base64
from pathlib import Path
import qrcode
from PIL import Image, ImageDraw

from backend.config import QR_DIR, SERVER_PORT, get_local_ip
from backend.database import get_setting

DEFAULT_QR_PATH = QR_DIR / "event_qr.png"

def get_guest_url() -> str:
    """Return the active guest URL for the event."""
    configured = get_setting("guest_base_url", "").strip()
    if configured:
        return configured.rstrip("/")
    # Production / Cloud deployment (Vercel frontend or public custom domain)
    public_url = (os.environ.get("PUBLIC_URL") or os.environ.get("VERCEL_URL") or os.environ.get("FRONTEND_URL") or "").strip()
    if public_url:
        if not public_url.startswith("http://") and not public_url.startswith("https://"):
            public_url = f"https://{public_url}"
        return public_url.rstrip("/")
    local_ip = get_local_ip()
    return f"http://{local_ip}:{SERVER_PORT}"

def _load_suv_logo() -> Image.Image:
    """Load the official black SUV monogram logo (no wine background)."""
    base_dir = Path(__file__).resolve().parent.parent / "static" / "img"
    for name in ["suv_monogram_black.png", "suv_badge.png"]:
        p = base_dir / name
        if p.exists():
            return Image.open(str(p)).convert("RGBA")
    return None

def generate_qr_code(url: str = None) -> Path:
    """
    Generate clean, high-contrast QR code with black SUV monogram on pure white background:
    - Standard high-contrast black square modules on a pure white background
    - Level H Error Correction (~30% recovery) ensuring 100% scannability
    - Clears exact module blocks in the matrix before rendering (no clipped/chopped pixels)
    - Perfectly centered black SUV logo on pure white (no French Wine background)
    """
    if not url:
        url = get_guest_url()

    QR_DIR.mkdir(parents=True, exist_ok=True)

    box_size = 20
    border = 4

    # High Error Correction (~30% recovery) ensures 100% scannability with center logo
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=border
    )
    qr.add_data(url)
    qr.make(fit=True)

    m_count = qr.modules_count

    # Calculate exact symmetric square module dimensions to clear in the center
    center_size = int(m_count * 0.26)
    if (m_count - center_size) % 2 != 0:
        center_size += 1

    r_start = (m_count - center_size) // 2
    r_end = r_start + center_size
    c_start = (m_count - center_size) // 2
    c_end = c_start + center_size

    # Clear modules directly in QR matrix BEFORE rendering so all surrounding
    # modules remain 100% full, sharp, intact squares with zero jagged edges
    for r in range(r_start, r_end):
        for c in range(c_start, c_end):
            qr.modules[r][c] = False

    # Render clean QR code (every module is a sharp, complete square)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    # Center white opening pixel bounds
    bx0 = (border + c_start) * box_size
    by0 = (border + r_start) * box_size
    bw = center_size * box_size
    bh = center_size * box_size

    # Load and embed the black SUV logo on pure white (no wine background)
    logo = _load_suv_logo()
    if logo:
        scale = min((bw * 0.82) / logo.width, (bh * 0.82) / logo.height)
        target_lw = int(logo.width * scale)
        target_lh = int(logo.height * scale)
        resized_logo = logo.resize((target_lw, target_lh), Image.Resampling.LANCZOS)
        lx = bx0 + (bw - target_lw) // 2
        ly = by0 + (bh - target_lh) // 2
        qr_img.paste(resized_logo, (lx, ly), mask=resized_logo)

    # Save to standard QR path
    qr_img.save(str(DEFAULT_QR_PATH), format="PNG")
    return DEFAULT_QR_PATH

def get_qr_base64() -> str:
    """Get QR code as base64 string for immediate frontend rendering."""
    if not DEFAULT_QR_PATH.exists():
        generate_qr_code()
    with open(DEFAULT_QR_PATH, "rb") as f:
        data = f.read()
    return f"data:image/png;base64,{base64.b64encode(data).decode('utf-8')}"
