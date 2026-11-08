import os

try:
    import cloudinary
except ImportError:
    cloudinary = None

try:
    from dotenv import load_dotenv

    # Load environment variables from .env file
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'), override=True)
except Exception:
    # If python-dotenv isn't installed or .env isn't present, fall back to OS env vars.
    pass

class Config:
    # The app's only database: Render Postgres (External Database URL).
    POSTGRES_URL = os.environ.get('POSTGRES_URL', '')

    # SECRET_KEY must be set as an env var in production. If missing, generate a
    # random one per process — sessions won't persist across restarts but it's
    # safer than shipping a hardcoded fallback.
    _secret_key_env = os.environ.get('SECRET_KEY', '').strip()
    if not _secret_key_env:
        import warnings as _warnings
        _warnings.warn(
            "SECRET_KEY env var is not set. A random key will be generated per process — "
            "admin sessions will not persist across restarts. Set SECRET_KEY in production.",
            RuntimeWarning, stacklevel=2
        )
    SECRET_KEY = _secret_key_env if _secret_key_env else os.urandom(32).hex()

    _email_enc_key_env = os.environ.get('EMAIL_ENCRYPTION_KEY', '').strip()
    if not _email_enc_key_env:
        import warnings as _warnings
        _warnings.warn(
            "EMAIL_ENCRYPTION_KEY env var is not set. Emails will not be encrypted. "
            "Set EMAIL_ENCRYPTION_KEY to a secure random string in production.",
            RuntimeWarning, stacklevel=2
        )
    EMAIL_ENCRYPTION_KEY = _email_enc_key_env or os.urandom(32).hex()
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', os.environ.get('SITE_URL', '')).strip().rstrip('/')

    # ── Session security ──────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True          # JS cannot read session cookie
    SESSION_COOKIE_SAMESITE = 'Lax'        # Blocks CSRF via cross-site requests
    # Only send cookie over HTTPS in production
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_ENV', 'production').lower() != 'development'
    PERMANENT_SESSION_LIFETIME = 43200      # 12 h max session lifetime in seconds

    # ── Upload size limit ─────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB hard cap on all uploads

    # Cloudinary settings
    CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', '')
    CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY', '')
    CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '')

    @classmethod
    def configure_cloudinary(cls):
        """Configure Cloudinary SDK if credentials are present."""
        if not cloudinary:
            return
        if not (cls.CLOUDINARY_CLOUD_NAME and cls.CLOUDINARY_API_KEY and cls.CLOUDINARY_API_SECRET):
            return
        cloudinary.config(
            cloud_name=cls.CLOUDINARY_CLOUD_NAME,
            api_key=cls.CLOUDINARY_API_KEY,
            api_secret=cls.CLOUDINARY_API_SECRET,
            secure=True,
        )


Config.configure_cloudinary()
