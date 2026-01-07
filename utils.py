import logging
from typing import Optional

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:  # pragma: no cover - Cloudinary optional in some environments
    cloudinary = None


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
