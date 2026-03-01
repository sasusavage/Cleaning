import logging
import re
from typing import Optional

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # pragma: no cover - Cloudinary optional in some environments
    cloudinary = None


def extract_cloudinary_public_id(url: str) -> Optional[str]:
    """Extract the Cloudinary public_id from a secure_url.

    Example URL: https://res.cloudinary.com/xxx/image/upload/v123/cleaning_service/services/abc.jpg
    Returns: cleaning_service/services/abc
    """
    if not url or 'cloudinary' not in url:
        return None
    # Match everything after /upload/vNNN/ and strip extension
    m = re.search(r'/upload/(?:v\d+/)?(.+?)(?:\.\w+)?$', url)
    return m.group(1) if m else None


def delete_cloudinary_image(url: str) -> bool:
    """Delete an image from Cloudinary by its secure_url. Returns True on success."""
    if not cloudinary:
        return False
    public_id = extract_cloudinary_public_id(url)
    if not public_id:
        return False
    try:
        result = cloudinary.uploader.destroy(public_id)
        ok = result.get('result') == 'ok'
        if not ok:
            logging.warning("Cloudinary destroy for %s returned: %s", public_id, result)
        return ok
    except Exception:
        logging.exception("Cloudinary destroy failed for public_id %s", public_id)
        return False


def upload_file_to_cloudinary(file_object, folder_name: str, resource_type: str = 'image', return_result: bool = False):
    """Upload a Flask file object to Cloudinary under cleaning_service/{folder_name}.

    Args:
        file_object: Werkzeug FileStorage from Flask request.
        folder_name: Folder suffix under cleaning_service/.
        resource_type: Cloudinary resource type (image, raw, auto, video, ...).
        return_result: When True, return the full Cloudinary response dict; otherwise return secure_url or None.
    """
    if not file_object or not getattr(file_object, 'filename', ''):
        return None

    if not cloudinary:
        logging.warning("Cloudinary SDK not installed; skipping remote upload for %s", file_object.filename)
        return None

    normalized_folder = (folder_name or 'misc').strip().strip('/') or 'misc'
    target_folder = f"cleaning_service/{normalized_folder}"

    try:
        result = cloudinary.uploader.upload(
            file_object,
            folder=target_folder,
            use_filename=True,
            unique_filename=True,
            resource_type=resource_type or 'image',
            overwrite=False,
        )
        return result if return_result else result.get('secure_url')
    except Exception:
        logging.exception("Cloudinary upload failed for folder %s", target_folder)
        return None
