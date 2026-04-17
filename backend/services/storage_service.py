"""
Storage abstraction:
  - Cloudinary (production / CLOUDINARY_CLOUD_NAME set)
  - Local disk   (dev / no env var)
"""
import os
import io

_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
_cloudinary_enabled = bool(_CLOUD_NAME)

if _cloudinary_enabled:
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(
        cloud_name=_CLOUD_NAME,
        api_key=os.getenv("CLOUDINARY_API_KEY", ""),
        api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
    )

# Local dev upload dir (only used when Cloudinary is not configured)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LOCAL_UPLOAD_DIR = os.path.join(_BASE_DIR, os.getenv("UPLOAD_DIR", "uploads"))


def save_upload(file_bytes: bytes, folder: str, filename: str) -> str:
    """
    Save file and return its URL (Cloudinary) or local path (dev).
    folder: e.g. "42" or "42/receipts"
    """
    if _cloudinary_enabled:
        result = cloudinary.uploader.upload(
            io.BytesIO(file_bytes),
            folder=f"90days/{folder}",
            public_id=os.path.splitext(filename)[0],
            resource_type="auto",
            overwrite=True,
        )
        return result["secure_url"]

    # Dev: save to local disk
    dir_path = os.path.join(_LOCAL_UPLOAD_DIR, folder)
    os.makedirs(dir_path, exist_ok=True)
    full_path = os.path.join(dir_path, filename)
    with open(full_path, "wb") as f:
        f.write(file_bytes)
    return full_path


def read_file_bytes(path_or_url: str) -> bytes:
    """Read file bytes from local path or any URL."""
    if path_or_url.startswith("http"):
        import urllib.request
        with urllib.request.urlopen(path_or_url, timeout=30) as resp:
            return resp.read()
    with open(path_or_url, "rb") as f:
        return f.read()


def file_exists(path_or_url: str) -> bool:
    if not path_or_url:
        return False
    if path_or_url.startswith("http"):
        return True  # Assume Cloudinary URLs are always valid
    return os.path.exists(path_or_url)


def get_ext(path_or_url: str) -> str:
    """Extract file extension (.jpg / .png / .pdf) from path or URL."""
    clean = path_or_url.split("?")[0].split("#")[0]
    return os.path.splitext(clean)[1].lower() or ".jpg"
