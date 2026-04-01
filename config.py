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
    # Primary database engine for the app: 'mysql' or 'postgres'
    # Normalize 'postgresql' → 'postgres' so all engine == 'postgres' checks work
    _raw_engine = os.environ.get('DB_ENGINE', 'postgres').strip().lower()
    DB_ENGINE = 'postgres' if 'postgres' in _raw_engine else _raw_engine

    MYSQL_HOST = os.environ.get('MYSQL_HOST', 'your-db-host')
    MYSQL_USER = os.environ.get('MYSQL_USER', 'your-db-user')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', 'your-db-password')
    MYSQL_DB = os.environ.get('MYSQL_DB', 'your-db-name')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', 3306))

    # Render Postgres (optional secondary DB). Use the External Database URL.
    POSTGRES_URL = os.environ.get('POSTGRES_URL', '')

    # Which DB should store analytics events? ('mysql' or 'postgres')
    ANALYTICS_DB = os.environ.get('ANALYTICS_DB', 'postgres')

    MYSQL_CURSORCLASS = 'DictCursor'
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key')
    EMAIL_ENCRYPTION_KEY = os.environ.get('EMAIL_ENCRYPTION_KEY', 'your-email-key')

    # Cloudinary settings
    CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', 'dhzw2vdy9')
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
