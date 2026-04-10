from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, send_file, session, Response, g
import mysql.connector
import os
from uuid import uuid4
from collections import OrderedDict
from xml.sax.saxutils import escape as xml_escape
from werkzeug.utils import secure_filename, safe_join
from werkzeug.security import check_password_hash
from functools import wraps
from config import Config
from utils import upload_file_to_cloudinary, delete_cloudinary_image
import random
import json
import re
import smtplib
import base64
import hashlib
import traceback
import math
import mimetypes
import requests
from requests import RequestException
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr
from io import BytesIO
from datetime import datetime, timedelta, date, timezone
import calendar
import time
import threading
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP

try:
    import resend
except ImportError:
    resend = None

try:
    import stripe
except ImportError:
    stripe = None

try:
    import psycopg2
    from psycopg2.extras import Json as PGJson, RealDictCursor
    from psycopg2 import pool as psycopg2_pool
except ImportError:
    psycopg2 = None
    psycopg2_pool = None

# ── PostgreSQL connection pool (created once at startup) ──────────────────────
_pg_pool = None

def _get_pg_pool():
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    if psycopg2_pool is None:
        return None
    dsn = (os.environ.get('POSTGRES_URL') or '').strip()
    if not dsn:
        return None
    try:
        _pg_pool = psycopg2_pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=20,
            dsn=dsn,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
            connect_timeout=10,
        )
        import logging as _log
        _log.getLogger(__name__).info('PostgreSQL connection pool created (min=1, max=10)')
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning('Could not create PG pool: %s', exc)
        _pg_pool = None
    return _pg_pool
    PGJson = None
    RealDictCursor = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # cryptography is optional; fallback handlers will be used
    Fernet = None
    InvalidToken = Exception

app = Flask(__name__, static_folder='.', static_url_path='')


@app.teardown_appcontext
def _close_leaked_db_conns(exc):
    """Return any DB connections that weren't explicitly closed back to the pool."""
    conns = getattr(g, '_db_conns', [])
    for conn in conns:
        try:
            if not getattr(conn, '_closed', False):
                conn.close()
        except Exception:
            pass


@app.route('/healthz')
def healthz():
    return 'ok', 200


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'favicon'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )


@app.route('/favicon/<path:filename>')
def favicon_asset(filename):
    return send_from_directory(
        os.path.join(app.root_path, 'favicon'),
        filename
    )


@app.route('/android-chrome-192x192.png')
def favicon_android_192():
    return send_from_directory(os.path.join(app.root_path, 'favicon'), 'android-chrome-192x192.png', mimetype='image/png')


@app.route('/android-chrome-512x512.png')
def favicon_android_512():
    return send_from_directory(os.path.join(app.root_path, 'favicon'), 'android-chrome-512x512.png', mimetype='image/png')


@app.route('/apple-touch-icon.png')
def favicon_apple_touch_icon():
    return send_from_directory(os.path.join(app.root_path, 'favicon'), 'apple-touch-icon.png', mimetype='image/png')
app.config.from_object(Config)

UPLOAD_ROOT = os.path.join(app.static_folder, 'static', 'uploads')
SERVICE_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'services')
JOB_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'jobs')
HERO_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'hero')
BADGE_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'badges')
QUOTE_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'quote')
REQUEST_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'requests')
BRAND_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'brand')
ABOUT_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'about')
DOMESTIC_UPLOAD_FOLDER = os.path.join(UPLOAD_ROOT, 'domestic')
for folder in (
    UPLOAD_ROOT,
    SERVICE_UPLOAD_FOLDER,
    JOB_UPLOAD_FOLDER,
    HERO_UPLOAD_FOLDER,
    BADGE_UPLOAD_FOLDER,
    QUOTE_UPLOAD_FOLDER,
    REQUEST_UPLOAD_FOLDER,
    BRAND_UPLOAD_FOLDER,
    ABOUT_UPLOAD_FOLDER,
    DOMESTIC_UPLOAD_FOLDER
):
    os.makedirs(folder, exist_ok=True)

IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | {'pdf', 'doc', 'docx'}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB
FERNET_PREFIX = 'fernet::'
BASE64_PREFIX = 'base64::'
REQUEST_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled', 'survey_needed', 'draft'}
REQUEST_STATUS_LABELS = {
    'pending': 'Pending',
    'in_progress': 'In Progress',
    'completed': 'Completed',
    'cancelled': 'Cancelled',
    'survey_needed': 'Survey Needed',
    'draft': 'Draft'
}
PREBOOK_DISCOUNT_PERCENT = 10
PAYMENT_OPTION_PREBOOK = 'prebook_save'
PAYMENT_OPTION_IN_PERSON = 'pay_in_person'
STRIPE_PENDING_TO_DRAFT_MINUTES = 10

TRAVEL_CACHE_TTL_SECONDS = 15 * 60
SESSION_TRAVEL_CACHE_TTL_SECONDS = 10 * 60
_travel_quote_cache = {}
_travel_cache_lock = threading.Lock()
EMAIL_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_contract_reminder_lock = threading.Lock()
_contract_reminder_last_run = datetime.min

def allowed_file(filename, allowed_extensions=None):
    extensions = allowed_extensions or IMAGE_EXTENSIONS
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in extensions
    
def generate_unique_ref_id(cursor, attempts=5):
    for _ in range(max(1, attempts)):
        candidate = uuid4().hex[:10].upper()
        cursor.execute("SELECT id FROM requests WHERE ref_id = %s LIMIT 1", (candidate,))
        if not cursor.fetchone():
            return candidate
    # Fallback to full uuid if short code collides repeatedly
    return uuid4().hex.upper()


def save_file(file, folder, allowed_extensions=None):
    if not file or not file.filename:
        return None
    if not allowed_file(file.filename, allowed_extensions):
        allowed_display = ', '.join(sorted((allowed_extensions or IMAGE_EXTENSIONS)))
        raise ValueError(f'Unsupported file type. Allowed types: {allowed_display}.')

    filename = secure_filename(file.filename)
    unique_name = f"{uuid4().hex}_{filename}"
    destination = os.path.join(folder, unique_name)
    file.save(destination)
    relative_path = os.path.relpath(destination, app.static_folder).replace('\\', '/')
    return relative_path

def handle_upload(field_name, folder, existing_path=''):
    """Upload an admin asset to Cloudinary, falling back to local storage if needed."""
    file = request.files.get(field_name)
    if file and file.filename:
        folder_name = os.path.basename(folder.rstrip(os.sep)) or 'misc'
        cloud_url = upload_file_to_cloudinary(file, folder_name)
        if cloud_url:
            return cloud_url
        saved = save_file(file, folder)
        return saved or ''
    return (existing_path or '').strip()

def upload_service_image(existing_path=''):
    return handle_upload('image', SERVICE_UPLOAD_FOLDER, existing_path)

def upload_job_image(existing_path=''):
    return handle_upload('image', JOB_UPLOAD_FOLDER, existing_path)


def upload_hero_background(existing_path=''):
    return handle_upload('background_image', HERO_UPLOAD_FOLDER, existing_path)


def upload_badge_image():
    return handle_upload('image', BADGE_UPLOAD_FOLDER)


def upload_quote_background(existing_path=''):
    return handle_upload('quote_background', QUOTE_UPLOAD_FOLDER, existing_path)


def upload_brand_logo(existing_path=''):
    return handle_upload('logo', BRAND_UPLOAD_FOLDER, existing_path)


def upload_team_photo(existing_path=''):
    return handle_upload('team_photo', ABOUT_UPLOAD_FOLDER, existing_path)


def upload_domestic_card_image(existing_path=''):
    return handle_upload('image', DOMESTIC_UPLOAD_FOLDER, existing_path)


def delete_uploaded_file(relative_path):
    """Delete an image from Cloudinary (if URL) and/or local filesystem."""
    if not relative_path:
        return
    # If it's a Cloudinary URL, destroy it remotely
    if 'cloudinary' in (relative_path or ''):
        delete_cloudinary_image(relative_path)
    # Also try local file removal (for legacy local uploads)
    absolute_path = os.path.join(app.static_folder, relative_path.replace('/', os.sep))
    if os.path.isfile(absolute_path):
        try:
            os.remove(absolute_path)
        except OSError:
            app.logger.warning('Failed to delete file at %s', absolute_path)


@app.template_filter('media_url')
def media_url_filter(value):
    """Convert stored media paths to usable URLs (handles absolute + static paths).
    For Cloudinary URLs, inject auto-format + auto-quality transforms for faster loading."""
    if not value:
        return ''
    value = str(value).strip()
    if value.startswith(('http://', 'https://')):
        # Inject Cloudinary optimisation transforms if not already present
        if 'res.cloudinary.com' in value and '/upload/' in value and 'f_auto' not in value:
            value = value.replace('/upload/', '/upload/f_auto,q_auto/', 1)
        return value
    return url_for('static', filename=value.lstrip('/'))


@app.template_filter('optimized_card_url')
def optimized_card_url_filter(value):
    """Like media_url but also limits width to 600px for card thumbnails."""
    url = media_url_filter(value)
    if url and 'res.cloudinary.com' in url and '/upload/' in url:
        # Add width limit after existing transforms
        url = url.replace('f_auto,q_auto', 'f_auto,q_auto,w_600,c_limit', 1)
    return url


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def build_active_true_condition(column: str, engine: str):
    """Return SQL fragment and params to test truthy flag across engines."""
    normalized = (engine or 'mysql').strip().lower()
    if 'postgres' in normalized:
        # Works for boolean or smallint/tinyint columns migrated from MySQL
        return f"COALESCE({column}::text, '0') IN ('1','t','true')", ()
    return f"{column} = %s", (1,)


def sanitize_text(value, max_length=None):
    if value is None:
        return ''
    cleaned = re.sub(r'\s+', ' ', str(value)).strip()
    cleaned = cleaned.replace('<', '').replace('>', '')
    if max_length is not None:
        cleaned = cleaned[:max_length]
    return cleaned


def sanitize_int_range(value, default=0, min_value=-10000, max_value=10000):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def sanitize_css_color(value, default='#ffffff', allow_empty=False):
    cleaned = sanitize_text(value, 20)
    if allow_empty and not cleaned:
        return ''
    if re.fullmatch(r'#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?', cleaned):
        return cleaned
    return default


def sanitize_email(value, max_length=150):
    if not value:
        return ''
    email = sanitize_text(value, max_length)
    if '@' not in email:
        return ''
    return email


def sanitize_phone(value, max_length=50):
    if not value:
        return ''
    phone = sanitize_text(value, max_length)
    return phone


def admin_login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('admin_logged_in'):
            if request.path.startswith('/admin/api/'):
                return jsonify({'error': 'Authentication required.'}), 401
            return redirect(url_for('admin_login'))
        return func(*args, **kwargs)

    return wrapper


def get_status_label(status_key):
    if not status_key:
        return REQUEST_STATUS_LABELS.get('pending')
    normalized = str(status_key).strip().lower()
    if normalized in REQUEST_STATUS_LABELS:
        return REQUEST_STATUS_LABELS[normalized]
    return sanitize_text(status_key.replace('_', ' ').title(), 64)


def derive_fernet_key(secret):
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest)


def get_email_cipher():
    if not Fernet:
        return None
    secret = app.config.get('EMAIL_ENCRYPTION_KEY')
    if not secret:
        return None
    try:
        return Fernet(derive_fernet_key(secret))
    except Exception:
        app.logger.warning('Invalid EMAIL_ENCRYPTION_KEY configured; encryption disabled.')
        return None


def encrypt_secret(plaintext):
    if not plaintext:
        return None
    cipher = get_email_cipher()
    data = plaintext.encode('utf-8')
    if cipher:
        token = cipher.encrypt(data).decode('utf-8')
        return f"{FERNET_PREFIX}{token}"
    encoded = base64.b64encode(data).decode('utf-8')
    return f"{BASE64_PREFIX}{encoded}"


def decrypt_secret(value):
    if not value:
        return ''
    try:
        if isinstance(value, bytes):
            value = value.decode('utf-8')
        if value.startswith(FERNET_PREFIX):
            cipher = get_email_cipher()
            if not cipher:
                app.logger.warning('Unable to decrypt email secret because encryption key is not available.')
                return ''
            token = value[len(FERNET_PREFIX):]
            decrypted = cipher.decrypt(token.encode('utf-8')).decode('utf-8')
            return decrypted
        if value.startswith(BASE64_PREFIX):
            decoded = base64.b64decode(value[len(BASE64_PREFIX):]).decode('utf-8')
            return decoded
        return value
    except (InvalidToken, ValueError, TypeError) as exc:
        app.logger.warning('Failed to decrypt secret: %s', exc)
        return ''


def parse_recipient_list(raw_value):
    if not raw_value:
        return []
    parts = [segment.strip() for segment in str(raw_value).split(',')]
    return [sanitize_email(part) for part in parts if sanitize_email(part)]


def fetch_email_settings():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM email_settings WHERE id = 1")
    row = cursor.fetchone() or {}
    cursor.close()
    conn.close()

    smtp_password = decrypt_secret(row.get('smtp_password_encrypted')) if row.get('smtp_password_encrypted') else ''
    row['smtp_password'] = smtp_password
    resend_api_key = decrypt_secret(row.get('resend_api_key_encrypted')) if row.get('resend_api_key_encrypted') else ''
    row['resend_api_key'] = resend_api_key
    row['admin_recipient_list'] = parse_recipient_list(row.get('admin_recipients'))
    # Default to 'smtp' if not set
    row['email_provider'] = (row.get('email_provider') or 'smtp').strip().lower()
    return row


def log_email_error(request_id=None, email_type='notification', subject=None, recipients=None, error_message=None, error_payload=None):
    recipients_value = ''
    if recipients:
        if isinstance(recipients, (list, tuple, set)):
            recipients_value = ','.join([sanitize_email(addr) for addr in recipients if sanitize_email(addr)])
        else:
            recipients_value = sanitize_text(str(recipients), 255)

    payload_json = None
    if error_payload:
        try:
            payload_json = json.dumps(error_payload)
        except (TypeError, ValueError):
            payload_json = json.dumps({'detail': str(error_payload)})

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO email_errors (request_id, email_type, subject, recipients, error_message, error_payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                request_id,
                sanitize_text(email_type, 64) or 'notification',
                sanitize_text(subject, 255) if subject else None,
                recipients_value or None,
                (str(error_message).strip() or 'Unknown email error.')[:2000],
                payload_json
            )
        )
        conn.commit()
    except Exception:
        app.logger.exception('Failed to log email error for request %s', request_id)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def send_email_via_resend(subject, html_body, text_body, recipients, settings, attachments=None, reply_to=None, error_context=None, request_id=None, extra_error_payload=None):
    """Send email using Resend API."""
    context_key = sanitize_text(error_context or 'notification', 64) or 'notification'
    subject_summary = sanitize_text(subject, 150) if subject else '(none)'

    if not resend:
        app.logger.error('Resend package not installed. Cannot send email via Resend API.')
        log_email_error(
            request_id=request_id,
            email_type=context_key,
            subject=subject_summary,
            recipients=recipients,
            error_message='Resend package not installed.',
            error_payload={'reason': 'resend_not_installed'}
        )
        return False

    api_key = (settings.get('resend_api_key') or '').strip()
    if not api_key:
        app.logger.warning('Resend API key not configured. Email not sent.')
        payload = dict(extra_error_payload or {})
        payload['reason'] = 'missing_resend_api_key'
        log_email_error(
            request_id=request_id,
            email_type=context_key,
            subject=subject_summary,
            recipients=recipients,
            error_message='Resend API key not configured.',
            error_payload=payload
        )
        send_telegram_notification(
            'notify_email_error',
            [
                '[Email] Delivery failed',
                f'Context: {context_key}',
                f'Subject: {subject_summary}',
                'Reason: Resend API key not configured.'
            ]
        )
        return False

    sender_email = sanitize_email(settings.get('sender_email')) or 'no-reply@example.com'
    sender_name = sanitize_text(settings.get('sender_name') or 'Notifications', 150)
    from_address = f"{sender_name} <{sender_email}>"

    # Configure Resend API key
    resend.api_key = api_key

    # Prepare attachments for Resend
    resend_attachments = []
    attachment_manifest = []
    MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
    MAX_SINGLE_FILE_SIZE = 10 * 1024 * 1024
    total_attachment_size = 0

    for attachment in attachments or []:
        filename = attachment.get('filename') or 'attachment'
        path = attachment.get('absolute_path')
        remote_url = attachment.get('remote_url')
        payload = None
        file_size = None

        if path and os.path.isfile(path):
            try:
                file_size = os.path.getsize(path)
                with open(path, 'rb') as file_handle:
                    payload = file_handle.read()
            except OSError:
                app.logger.warning('Unable to read attachment from %s', path)
                payload = None
        elif remote_url:
            try:
                response = requests.get(remote_url, timeout=20)
                response.raise_for_status()
                payload = response.content
                file_size = len(payload)
            except RequestException:
                app.logger.warning('Unable to download remote attachment %s', remote_url)
                payload = None

        if not payload:
            continue

        file_size = file_size or len(payload)

        if file_size > MAX_SINGLE_FILE_SIZE:
            app.logger.warning('Skipping attachment %s (%.2f MB) - exceeds single file limit.', filename, file_size / (1024 * 1024))
            continue

        if total_attachment_size + file_size > MAX_ATTACHMENT_SIZE:
            app.logger.warning('Skipping attachment %s - would exceed total attachment limit.', filename)
            continue

        resend_attachments.append({
            'filename': filename,
            'content': list(payload)  # Resend expects bytes as list of integers
        })
        attachment_manifest.append({'filename': filename, 'size': file_size, 'remote': bool(remote_url)})
        total_attachment_size += file_size

    try:
        email_params = {
            'from': from_address,
            'to': recipients,
            'subject': subject,
            'html': html_body or '',
            'text': text_body or 'Please view this email in an HTML compatible client.'
        }

        if reply_to and sanitize_email(reply_to):
            email_params['reply_to'] = sanitize_email(reply_to)

        if resend_attachments:
            email_params['attachments'] = resend_attachments

        result = resend.Emails.send(email_params)

        success_lines = [
            '[Email] Delivery succeeded (Resend)',
            f'Context: {context_key}',
            f'Subject: {subject_summary}',
            f'Recipients: {", ".join(recipients)}',
            f'Attachments: {len(attachment_manifest)}'
        ]
        if request_id:
            success_lines.append(f'Reference ID: {request_id}')
        send_telegram_notification('notify_email_success', success_lines)
        return True

    except Exception as exc:
        app.logger.exception('Failed to send email via Resend API with subject %s', subject)
        payload = dict(extra_error_payload or {})
        payload.update({
            'provider': 'resend',
            'attachment_count': len(attachment_manifest)
        })
        raw_error = ''.join(traceback.format_exception_only(type(exc), exc)).strip() or str(exc)
        error_details = sanitize_text(raw_error, 180)
        log_email_error(
            request_id=request_id,
            email_type=context_key,
            subject=subject_summary,
            recipients=recipients,
            error_message=error_details,
            error_payload=payload
        )
        error_lines = [
            '[Email] Delivery failed (Resend)',
            f'Context: {context_key}',
            f'Subject: {subject_summary}',
            f'Error: {error_details}',
            f'Recipients: {", ".join(recipients) if recipients else "(none)"}'
        ]
        if request_id:
            error_lines.append(f'Reference ID: {request_id}')
        send_telegram_notification('notify_email_error', error_lines)
        return False


def send_email_via_settings(subject, html_body, text_body, recipients, settings, attachments=None, reply_to=None, error_context=None, request_id=None, extra_error_payload=None):
    recipients = [sanitize_email(addr) for addr in (recipients or []) if sanitize_email(addr)]
    context_key = sanitize_text(error_context or 'notification', 64) or 'notification'
    subject_summary = sanitize_text(subject, 150) if subject else '(none)'

    if not recipients:
        app.logger.info('Skipping email send because no valid recipients were supplied for %s', subject)
        log_email_error(
            request_id=request_id,
            email_type=context_key,
            subject=subject_summary,
            recipients=[],
            error_message='No valid recipients were supplied for this email.',
            error_payload={'reason': 'missing_recipients'}
        )
        send_telegram_notification(
            'notify_email_error',
            [
                '[Email] Delivery skipped',
                f'Context: {context_key}',
                f'Subject: {subject_summary}',
                'Reason: No valid recipients supplied.'
            ]
        )
        return False

    # Check which email provider to use
    email_provider = (settings.get('email_provider') or 'smtp').strip().lower()

    if email_provider == 'resend':
        return send_email_via_resend(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            recipients=recipients,
            settings=settings,
            attachments=attachments,
            reply_to=reply_to,
            error_context=error_context,
            request_id=request_id,
            extra_error_payload=extra_error_payload
        )

    # Default to SMTP
    sender_email = sanitize_email(settings.get('sender_email')) or 'no-reply@example.com'
    sender_name = sanitize_text(settings.get('sender_name') or 'Notifications', 150)
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = formataddr((sender_name, sender_email))
    message['To'] = ', '.join(recipients)

    if reply_to and sanitize_email(reply_to):
        message['Reply-To'] = sanitize_email(reply_to)

    safe_text = text_body or 'Please view this email in an HTML compatible client.'
    message.set_content(safe_text)
    if html_body:
        message.add_alternative(html_body, subtype='html')

    # Gmail has a 25MB limit; we'll use 20MB as a safe threshold
    MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024  # 20MB total
    MAX_SINGLE_FILE_SIZE = 10 * 1024 * 1024  # 10MB per file
    attachment_manifest = []
    total_attachment_size = 0
    
    for attachment in attachments or []:
        filename = attachment.get('filename') or 'attachment'
        mimetype = attachment.get('mime_type') or 'application/octet-stream'
        path = attachment.get('absolute_path')
        remote_url = attachment.get('remote_url')
        payload = None
        file_size = None

        if path and os.path.isfile(path):
            try:
                file_size = os.path.getsize(path)
                with open(path, 'rb') as file_handle:
                    payload = file_handle.read()
            except OSError:
                app.logger.warning('Unable to read attachment from %s', path)
                payload = None
        elif remote_url:
            try:
                response = requests.get(remote_url, timeout=20)
                response.raise_for_status()
                payload = response.content
                file_size = len(payload)
            except RequestException:
                app.logger.warning('Unable to download remote attachment %s', remote_url)
                payload = None

        if not payload:
            continue

        file_size = file_size or len(payload)

        if file_size > MAX_SINGLE_FILE_SIZE:
            app.logger.warning('Skipping attachment %s (%.2f MB) - exceeds single file limit.', filename, file_size / (1024 * 1024))
            continue

        if total_attachment_size + file_size > MAX_ATTACHMENT_SIZE:
            app.logger.warning('Skipping attachment %s - would exceed total attachment limit.', filename)
            continue

        maintype, _, subtype = mimetype.partition('/')
        if not subtype:
            maintype = 'application'
            subtype = 'octet-stream'

        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
        attachment_manifest.append({'filename': filename, 'mime_type': mimetype, 'size': file_size, 'remote': bool(remote_url)})
        total_attachment_size += file_size

    host = (settings.get('smtp_host') or '').strip()
    port = int(settings.get('smtp_port') or 0)
    username = settings.get('smtp_username') or ''
    password = settings.get('smtp_password') or ''
    use_ssl = bool(int(settings.get('use_ssl') or 0))
    use_tls = bool(int(settings.get('use_tls') or 0))

    if not host or not port:
        app.logger.warning('SMTP configuration incomplete; host or port missing. Email not sent.')
        payload = dict(extra_error_payload or {})
        payload.update({
            'reason': 'missing_smtp_configuration',
            'host': host,
            'port': port,
            'use_ssl': use_ssl,
            'use_tls': use_tls
        })
        log_email_error(
            request_id=request_id,
            email_type=context_key,
            subject=subject_summary,
            recipients=recipients,
            error_message='SMTP configuration incomplete; host or port missing.',
            error_payload=payload
        )
        send_telegram_notification(
            'notify_email_error',
            [
                '[Email] Delivery failed',
                f'Context: {context_key}',
                f'Subject: {subject_summary}',
                'Reason: SMTP configuration missing host or port.'
            ]
        )
        return False

    smtp = None
    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()

        if username or password:
            smtp.login(username or sender_email, password)

        # Apply socket-level timeout for the entire send operation (covers data + reply wait)
        if smtp.sock:
            smtp.sock.settimeout(30)

        smtp.send_message(message)
        success_lines = [
            '[Email] Delivery succeeded',
            f'Context: {context_key}',
            f'Subject: {subject_summary}',
            f'Recipients: {", ".join(recipients)}',
            f'Attachments: {len(attachment_manifest)}'
        ]
        if request_id:
            success_lines.append(f'Reference ID: {request_id}')
        send_telegram_notification('notify_email_success', success_lines)
        return True
    except Exception as exc:
        app.logger.exception('Failed to send email notification with subject %s', subject)
        payload = dict(extra_error_payload or {})
        payload.update({
            'host': host,
            'port': port,
            'use_ssl': use_ssl,
            'use_tls': use_tls,
            'has_username': bool(username),
            'attachment_count': len(attachment_manifest)
        })
        raw_error = ''.join(traceback.format_exception_only(type(exc), exc)).strip() or str(exc)
        error_details = sanitize_text(raw_error, 180)
        log_email_error(
            request_id=request_id,
            email_type=context_key,
            subject=subject_summary,
            recipients=recipients,
            error_message=error_details,
            error_payload=payload
        )
        error_lines = [
            '[Email] Delivery failed',
            f'Context: {context_key}',
            f'Subject: {subject_summary}',
            f'Error: {error_details}',
            f'Recipients: {", ".join(recipients) if recipients else "(none)"}'
        ]
        if request_id:
            error_lines.append(f'Reference ID: {request_id}')
        send_telegram_notification('notify_email_error', error_lines)
        return False
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                pass


def update_email_settings(payload):
    if not payload:
        raise ValueError('No data provided.')

    sender_name = sanitize_text(payload.get('sender_name'), 150)
    sender_email = sanitize_email(payload.get('sender_email'))
    if not sender_name or not sender_email:
        raise ValueError('Sender name and sender email are required.')

    raw_recipients = payload.get('admin_recipients') or payload.get('admin_recipient_list') or ''
    recipient_list = parse_recipient_list(raw_recipients)
    if not recipient_list:
        raise ValueError('At least one admin recipient email is required.')

    reply_to = sanitize_email(payload.get('reply_to'))
    
    # Email provider selection (smtp or resend)
    email_provider = (payload.get('email_provider') or 'smtp').strip().lower()
    if email_provider not in ('smtp', 'resend'):
        email_provider = 'smtp'

    # SMTP settings
    smtp_host = sanitize_text(payload.get('smtp_host'), 150)
    smtp_port_value = payload.get('smtp_port') or 0
    try:
        smtp_port = int(smtp_port_value)
    except (TypeError, ValueError):
        smtp_port = 0
    
    # Only require SMTP port if using SMTP provider
    if email_provider == 'smtp' and smtp_port <= 0:
        raise ValueError('SMTP port must be greater than zero when using SMTP.')

    smtp_username = sanitize_text(payload.get('smtp_username'), 150)
    smtp_password_raw = payload.get('smtp_password')
    use_tls = 1 if str(payload.get('use_tls')).strip().lower() in {'1', 'true', 'yes', 'on'} else 0
    use_ssl = 1 if str(payload.get('use_ssl')).strip().lower() in {'1', 'true', 'yes', 'on'} else 0
    is_active = 1 if str(payload.get('is_active')).strip().lower() in {'1', 'true', 'yes', 'on'} else 0

    # Resend API key
    resend_api_key_raw = payload.get('resend_api_key')

    existing = fetch_email_settings()
    encrypted_password = existing.get('smtp_password_encrypted')
    if smtp_password_raw:
        encrypted_password = encrypt_secret(smtp_password_raw)

    encrypted_resend_key = existing.get('resend_api_key_encrypted')
    if resend_api_key_raw:
        encrypted_resend_key = encrypt_secret(resend_api_key_raw)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE email_settings
        SET sender_name=%s,
            sender_email=%s,
            admin_recipients=%s,
            reply_to=%s,
            email_provider=%s,
            smtp_host=%s,
            smtp_port=%s,
            smtp_username=%s,
            smtp_password_encrypted=%s,
            use_tls=%s,
            use_ssl=%s,
            is_active=%s,
            resend_api_key_encrypted=%s
        WHERE id = 1
        """,
        (
            sender_name,
            sender_email,
            ','.join(recipient_list),
            reply_to or None,
            email_provider,
            smtp_host,
            smtp_port or 0,
            smtp_username or None,
            encrypted_password,
            use_tls,
            use_ssl,
            is_active,
            encrypted_resend_key
        )
    )
    conn.commit()
    cursor.close()
    conn.close()

    updated = fetch_email_settings()
    updated['smtp_password'] = ''  # never echo password back to client
    updated['resend_api_key'] = ''  # never echo API key back to client
    updated.pop('smtp_password_encrypted', None)
    updated.pop('resend_api_key_encrypted', None)
    return updated


def prepare_request_payload(raw_payload, remote_addr=None):
    payload = raw_payload or {}
    request_type = (payload.get('request_type') or '').strip().lower()
    allowed_types = {'service', 'job', 'general'}
    if request_type not in allowed_types:
        raise ValueError('Unsupported request type provided.')

    name = sanitize_text(payload.get('name'), 150)
    if not name:
        raise ValueError('Full name is required.')

    email = sanitize_email(payload.get('email'))
    phone = sanitize_phone(payload.get('phone'))
    subject = sanitize_text(payload.get('subject'), 255) or ''
    service_name = sanitize_text(payload.get('service') or payload.get('service_name'), 255)
    job_position = sanitize_text(payload.get('job_position') or payload.get('position'), 255)
    message = sanitize_text(payload.get('message'))
    context_page = sanitize_text(payload.get('context_page'), 255)
    source = sanitize_text(payload.get('source'), 100) or 'web'

    if request_type == 'service':
        if not service_name:
            raise ValueError('Please select the service you need help with.')
        if not phone:
            raise ValueError('Phone number is required for service requests.')
        subject = subject or f'Service request: {service_name}'
    elif request_type == 'job':
        if not job_position:
            raise ValueError('Please choose the position you are applying for.')
        if not email:
            raise ValueError('Email address is required for job applications.')
        subject = subject or f'Job application: {job_position}'
    else:
        subject = subject or 'New enquiry received'

    metadata = {
        'origin_ip': remote_addr,
        'raw': {key: value for key, value in payload.items() if key not in {'csrf_token'}},
    }

    return {
        'request_type': request_type,
        'name': name,
        'email': email,
        'phone': phone,
        'subject': subject,
        'service_name': service_name,
        'job_position': job_position,
        'message': message,
        'context_page': context_page,
        'source': source,
        'metadata': metadata
    }


def store_request(clean_payload, uploaded_files=None, status_override=None):
    attachments = []
    conn = None
    cursor = None
    db_engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    pg_mode = db_engine == 'postgres'

    def rewind_file_storage(file_storage):
        stream = getattr(file_storage, 'stream', None)
        if hasattr(stream, 'seek'):
            try:
                stream.seek(0)
            except (OSError, ValueError):
                pass

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        status_value = status_override if status_override in REQUEST_STATUSES else 'pending'

        metadata_json = json.dumps(clean_payload.get('metadata') or {})
        ref_id = generate_unique_ref_id(cursor)
        request_insert_sql = """
            INSERT INTO requests (
                ref_id, request_type, name, email, phone, subject, service_name, job_position,
                context_page, status, source, message, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        if pg_mode:
            request_insert_sql += " RETURNING id"

        cursor.execute(
            request_insert_sql,
            (
                ref_id,
                clean_payload['request_type'],
                clean_payload['name'],
                clean_payload.get('email') or None,
                clean_payload.get('phone') or None,
                clean_payload.get('subject') or None,
                clean_payload.get('service_name') or None,
                clean_payload.get('job_position') or None,
                clean_payload.get('context_page') or None,
                status_value,
                clean_payload.get('source') or None,
                clean_payload.get('message') or None,
                metadata_json
            )
        )
        if pg_mode:
            row = cursor.fetchone()
            if not row:
                raise RuntimeError('Failed to retrieve inserted request ID (postgres).')
            request_id = row[0]
        else:
            request_id = cursor.lastrowid

        for uploaded in uploaded_files or []:
            if not uploaded or not uploaded.filename:
                continue
            if uploaded.content_length and uploaded.content_length > MAX_ATTACHMENT_BYTES:
                raise ValueError('Attachment is too large. Maximum allowed size is 10MB.')

            rewind_file_storage(uploaded)
            file_size = getattr(uploaded, 'content_length', None) or 0
            stored_path = ''
            absolute_path = None
            remote_url = ''

            _, ext = os.path.splitext(uploaded.filename)
            ext = (ext or '').lower().lstrip('.')
            cloud_resource_type = 'image' if ext in IMAGE_EXTENSIONS else 'raw'

            cloud_result = upload_file_to_cloudinary(
                uploaded,
                'requests',
                resource_type=cloud_resource_type,
                return_result=True
            )
            if isinstance(cloud_result, dict) and cloud_result.get('secure_url'):
                stored_path = cloud_result.get('secure_url')
                remote_url = stored_path
                file_size = cloud_result.get('bytes') or file_size or 0
            else:
                rewind_file_storage(uploaded)
                saved_path = save_file(uploaded, REQUEST_UPLOAD_FOLDER, ATTACHMENT_EXTENSIONS)
                stored_path = saved_path
                absolute_path = os.path.join(app.static_folder, saved_path.replace('/', os.sep))
                if os.path.exists(absolute_path):
                    try:
                        file_size = os.path.getsize(absolute_path)
                    except OSError:
                        file_size = file_size or 0

            if not stored_path:
                continue

            insert_file_sql = """
                INSERT INTO request_files (request_id, original_filename, stored_path, mime_type, file_size_bytes)
                VALUES (%s, %s, %s, %s, %s)
            """
            if pg_mode:
                insert_file_sql += " RETURNING id"

            cursor.execute(
                insert_file_sql,
                (
                    request_id,
                    uploaded.filename,
                    stored_path,
                    uploaded.mimetype or 'application/octet-stream',
                    file_size
                )
            )
            if pg_mode:
                attachment_row = cursor.fetchone()
                attachment_id = attachment_row[0] if attachment_row else None
            else:
                attachment_id = cursor.lastrowid
            attachments.append({
                'id': attachment_id,
                'filename': uploaded.filename,
                'stored_path': stored_path,
                'mime_type': uploaded.mimetype or 'application/octet-stream',
                'file_size_bytes': file_size,
                'absolute_path': absolute_path,
                'remote_url': remote_url
            })

        conn.commit()

        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM requests WHERE id = %s", (request_id,))
        request_record = cursor.fetchone()
        return request_record, attachments
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def generate_request_context(request_row):
    metadata_raw = request_row.get('metadata') if request_row else {}
    metadata = {}
    if isinstance(metadata_raw, str):
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata = {}
    elif isinstance(metadata_raw, dict):
        metadata = metadata_raw

    created_at = request_row.get('created_at')
    updated_at = request_row.get('updated_at')

    return {
        'id': request_row.get('id'),
        'ref_id': request_row.get('ref_id'),
        'request_type': request_row.get('request_type'),
        'name': request_row.get('name'),
        'email': request_row.get('email'),
        'phone': request_row.get('phone'),
        'subject': request_row.get('subject'),
        'service_name': request_row.get('service_name'),
        'job_position': request_row.get('job_position'),
        'status': request_row.get('status'),
        'status_label': get_status_label(request_row.get('status')),
        'message': request_row.get('message'),
        'created_at': format_friendly_datetime(created_at),
        'updated_at': format_friendly_datetime(updated_at),
        'context_page': request_row.get('context_page'),
        'source': request_row.get('source'),
        'metadata': metadata,
        'service_flow': metadata.get('service_flow') if isinstance(metadata, dict) else None,
        'email_sent_admin': bool(request_row.get('email_sent_admin')),
        'email_sent_user': bool(request_row.get('email_sent_user')),
    }


def build_request_summary_text(context):
    lines = []
    if context.get('ref_id'):
        lines.append(f"Reference: {context.get('ref_id')}")
    lines.append(f"Request Type: {context.get('request_type')}")
    lines.append(f"Name: {context.get('name')}")
    if context.get('email'):
        lines.append(f"Email: {context.get('email')}")
    if context.get('phone'):
        lines.append(f"Phone: {context.get('phone')}")
    if context.get('service_name'):
        lines.append(f"Service: {context.get('service_name')}")
    if context.get('job_position'):
        lines.append(f"Position: {context.get('job_position')}")
    if context.get('context_page'):
        lines.append(f"Page: {context.get('context_page')}")
    if context.get('source'):
        lines.append(f"Source: {context.get('source')}")

    service_flow = context.get('service_flow') or {}
    payment_info = service_flow.get('payment') if isinstance(service_flow, dict) else {}
    if isinstance(payment_info, dict) and payment_info:
        payment_status = payment_info.get('status_label')
        payment_type = payment_info.get('payment_type')
        payment_intent_id = payment_info.get('stripe_payment_intent_id')
        checkout_session_id = payment_info.get('stripe_checkout_session_id')
        transaction_id = payment_info.get('transaction_id')
        if payment_status or payment_type or payment_intent_id or checkout_session_id or transaction_id:
            lines.append('')
            lines.append('Payment:')
            if payment_status:
                lines.append(f"- Status: {payment_status}")
            if payment_type:
                lines.append(f"- Method: {payment_type}")
            if transaction_id:
                lines.append(f"- Transaction ID: {transaction_id}")
            if checkout_session_id:
                lines.append(f"- Checkout Session ID: {checkout_session_id}")
            if payment_intent_id:
                lines.append(f"- Payment Intent ID: {payment_intent_id}")

    lines.append('')
    lines.append('Message:')
    lines.append(context.get('message') or '(no message provided)')
    return '\n'.join(lines)


def parse_order_for_email(service_flow):
    """
    Parse service_flow data into structured order items for email templates.
    Returns: (order_items, order_totals, schedule_info, customer_notes, location_info, assigned_base_info)
    Each order_item has: service_name, detail (option_label), price, is_survey
    """
    if not service_flow or not isinstance(service_flow, dict):
        return [], None, None, None, None, None

    selections = service_flow.get('selections') or []
    totals = service_flow.get('totals') or {}
    schedule = service_flow.get('schedule') or {}
    notes = service_flow.get('notes') or ''
    customer = service_flow.get('customer') or {}
    travel = service_flow.get('travel') or {}

    # Build order items list - each selection has service_name, option_label, price
    order_items = []
    for sel in selections:
        service_name = sel.get('service_name') or 'Service'
        option_label = sel.get('option_label') or ''
        is_survey = sel.get('is_survey_request', False)

        # Add item with service_name and detail (option_label)
        order_items.append({
            'service_name': service_name,
            'detail': option_label,
            'price': sel.get('price'),
            'is_survey': is_survey
        })

    # Build totals dict — map both old-style keys and prepare_service_booking keys
    order_totals = None
    if order_items:
        # Discount: support both discount_applied/discount_percent AND prebook_discount_amount/prebook_discount_percent
        discount_amount = totals.get('discount_amount') or totals.get('prebook_discount_amount') or 0
        discount_percent = totals.get('discount_percent') or totals.get('prebook_discount_percent') or 0
        discount_applied = bool(totals.get('discount_applied')) or (discount_amount and discount_amount > 0)
        # Final total: use payable_total (post-discount) if available, else total_with_travel, else amount
        final_total = totals.get('payable_total') if totals.get('payable_total') is not None else (
            totals.get('total_with_travel') if totals.get('total_with_travel') is not None else totals.get('amount')
        )
        order_totals = {
            'discount_applied': discount_applied,
            'discount_percent': discount_percent,
            'discount_amount': discount_amount,
            'travel_fee': totals.get('travel_fee'),
            'final_total': final_total,
            'is_survey_request': totals.get('is_survey_request', False),
            'has_custom_pricing': totals.get('has_custom_pricing', False)
        }

    # Build schedule info string
    schedule_info = None
    if schedule.get('preferred_date') or schedule.get('preferred_time') or schedule.get('contract_frequency'):
        parts = []
        if schedule.get('preferred_date'):
            parts.append(str(schedule.get('preferred_date')))
        if schedule.get('preferred_time'):
            parts.append(f"at {schedule.get('preferred_time')}")
        if schedule.get('contract_frequency'):
            freq_label = format_contract_frequency_label(schedule.get('contract_frequency'))
            if freq_label:
                parts.append(f"({freq_label})")
        schedule_info = ' '.join(parts)

    # Build location info for email
    location_info = None
    customer_address = customer.get('address')
    customer_lat = travel.get('customer_lat')
    customer_lng = travel.get('customer_lng')
    customer_postcode = travel.get('customer_postcode') or customer.get('postcode')
    
    # Use postcode as fallback for address if address is empty
    display_address = customer_address or customer_postcode
    
    if display_address or customer_lat:
        location_info = {
            'address': display_address,
            'postcode': customer_postcode if customer_postcode != display_address else None,
            'lat': customer_lat,
            'lng': customer_lng,
            'map_url': None
        }
        # Build Google Maps URL
        if customer_lat and customer_lng:
            location_info['map_url'] = f"https://www.google.com/maps/search/?api=1&query={customer_lat},{customer_lng}"
        elif display_address:
            import urllib.parse
            location_info['map_url'] = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(display_address)}"

    # Build assigned base info for admin emails
    assigned_base_info = None
    if travel.get('base_name') or travel.get('base_id'):
        assigned_base_info = {
            'base_name': travel.get('base_name'),
            'base_postcode': travel.get('base_postcode'),
            'distance_miles': travel.get('distance_miles'),
            'travel_time_minutes': travel.get('travel_time_minutes'),
            'is_extended_coverage': travel.get('is_extended_coverage', False)
        }

    return order_items, order_totals, schedule_info, notes if notes else None, location_info, assigned_base_info


def process_request_submission(payload, uploaded_files=None, remote_addr=None, extra_metadata=None, status_override=None):
    clean_payload = prepare_request_payload(payload, remote_addr)
    if extra_metadata:
        metadata = clean_payload.get('metadata') or {}
        metadata.update(extra_metadata)
        clean_payload['metadata'] = metadata
    request_record, attachments = store_request(clean_payload, uploaded_files, status_override=status_override)
    queue_request_notifications(request_record, attachments)

    log_analytics_event('request_submission', {
        'request_type': clean_payload.get('request_type'),
        'source': clean_payload.get('source'),
        'service_name': clean_payload.get('service_name'),
        'job_position': clean_payload.get('job_position')
    })

    response_payload = {
        'message': 'Your request has been received.',
        'request_id': request_record.get('id'),
        'reference': request_record.get('ref_id'),
        'status': request_record.get('status'),
        'emails': {'queued': True}
    }
    return response_payload


def route_contract_booking_to_crm(prepared, submission, service_request_id=None):
    prepared = prepared or {}
    submission = submission or {}
    service_metadata = prepared.get('service_metadata') or {}
    selections = prepared.get('selections') or []
    service_names = [item.get('service_name') for item in selections if item.get('service_name')]
    unique_names = []
    for name in service_names:
        if name not in unique_names:
            unique_names.append(name)
    notes = sanitize_text(prepared.get('notes'), 1000)
    schedule = service_metadata.get('schedule') or {}
    preferred_date = sanitize_text(schedule.get('preferred_date'), 40)
    preferred_time = sanitize_text(schedule.get('preferred_time'), 40)
    contract_frequency = format_contract_frequency_label(schedule.get('contract_frequency'))
    lead_lines = [
        'Contract service lead captured from booking flow.',
        f"Linked service request ref: {submission.get('reference') or ''}".strip(),
        f"Requested services: {', '.join(unique_names) if unique_names else prepared.get('primary_service_name') or 'N/A'}",
    ]
    if preferred_date or preferred_time:
        lead_lines.append(f"Preferred schedule: {preferred_date or 'Flexible'} {preferred_time or ''}".strip())
    if contract_frequency:
        lead_lines.append(f"Contract frequency: {contract_frequency}")
    if notes:
        lead_lines.append(f"Notes: {notes}")

    payload = {
        'request_type': 'general',
        'name': prepared.get('customer_name') or (prepared.get('customer_bundle') or {}).get('name'),
        'email': prepared.get('customer_email') or (prepared.get('customer_bundle') or {}).get('email'),
        'phone': (prepared.get('customer_bundle') or {}).get('phone'),
        'subject': 'Contract cleaning lead (from service booking)',
        'service_name': prepared.get('primary_service_name') or 'Contract service enquiry',
        'message': '\n'.join([line for line in lead_lines if line]),
        'context_page': '/services',
        'source': 'crm_contract_booking'
    }

    clean_payload = prepare_request_payload(payload)
    metadata = clean_payload.get('metadata') or {}
    metadata.update({
        'crm_origin': 'contract_service_booking',
        'linked_request_id': submission.get('request_id'),
        'linked_reference': submission.get('reference'),
        'linked_service_request_id': service_request_id,
        'service_flow': service_metadata
    })
    clean_payload['metadata'] = metadata

    request_record, _ = store_request(clean_payload, uploaded_files=None)
    return request_record.get('id') if request_record else None


def persist_contract_record(prepared, submission, service_request_id=None):
    prepared = prepared or {}
    if not prepared.get('has_contract_service'):
        return None

    service_metadata = prepared.get('service_metadata') or {}
    schedule = service_metadata.get('schedule') or {}
    contract_info = service_metadata.get('contract') or {}
    frequency = normalize_contract_frequency(schedule.get('contract_frequency'))
    if not frequency:
        return None

    signer_name = sanitize_text(
        contract_info.get('signer_name') or (prepared.get('customer_bundle') or {}).get('name') or prepared.get('customer_name'),
        150
    )
    terms_agreed = bool(str_to_bool(contract_info.get('terms_agreed') or contract_info.get('agreed')))

    preferred_date = parse_preferred_date(schedule.get('preferred_date'))
    if not preferred_date:
        preferred_date = date.today()
    while preferred_date < date.today():
        next_date = advance_contract_date(preferred_date, frequency)
        if not isinstance(next_date, date) or next_date == preferred_date:
            break
        preferred_date = next_date

    weekday_names = set(calendar.day_name)
    anchored_service_day = sanitize_text(contract_info.get('service_day'), 20)
    anchored_service_day = anchored_service_day.title() if anchored_service_day else ''
    if anchored_service_day not in weekday_names:
        anchored_service_day = ''
    service_day = anchored_service_day or (calendar.day_name[preferred_date.weekday()] if isinstance(preferred_date, date) else calendar.day_name[date.today().weekday()])

    next_reminder_at = calculate_next_reminder_at(preferred_date, frequency)
    metadata = {
        'service_flow': service_metadata,
        'agreement': contract_info,
        'linked_request_reference': submission.get('reference') if isinstance(submission, dict) else None
    }

    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    try:
        insert_sql = """
            INSERT INTO contracts (
                request_id, service_request_id, customer_name, customer_email, customer_phone,
                service_name, frequency, preferred_time, next_service_date, next_reminder_at,
                signer_name, terms_agreed, service_day, reminder_enabled, status, metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        if 'postgres' in engine:
            insert_sql += " RETURNING id"

        cursor.execute(
            insert_sql,
            (
                submission.get('request_id') if isinstance(submission, dict) else None,
                service_request_id,
                (prepared.get('customer_bundle') or {}).get('name') or prepared.get('customer_name'),
                (prepared.get('customer_bundle') or {}).get('email') or prepared.get('customer_email'),
                (prepared.get('customer_bundle') or {}).get('phone'),
                prepared.get('primary_service_name') or 'Contract service',
                frequency,
                sanitize_text(schedule.get('preferred_time'), 32),
                preferred_date,
                next_reminder_at,
                signer_name,
                (True if 'postgres' in engine else (1 if terms_agreed else 0)),
                service_day,
                1 if 'postgres' not in engine else True,
                'active',
                json.dumps(metadata)
            )
        )

        contract_id = cursor.fetchone()[0] if 'postgres' in engine else cursor.lastrowid
        conn.commit()
        return contract_id
    finally:
        cursor.close()
        conn.close()


def fetch_contract_records(limit=250):
    ensure_travel_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT id, request_id, service_request_id, customer_name, customer_email, customer_phone,
             service_name, frequency, preferred_time, signer_name, terms_agreed, service_day,
             next_service_date, next_reminder_at,
               last_reminder_sent_at, reminder_enabled, status, created_at
        FROM contracts
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (max(1, min(int(limit or 250), 1000)),)
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    for row in rows:
        for key in ('next_service_date', 'next_reminder_at', 'last_reminder_sent_at', 'created_at'):
            value = row.get(key)
            if isinstance(value, datetime):
                row[key] = value.isoformat()
            elif isinstance(value, date):
                row[key] = value.isoformat()
    return rows


def process_due_contract_reminders(limit=25):
    ensure_travel_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()

    try:
        cursor.execute(
            """
            SELECT *
            FROM contracts
            WHERE status = 'active'
              AND reminder_enabled = %s
                            AND next_service_date = %s
                        ORDER BY next_service_date ASC, id ASC
            LIMIT %s
            """,
                        ((True if engine == 'postgres' else 1), tomorrow, max(1, min(int(limit or 25), 200)))
        )
        due_contracts = cursor.fetchall()
        if not due_contracts:
            return {'processed': 0, 'sent': 0}

        settings = fetch_email_settings()
        sent_count = 0
        processed_count = 0
        for contract in due_contracts:
            processed_count += 1
            recipient = sanitize_email(contract.get('customer_email'))
            if recipient and settings and int(settings.get('is_active') or 0):
                frequency_label = format_contract_frequency_label(contract.get('frequency')) or 'Recurring'
                service_date = contract.get('next_service_date')
                service_time = contract.get('preferred_time') or '09:00'
                subject = f"Reminder: {contract.get('service_name') or 'Cleaning service'} tomorrow"
                html_body = (
                    f"<p>Hello {contract.get('customer_name') or 'there'},</p>"
                    f"<p>This is your reminder for your {frequency_label.lower()} contract service <strong>{contract.get('service_name') or 'Cleaning service'}</strong>.</p>"
                    f"<p><strong>Scheduled for:</strong> {service_date} at {service_time}</p>"
                    f"<p>If you need any changes, please reply to this email.</p>"
                )
                text_body = (
                    f"Hello {contract.get('customer_name') or 'there'},\n\n"
                    f"Reminder for your {frequency_label.lower()} contract service ({contract.get('service_name') or 'Cleaning service'})\n"
                    f"Scheduled for: {service_date} at {service_time}\n\n"
                    "Need changes? Reply to this email."
                )
                if send_email_via_settings(
                    subject=subject,
                    html_body=html_body,
                    text_body=text_body,
                    recipients=[recipient],
                    settings=settings,
                    attachments=None,
                    reply_to=settings.get('reply_to') or settings.get('sender_email'),
                    error_context='contract_reminder',
                    request_id=contract.get('request_id')
                ):
                    sent_count += 1

            next_service_date = contract.get('next_service_date')
            if isinstance(next_service_date, datetime):
                next_service_date = next_service_date.date()
            if isinstance(next_service_date, str):
                next_service_date = parse_preferred_date(next_service_date)
            next_service_date = advance_contract_date(next_service_date or date.today(), contract.get('frequency'))
            next_reminder_at = calculate_next_reminder_at(next_service_date, contract.get('frequency'))
            next_service_day = calendar.day_name[next_service_date.weekday()] if isinstance(next_service_date, date) else ''

            cursor.execute(
                """
                UPDATE contracts
                SET next_service_date=%s,
                    next_reminder_at=%s,
                    last_reminder_sent_at=%s,
                    service_day=%s
                WHERE id=%s
                """,
                (next_service_date, next_reminder_at, now, next_service_day, contract.get('id'))
            )

        conn.commit()
        return {'processed': processed_count, 'sent': sent_count}
    finally:
        cursor.close()
        conn.close()


def maybe_process_due_contract_reminders():
    global _contract_reminder_last_run
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with _contract_reminder_lock:
        if (now - _contract_reminder_last_run).total_seconds() < 300:
            return {'processed': 0, 'sent': 0, 'skipped': True}
        _contract_reminder_last_run = now
    try:
        return process_due_contract_reminders(limit=50)
    except Exception:
        app.logger.exception('Automated contract reminder processing failed')
        return {'processed': 0, 'sent': 0, 'error': True}


def send_request_notifications(request_record, attachments=None):
    result = {'admin_sent': False, 'user_sent': False}
    if not request_record:
        return result

    settings = fetch_email_settings()
    if not settings or not int(settings.get('is_active') or 0):
        app.logger.info('Email settings inactive; skipping notifications for request %s', request_record.get('id'))
        return result

    context = generate_request_context(request_record)
    service_flow = context.get('service_flow') or {}
    service_totals = service_flow.get('totals') or {}
    service_selections = service_flow.get('selections') or []
    is_survey_request = bool(service_totals.get('is_survey_request') or any(item.get('is_survey_request') for item in service_selections))
    context['is_survey_request'] = is_survey_request
    context['status_label'] = context.get('status_label') or get_status_label(context.get('status'))
    user_email = sanitize_email(context.get('email'))
    attachments = attachments or []

    # Parse order items for email templates
    order_items, order_totals, schedule_info, customer_notes, location_info, assigned_base_info = parse_order_for_email(service_flow)

    # Build absolute logo URL for email templates
    _site = fetch_site_settings() or {}
    _logo_path = (_site.get('logo_path') or '').strip()
    logo_url = _build_logo_url(_logo_path) or None

    try:
        admin_html = render_template(
            'emails/request_admin.html',
            request=context,
            attachments=attachments,
            order_items=order_items,
            order_totals=order_totals,
            schedule_info=schedule_info,
            customer_notes=customer_notes,
            location_info=location_info,
            assigned_base_info=assigned_base_info,
            logo_url=logo_url
        )
    except Exception:
        app.logger.exception('Failed to render admin email template.')
        admin_html = None
    admin_text = build_request_summary_text(context)

    admin_subject = f"New {context.get('request_type', 'service')} request from {context.get('name')}"
    if is_survey_request:
        admin_subject = f"Survey request – action needed ({context.get('name')})"
        admin_text = "Survey request – pricing TBD. No payment taken. Please arrange a site visit.\n\n" + admin_text

    result['admin_sent'] = send_email_via_settings(
        subject=admin_subject,
        html_body=admin_html,
        text_body=admin_text,
        recipients=settings.get('admin_recipient_list'),
        settings=settings,
        attachments=attachments,
        reply_to=user_email or settings.get('reply_to'),
        error_context='request_admin_notification',
        request_id=request_record.get('id'),
        extra_error_payload={
            'ref_id': context.get('ref_id'),
            'request_type': context.get('request_type')
        }
    )

    if user_email:
        try:
            user_html = render_template(
                'emails/request_user.html',
                request=context,
                order_items=order_items,
                order_totals=order_totals,
                schedule_info=schedule_info,
                customer_notes=customer_notes,
                location_info=location_info,
                logo_url=logo_url
            )
        except Exception:
            app.logger.exception('Failed to render user confirmation email template.')
            user_html = None
        user_text = (
            "Thanks for reaching out! We'll get back to you soon.\n\n" +
            build_request_summary_text(context)
        )
        service_name_for_subject = context.get('service_name') or context.get('request_type') or 'service'
        payment_info_for_subject = service_flow.get('payment') or {}
        is_paid_for_subject = bool(payment_info_for_subject.get('is_paid'))
        if is_paid_for_subject:
            user_subject = f"Booking confirmed – {service_name_for_subject} (Paid)"
        else:
            user_subject = f"We received your request – {service_name_for_subject}"
        result['user_sent'] = send_email_via_settings(
            subject=user_subject,
            html_body=user_html,
            text_body=user_text,
            recipients=[user_email],
            settings=settings,
            attachments=None,
            reply_to=settings.get('reply_to') or settings.get('sender_email'),
            error_context='request_user_notification',
            request_id=request_record.get('id'),
            extra_error_payload={
                'ref_id': context.get('ref_id'),
                'request_type': context.get('request_type')
            }
        )

    update_request_email_flags(request_record.get('id'), admin_sent=result['admin_sent'], user_sent=result['user_sent'])
    request_record['email_sent_admin'] = 1 if result['admin_sent'] else 0
    request_record['email_sent_user'] = 1 if result['user_sent'] else 0
    return result


def queue_request_notifications(request_record, attachments=None):
    if not request_record:
        return

    record_copy = dict(request_record)
    attachments_copy = [dict(item) for item in (attachments or [])]

    def _deliver():
        try:
            with app.app_context():
                send_request_notifications(record_copy, attachments_copy)
        except Exception:
            app.logger.exception('Async notification delivery failed for request %s', record_copy.get('id'))

    try:
        EMAIL_EXECUTOR.submit(_deliver)
    except Exception:
        _deliver()


def update_request_email_flags(request_id, admin_sent=None, user_sent=None):
    if not request_id:
        return

    fields = []
    values = []
    if admin_sent is not None:
        fields.append('email_sent_admin = %s')
        values.append(1 if admin_sent else 0)
    if user_sent is not None:
        fields.append('email_sent_user = %s')
        values.append(1 if user_sent else 0)

    if not fields:
        return

    values.append(request_id)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE requests SET {', '.join(fields)} WHERE id = %s",
            values
        )
        conn.commit()
    except Exception:
        app.logger.exception('Failed to update email status flags for request %s', request_id)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def send_status_update_notifications(request_row, previous_status=None):
    result = {'admin_sent': False, 'user_sent': False}
    if not request_row:
        return result

    settings = fetch_email_settings()
    if not settings or not int(settings.get('is_active') or 0):
        return result

    context = generate_request_context(request_row)
    context['status_label'] = get_status_label(context.get('status'))
    context['previous_status'] = previous_status
    context['previous_status_label'] = get_status_label(previous_status)

    user_email = sanitize_email(context.get('email'))
    admin_payload = {
        'ref_id': context.get('ref_id'),
        'request_type': context.get('request_type'),
        'status': context.get('status'),
        'previous_status': previous_status
    }

    _site2 = fetch_site_settings() or {}
    _logo_path2 = (_site2.get('logo_path') or '').strip()
    logo_url2 = _build_logo_url(_logo_path2) or None

    try:
        admin_html = render_template('emails/request_status_admin.html', request=context, logo_url=logo_url2)
    except Exception:
        app.logger.exception('Failed to render admin status update email template.')
        admin_html = None

    previous_label = context.get('previous_status_label') or '—'
    admin_text = (
        f"Request {context.get('ref_id') or request_row.get('id')} status updated to {context.get('status_label')}\n"
        f"Previous status: {previous_label}\n\n"
        f"{build_request_summary_text(context)}"
    )

    result['admin_sent'] = send_email_via_settings(
        subject=f"Request {context.get('ref_id') or request_row.get('id')} status updated",
        html_body=admin_html,
        text_body=admin_text,
        recipients=settings.get('admin_recipient_list'),
        settings=settings,
        reply_to=settings.get('reply_to') or settings.get('sender_email'),
        error_context='status_update_admin',
        request_id=request_row.get('id'),
        extra_error_payload=admin_payload
    )

    if user_email:
        try:
            user_html = render_template('emails/request_status_user.html', request=context, logo_url=logo_url2)
        except Exception:
            app.logger.exception('Failed to render user status update email template.')
            user_html = None

        user_text = (
            f"Hi {context.get('name') or ''},\n\n"
            f"We've updated the status of your request to {context.get('status_label')}."
        )
        if previous_label and previous_status:
            user_text += f"\nPrevious status: {previous_label}."
        user_text += "\n\nWe'll follow up soon with any additional details."

        result['user_sent'] = send_email_via_settings(
            subject='Update on your request status',
            html_body=user_html,
            text_body=user_text,
            recipients=[user_email],
            settings=settings,
            reply_to=settings.get('reply_to') or settings.get('sender_email'),
            error_context='status_update_user',
            request_id=request_row.get('id'),
            extra_error_payload={
                'ref_id': context.get('ref_id'),
                'status': context.get('status'),
                'previous_status': previous_status
            }
        )

    log_analytics_event('request_status_update', {
        'request_id': request_row.get('id'),
        'ref_id': context.get('ref_id'),
        'status': context.get('status'),
        'previous_status': previous_status
    })

    return result


def send_quote_ready_notification(request_row, quote_amount):
    """Send email to customer when survey is complete and quote is ready."""
    result = {'user_sent': False, 'admin_sent': False}
    if not request_row:
        return result

    settings = fetch_email_settings()
    if not settings or not int(settings.get('is_active') or 0):
        app.logger.info('Email settings inactive; skipping quote ready notification for request %s', request_row.get('id'))
        return result

    context = generate_request_context(request_row)
    context['status_label'] = get_status_label(context.get('status'))
    context['quote_amount'] = quote_amount
    context['quote_formatted'] = f"£{quote_amount:.2f}" if quote_amount else 'TBD'

    user_email = sanitize_email(context.get('email'))
    if not user_email:
        app.logger.info('No user email for quote ready notification; skipping for request %s', request_row.get('id'))
        return result

    _site3 = fetch_site_settings() or {}
    _logo_path3 = (_site3.get('logo_path') or '').strip()
    logo_url3 = _build_logo_url(_logo_path3) or None

    # Build email content
    user_subject = f"Your quote is ready - {context.get('quote_formatted')}"
    user_text = (
        f"Hi {context.get('name') or 'there'},\n\n"
        f"Great news! Your survey is complete and we have your quote ready.\n\n"
        f"Quote Amount: {context.get('quote_formatted')}\n"
        f"Reference: {context.get('ref_id') or request_row.get('id')}\n\n"
        f"To proceed with your booking, please reply to this email or contact us to confirm and arrange payment.\n\n"
        f"We look forward to serving you!\n\n"
        f"Warm regards,\nClean Co. Team"
    )

    try:
        user_html = render_template('emails/quote_ready_user.html', request=context, logo_url=logo_url3)
    except Exception:
        app.logger.warning('Quote ready email template not found; using plain text.')
        user_html = None

    result['user_sent'] = send_email_via_settings(
        subject=user_subject,
        html_body=user_html,
        text_body=user_text,
        recipients=[user_email],
        settings=settings,
        reply_to=settings.get('reply_to') or settings.get('sender_email'),
        error_context='quote_ready_user',
        request_id=request_row.get('id'),
        extra_error_payload={
            'ref_id': context.get('ref_id'),
            'quote_amount': quote_amount
        }
    )

    # Notify admin as well
    admin_text = (
        f"Quote ready notification sent to customer.\n\n"
        f"Reference: {context.get('ref_id') or request_row.get('id')}\n"
        f"Customer: {context.get('name')} ({user_email})\n"
        f"Quote Amount: {context.get('quote_formatted')}\n"
        f"Status changed to: {context.get('status_label')}"
    )

    result['admin_sent'] = send_email_via_settings(
        subject=f"Quote sent to customer - {context.get('ref_id') or request_row.get('id')}",
        html_body=None,
        text_body=admin_text,
        recipients=settings.get('admin_recipient_list'),
        settings=settings,
        reply_to=settings.get('reply_to') or settings.get('sender_email'),
        error_context='quote_ready_admin',
        request_id=request_row.get('id'),
        extra_error_payload={
            'ref_id': context.get('ref_id'),
            'quote_amount': quote_amount
        }
    )

    log_analytics_event('quote_ready_sent', {
        'request_id': request_row.get('id'),
        'ref_id': context.get('ref_id'),
        'quote_amount': quote_amount,
        'user_email': user_email
    })

    return result


def get_db_connection():
    """Return a DB connection for the app's primary DB (mysql or postgres).

    This keeps existing code working by supporting `cursor(dictionary=True)`
    for both MySQL and Postgres.
    """

    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if engine == 'postgres':
        if psycopg2 is None:
            raise RuntimeError('psycopg2 is not installed (install psycopg2-binary)')

        pool = _get_pg_pool()
        if pool is not None:
            raw = pool.getconn()
        else:
            # Fallback: direct connect if pool unavailable
            dsn = (app.config.get('POSTGRES_URL') or '').strip()
            if not dsn:
                raise ValueError('DB_ENGINE=postgres but POSTGRES_URL is not configured')
            raw = psycopg2.connect(dsn)
            pool = None  # mark so close() doesn't try to return to pool

        class _PGConnWrapper:
            def __init__(self, conn, pool_ref):
                self._conn = conn
                self._pool = pool_ref
                self._closed = False

            def cursor(self, dictionary=False, *args, **kwargs):
                if dictionary:
                    if RealDictCursor is None:
                        return self._conn.cursor(*args, **kwargs)
                    return self._conn.cursor(cursor_factory=RealDictCursor)
                return self._conn.cursor(*args, **kwargs)

            def commit(self):
                return self._conn.commit()

            def close(self):
                if self._closed:
                    return
                self._closed = True
                if self._pool is not None:
                    try:
                        # Roll back any open transaction so the connection
                        # is clean when returned to the pool.
                        if self._conn.status != 0:  # 0 = STATUS_READY
                            self._conn.rollback()
                    except Exception:
                        pass
                    try:
                        self._conn.reset()
                    except Exception:
                        pass
                    try:
                        self._pool.putconn(self._conn)
                    except Exception:
                        try:
                            self._conn.close()
                        except Exception:
                            pass
                else:
                    try:
                        self._conn.close()
                    except Exception:
                        pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.close()
                return False

            def __getattr__(self, name):
                return getattr(self._conn, name)

        wrapped = _PGConnWrapper(raw, pool)
        # Track on Flask g so teardown can close any leaked connections
        try:
            if hasattr(g, '_db_conns'):
                g._db_conns.append(wrapped)
            else:
                g._db_conns = [wrapped]
        except RuntimeError:
            pass  # Outside request context — no g available
        return wrapped

    # Default: MySQL
    conn = mysql.connector.connect(
        host=app.config['MYSQL_HOST'],
        user=app.config['MYSQL_USER'],
        password=app.config['MYSQL_PASSWORD'],
        database=app.config['MYSQL_DB'],
        port=app.config.get('MYSQL_PORT', 3306)
    )
    return conn


def get_pg_connection():
    """Connect to the optional Render Postgres database using POSTGRES_URL."""
    dsn = (app.config.get('POSTGRES_URL') or '').strip()
    if not dsn:
        raise ValueError('POSTGRES_URL is not configured')
    if psycopg2 is None:
        raise RuntimeError('psycopg2 is not installed (install psycopg2-binary)')
    return psycopg2.connect(dsn)


def ensure_pg_analytics_table(pg_conn):
    """Ensure the minimal analytics table exists in Postgres (used only if ANALYTICS_DB=postgres)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics (
                id BIGSERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                event_data JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    pg_conn.commit()

def normalize_price_value(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_payment_option(value):
    normalized = (value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if normalized in {'prebook_save', 'prebook', 'pay_now', 'stripe', 'card'}:
        return PAYMENT_OPTION_PREBOOK
    if normalized in {'pay_in_person', 'in_person', 'cash', 'pay_after_service'}:
        return PAYMENT_OPTION_IN_PERSON
    return PAYMENT_OPTION_IN_PERSON


def normalize_stored_payment_type(value):
    normalized = (value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if normalized in {'stripe', 'prebook_save', 'prebook', 'pay_now', 'card'}:
        return 'stripe'
    return 'in_person'


def resolve_payment_status_label(payment_type, is_paid):
    normalized_type = normalize_stored_payment_type(payment_type)
    paid = bool(is_paid)
    if normalized_type == 'stripe':
        return 'Paid via Stripe' if paid else 'Stripe payment pending'
    return 'Paid in Person' if paid else 'Unpaid / Cash'


def resolve_payment_badge(payment_type, is_paid):
    normalized_type = normalize_stored_payment_type(payment_type)
    paid = bool(is_paid)
    if normalized_type == 'stripe':
        return 'stripe'
    return 'in_person_paid' if paid else 'in_person'


def money_to_minor_units(amount):
    decimal_amount = Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return int((decimal_amount * Decimal('100')).to_integral_value(rounding=ROUND_HALF_UP))


def calculate_prebook_discount(original_total):
    if original_total is None:
        return None, None
    original_decimal = Decimal(str(original_total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discounted_decimal = (original_decimal * Decimal('0.90')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discount_amount_decimal = (original_decimal - discounted_decimal).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(discounted_decimal), float(discount_amount_decimal)


def calculate_prebook_discount_with_percent(original_total, discount_percent):
    if original_total is None:
        return None, None
    try:
        percent_value = Decimal(str(discount_percent)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        percent_value = Decimal(str(PREBOOK_DISCOUNT_PERCENT)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    if percent_value < Decimal('0'):
        percent_value = Decimal('0')
    if percent_value > Decimal('100'):
        percent_value = Decimal('100')

    original_decimal = Decimal(str(original_total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discount_multiplier = Decimal('1') - (percent_value / Decimal('100'))
    discounted_decimal = (original_decimal * discount_multiplier).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discount_amount_decimal = (original_decimal - discounted_decimal).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(discounted_decimal), float(discount_amount_decimal)


def haversine_distance_miles(lat1, lon1, lat2, lon2):
    # Calculate great-circle distance between two lat/lng pairs
    radius_miles = 3958.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_miles * c


_done_ensure_travel_tables = False


def ensure_travel_tables():
    """Create/upgrade travel settings and operating bases for multi-base pricing."""
    global _done_ensure_travel_tables
    if _done_ensure_travel_tables:
        return
    conn = get_db_connection()
    cursor = conn.cursor()

    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if engine == 'postgres':
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id BIGSERIAL PRIMARY KEY,
                tomtom_api_key VARCHAR(255),
                price_per_minute NUMERIC(6,2) DEFAULT 0.00,
                extended_price_per_minute NUMERIC(6,2) DEFAULT 0.00,
                max_service_radius_miles NUMERIC(6,2) DEFAULT 15.00,
                enable_travel_pricing BOOLEAN DEFAULT FALSE,
                ask_for_postcode BOOLEAN DEFAULT FALSE
            )
            """
        )

        # Drop legacy base_postcode column if present
        cursor.execute("ALTER TABLE settings DROP COLUMN IF EXISTS base_postcode")

        # Ensure columns exist (Postgres supports IF NOT EXISTS)
        cursor.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS tomtom_api_key VARCHAR(255)")
        cursor.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS price_per_minute NUMERIC(6,2) DEFAULT 0.00")
        cursor.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS extended_price_per_minute NUMERIC(6,2) DEFAULT 0.00")
        cursor.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS max_service_radius_miles NUMERIC(6,2) DEFAULT 15.00")
        cursor.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS enable_travel_pricing BOOLEAN DEFAULT FALSE")
        cursor.execute("ALTER TABLE settings ADD COLUMN IF NOT EXISTS ask_for_postcode BOOLEAN DEFAULT FALSE")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS operating_bases (
                id BIGSERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                postcode VARCHAR(255) NOT NULL,
                latitude NUMERIC(10,7) NULL,
                longitude NUMERIC(10,7) NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )

        # Travel columns on service_requests for audit of assigned base
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS travel_fee NUMERIC(10,2)")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS distance_miles NUMERIC(10,2)")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS travel_time_minutes INTEGER")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS pricing_method VARCHAR(50)")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS assigned_base_id INTEGER")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS assigned_base_name VARCHAR(150)")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS payment_type VARCHAR(30) DEFAULT 'in_person'")
        cursor.execute("ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS is_paid BOOLEAN DEFAULT FALSE")
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INT PRIMARY KEY AUTO_INCREMENT,
                tomtom_api_key VARCHAR(255),
                price_per_minute DECIMAL(6,2) DEFAULT 0.00,
                max_service_radius_miles DECIMAL(6,2) DEFAULT 15.00,
                enable_travel_pricing TINYINT(1) DEFAULT 0,
                ask_for_postcode TINYINT(1) DEFAULT 0
            )
            """
        )

        # Drop legacy base_postcode column if present
        cursor.execute("SHOW COLUMNS FROM settings LIKE 'base_postcode'")
        if cursor.fetchone():
            cursor.execute("ALTER TABLE settings DROP COLUMN base_postcode")

        cursor.execute("SHOW COLUMNS FROM settings LIKE 'tomtom_api_key'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE settings ADD COLUMN tomtom_api_key VARCHAR(255) AFTER id")

        cursor.execute("SHOW COLUMNS FROM settings LIKE 'price_per_minute'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE settings ADD COLUMN price_per_minute DECIMAL(6,2) DEFAULT 0.00 AFTER tomtom_api_key")

        cursor.execute("SHOW COLUMNS FROM settings LIKE 'extended_price_per_minute'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE settings ADD COLUMN extended_price_per_minute DECIMAL(6,2) DEFAULT 0.00 AFTER price_per_minute")

        cursor.execute("SHOW COLUMNS FROM settings LIKE 'max_service_radius_miles'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE settings ADD COLUMN max_service_radius_miles DECIMAL(6,2) DEFAULT 15.00 AFTER extended_price_per_minute")

        cursor.execute("SHOW COLUMNS FROM settings LIKE 'enable_travel_pricing'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE settings ADD COLUMN enable_travel_pricing TINYINT(1) DEFAULT 0 AFTER max_service_radius_miles")

        cursor.execute("SHOW COLUMNS FROM settings LIKE 'ask_for_postcode'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE settings ADD COLUMN ask_for_postcode TINYINT(1) DEFAULT 0")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS operating_bases (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                postcode VARCHAR(255) NOT NULL,
                latitude DECIMAL(10,7) NULL,
                longitude DECIMAL(10,7) NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

        # Travel columns on service_requests for audit of assigned base
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'travel_fee'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN travel_fee DECIMAL(10,2) DEFAULT NULL")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'distance_miles'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN distance_miles DECIMAL(10,2) DEFAULT NULL")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'travel_time_minutes'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN travel_time_minutes INT DEFAULT NULL")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'pricing_method'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN pricing_method VARCHAR(50) DEFAULT NULL")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'assigned_base_id'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN assigned_base_id INT NULL")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'assigned_base_name'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN assigned_base_name VARCHAR(150) NULL")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'payment_type'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN payment_type VARCHAR(30) DEFAULT 'in_person'")
        cursor.execute("SHOW COLUMNS FROM service_requests LIKE 'is_paid'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE service_requests ADD COLUMN is_paid TINYINT(1) DEFAULT 0")

    # Seed default settings row
    cursor.execute("SELECT COUNT(*) FROM settings")
    count_settings = cursor.fetchone()[0]
    if count_settings == 0:
        if engine == 'postgres':
            cursor.execute(
                """
                INSERT INTO settings (id, tomtom_api_key, price_per_minute, extended_price_per_minute, max_service_radius_miles, enable_travel_pricing, ask_for_postcode)
                VALUES (1, NULL, 0.00, 0.00, 15.00, FALSE, FALSE)
                """
            )
        else:
            cursor.execute(
                """
                INSERT INTO settings (id, tomtom_api_key, price_per_minute, extended_price_per_minute, max_service_radius_miles, enable_travel_pricing, ask_for_postcode)
                VALUES (1, NULL, 0.00, 0.00, 15.00, 0, 0)
                """
            )

    # Seed initial operating bases if empty
    cursor.execute("SELECT COUNT(*) FROM operating_bases")
    base_count = cursor.fetchone()[0]
    if base_count == 0:
        seed_bases = [
            ('Enfield', 'EN1 3ES'),
            ('Stevenage', 'SG1 1BP'),
            ('Barnet', 'EN5 5RP'),
            ('Ware', 'SG12 9AJ'),
            ('Cheshunt', 'EN8 0XG'),
            ('Hitchin', 'SG5 1DN'),
            ('Luton', 'LU1 2HE'),
            ('Hatfield', 'AL10 0RN'),
            ('Hertford', 'SG14 1AG'),
            ('St. Albans', 'AL1 3LD')
        ]
        if engine == 'postgres':
            cursor.executemany(
                "INSERT INTO operating_bases (name, postcode, is_active) VALUES (%s, %s, TRUE)",
                seed_bases
            )
        else:
            cursor.executemany(
                "INSERT INTO operating_bases (name, postcode, is_active) VALUES (%s, %s, 1)",
                seed_bases
            )

    # Migration: Add pricing_model column to services if missing
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS pricing_model VARCHAR(20) DEFAULT 'simple'")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'pricing_model'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN pricing_model VARCHAR(20) DEFAULT 'simple' AFTER discount_percent")
            app.logger.info('Added pricing_model column to services table.')

    # Migration: Add table header columns for tenancy customization
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS table_header_col1 VARCHAR(100) DEFAULT 'Property Type'")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS table_header_col2 VARCHAR(100) DEFAULT 'Standard Price'")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS table_header_col3 VARCHAR(100) DEFAULT 'Upgrade Option'")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'table_header_col1'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN table_header_col1 VARCHAR(100) DEFAULT 'Property Type' AFTER pricing_model")
            cursor.execute("ALTER TABLE services ADD COLUMN table_header_col2 VARCHAR(100) DEFAULT 'Standard Price' AFTER table_header_col1")
            cursor.execute("ALTER TABLE services ADD COLUMN table_header_col3 VARCHAR(100) DEFAULT 'Upgrade Option' AFTER table_header_col2")
            app.logger.info('Added table_header columns to services table.')

    # Migration: Add allow_multiselect column for simple/options services
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS allow_multiselect BOOLEAN DEFAULT FALSE")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'allow_multiselect'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN allow_multiselect TINYINT(1) DEFAULT 0 AFTER table_header_col3")
            app.logger.info('Added allow_multiselect column to services table.')

    # Migration: Add service_category column for one-time vs contract services
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS service_category VARCHAR(20) DEFAULT 'one_time'")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'service_category'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN service_category VARCHAR(20) DEFAULT 'one_time' AFTER discount_percent")
            app.logger.info('Added service_category column to services table.')

    cursor.execute("UPDATE services SET service_category = 'one_time' WHERE service_category IS NULL OR service_category = ''")

    # Migration: Add contract_pricing_plans column to services
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_pricing_plans TEXT")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'contract_pricing_plans'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN contract_pricing_plans TEXT NULL AFTER service_category")
            app.logger.info('Added contract_pricing_plans column to services table.')

    # Migration: Add is_contract boolean to services (replaces service_category for contract toggling)
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS is_contract BOOLEAN DEFAULT FALSE")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'is_contract'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN is_contract TINYINT(1) DEFAULT 0 AFTER contract_pricing_plans")
            app.logger.info('Added is_contract column to services table.')

    # Migration: Add contract_intro fields to services for residential-style detail pages
    if engine == 'postgres':
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_section_title VARCHAR(255)")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_section_subtitle TEXT")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_intro_title VARCHAR(255)")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_intro_body TEXT")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_trust_body TEXT")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_trust_image VARCHAR(500)")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_continuity_body TEXT")
        cursor.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS contract_continuity_image VARCHAR(500)")
    else:
        cursor.execute("SHOW COLUMNS FROM services LIKE 'contract_section_title'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE services ADD COLUMN contract_section_title VARCHAR(255) NULL AFTER is_contract")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_section_subtitle TEXT NULL AFTER contract_section_title")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_intro_title VARCHAR(255) NULL AFTER contract_section_subtitle")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_intro_body TEXT NULL AFTER contract_intro_title")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_trust_body TEXT NULL AFTER contract_intro_body")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_trust_image VARCHAR(500) NULL AFTER contract_trust_body")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_continuity_body TEXT NULL AFTER contract_trust_image")
            cursor.execute("ALTER TABLE services ADD COLUMN contract_continuity_image VARCHAR(500) NULL AFTER contract_continuity_body")
            app.logger.info('Added contract intro fields to services table.')
        else:
            # Add image columns if they were added after initial migration
            cursor.execute("SHOW COLUMNS FROM services LIKE 'contract_trust_image'")
            if not cursor.fetchone():
                cursor.execute("ALTER TABLE services ADD COLUMN contract_trust_image VARCHAR(500) NULL AFTER contract_trust_body")
                cursor.execute("ALTER TABLE services ADD COLUMN contract_continuity_image VARCHAR(500) NULL AFTER contract_continuity_body")
                app.logger.info('Added contract trust/continuity image columns to services table.')

    # Backfill is_contract from service_category for existing records
    if engine == 'postgres':
        cursor.execute("UPDATE services SET is_contract = TRUE WHERE service_category = 'contract' AND (is_contract IS NULL OR is_contract = FALSE)")
    else:
        cursor.execute("UPDATE services SET is_contract = 1 WHERE service_category = 'contract' AND (is_contract IS NULL OR is_contract = 0)")

    if engine == 'postgres':
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id BIGSERIAL PRIMARY KEY,
                request_id BIGINT NULL,
                service_request_id BIGINT NULL,
                customer_name VARCHAR(150) NOT NULL,
                customer_email VARCHAR(150) NULL,
                customer_phone VARCHAR(50) NULL,
                service_name VARCHAR(255) NOT NULL,
                frequency VARCHAR(30) NOT NULL,
                preferred_time VARCHAR(32) NULL,
                signer_name VARCHAR(150) NULL,
                terms_agreed BOOLEAN DEFAULT FALSE,
                service_day VARCHAR(20) NULL,
                next_service_date DATE NOT NULL,
                next_reminder_at TIMESTAMPTZ NULL,
                last_reminder_sent_at TIMESTAMPTZ NULL,
                reminder_enabled BOOLEAN DEFAULT TRUE,
                status VARCHAR(30) DEFAULT 'active',
                metadata TEXT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS contracts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                request_id INT NULL,
                service_request_id INT NULL,
                customer_name VARCHAR(150) NOT NULL,
                customer_email VARCHAR(150) NULL,
                customer_phone VARCHAR(50) NULL,
                service_name VARCHAR(255) NOT NULL,
                frequency VARCHAR(30) NOT NULL,
                preferred_time VARCHAR(32) NULL,
                signer_name VARCHAR(150) NULL,
                terms_agreed TINYINT(1) DEFAULT 0,
                service_day VARCHAR(20) NULL,
                next_service_date DATE NOT NULL,
                next_reminder_at DATETIME NULL,
                last_reminder_sent_at DATETIME NULL,
                reminder_enabled TINYINT(1) DEFAULT 1,
                status VARCHAR(30) DEFAULT 'active',
                metadata TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

    # Migration: ensure agreement/day columns exist on older contracts tables
    if engine == 'postgres':
        cursor.execute("ALTER TABLE contracts ADD COLUMN IF NOT EXISTS signer_name VARCHAR(150)")
        cursor.execute("ALTER TABLE contracts ADD COLUMN IF NOT EXISTS terms_agreed BOOLEAN DEFAULT FALSE")
        cursor.execute("ALTER TABLE contracts ADD COLUMN IF NOT EXISTS service_day VARCHAR(20)")
    else:
        cursor.execute("SHOW COLUMNS FROM contracts LIKE 'signer_name'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE contracts ADD COLUMN signer_name VARCHAR(150) NULL AFTER preferred_time")
        cursor.execute("SHOW COLUMNS FROM contracts LIKE 'terms_agreed'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE contracts ADD COLUMN terms_agreed TINYINT(1) DEFAULT 0 AFTER signer_name")
        cursor.execute("SHOW COLUMNS FROM contracts LIKE 'service_day'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE contracts ADD COLUMN service_day VARCHAR(20) NULL AFTER terms_agreed")

    # Create service_room_cards table for contract-service room cards (replaces domestic_cleaning_cards)
    if engine == 'postgres':
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS service_room_cards (
                id BIGSERIAL PRIMARY KEY,
                service_id BIGINT NOT NULL,
                card_key VARCHAR(80) NOT NULL,
                room_name VARCHAR(120) NOT NULL,
                lifestyle_copy TEXT NOT NULL,
                image_path TEXT,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(service_id, card_key)
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS service_room_cards (
                id INT AUTO_INCREMENT PRIMARY KEY,
                service_id INT NOT NULL,
                card_key VARCHAR(80) NOT NULL,
                room_name VARCHAR(120) NOT NULL,
                lifestyle_copy TEXT NOT NULL,
                image_path TEXT,
                sort_order INT DEFAULT 0,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_service_card (service_id, card_key)
            )
        """)

    # Auto-populate pricing_model based on existing pricing data
    cursor.execute("SELECT id FROM services WHERE pricing_model IS NULL OR pricing_model = 'simple'")
    services_to_update = cursor.fetchall()
    for (service_id,) in services_to_update:
        # Check for tenancy rates
        cursor.execute("SELECT COUNT(*) FROM service_tenancy_rates WHERE service_id = %s", (service_id,))
        if cursor.fetchone()[0] > 0:
            cursor.execute("UPDATE services SET pricing_model = 'tenancy' WHERE id = %s", (service_id,))
            continue
        # Check for pricing tiers
        cursor.execute("SELECT COUNT(*) FROM service_pricing_tiers WHERE service_id = %s", (service_id,))
        if cursor.fetchone()[0] > 0:
            cursor.execute("UPDATE services SET pricing_model = 'deep' WHERE id = %s", (service_id,))
            continue
        # Check for pricing items
        cursor.execute("SELECT COUNT(*) FROM service_pricing_items WHERE service_id = %s", (service_id,))
        if cursor.fetchone()[0] > 0:
            cursor.execute("UPDATE services SET pricing_model = 'itemized' WHERE id = %s", (service_id,))
            continue
        # Check for legacy options
        cursor.execute("SELECT COUNT(*) FROM service_options WHERE service_id = %s", (service_id,))
        if cursor.fetchone()[0] > 0:
            cursor.execute("UPDATE services SET pricing_model = 'options' WHERE id = %s", (service_id,))

    try:
        conn.commit()
        _done_ensure_travel_tables = True
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()


_done_ensure_faq_table = False


def ensure_faq_table():
    """Create FAQs table if it doesn't exist."""
    global _done_ensure_faq_table
    if _done_ensure_faq_table:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faqs (
                id BIGSERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                category VARCHAR(100) DEFAULT 'General',
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faqs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                category VARCHAR(100) DEFAULT 'General',
                sort_order INT DEFAULT 0,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_faq_table = True


DEFAULT_HOME_PAGE_SECTIONS = [
    {'section_key': 'hero', 'section_label': 'Hero'},
    {'section_key': 'services', 'section_label': 'Services'},
    {'section_key': 'why_choose', 'section_label': 'Why Choose Us'},
    {'section_key': 'about', 'section_label': 'About'},
    {'section_key': 'areas_coverage', 'section_label': 'Areas We Cover'},
    {'section_key': 'testimonials', 'section_label': 'Testimonials'},
    {'section_key': 'policy', 'section_label': 'Policies'},
    {'section_key': 'careers', 'section_label': 'Careers'},
    {'section_key': 'faqs', 'section_label': 'FAQs'},
    {'section_key': 'reviews', 'section_label': 'Reviews'}
]


_done_ensure_home_page_sections_table = False


def ensure_home_page_sections_table():
    """Create homepage section ordering table and seed defaults."""
    global _done_ensure_home_page_sections_table
    if _done_ensure_home_page_sections_table:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS home_page_sections (
                section_key VARCHAR(64) PRIMARY KEY,
                section_label VARCHAR(120) NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS home_page_sections (
                section_key VARCHAR(64) PRIMARY KEY,
                section_label VARCHAR(120) NOT NULL,
                sort_order INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )

    cursor.execute("SELECT section_key FROM home_page_sections")
    existing_keys = {row[0] for row in cursor.fetchall()}

    for index, section in enumerate(DEFAULT_HOME_PAGE_SECTIONS):
        section_key = section['section_key']
        section_label = section['section_label']
        if section_key in existing_keys:
            cursor.execute(
                "UPDATE home_page_sections SET section_label = %s WHERE section_key = %s",
                (section_label, section_key)
            )
        else:
            cursor.execute(
                "INSERT INTO home_page_sections (section_key, section_label, sort_order) VALUES (%s, %s, %s)",
                (section_key, section_label, index)
            )

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_home_page_sections_table = True


def fetch_home_page_sections():
    """Fetch homepage sections in the saved display order."""
    ensure_home_page_sections_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT section_key, section_label, sort_order FROM home_page_sections ORDER BY sort_order ASC, section_key ASC"
    )
    sections = cursor.fetchall()
    cursor.close()
    conn.close()
    return sections


def save_home_page_section_order(section_keys):
    """Persist homepage section order based on submitted key list."""
    ensure_home_page_sections_table()

    if not isinstance(section_keys, list) or not section_keys:
        raise ValueError('A valid section order is required.')

    valid_keys = [section['section_key'] for section in DEFAULT_HOME_PAGE_SECTIONS]
    normalized_keys = [str(key).strip() for key in section_keys if str(key).strip()]

    if len(normalized_keys) != len(set(normalized_keys)):
        raise ValueError('Duplicate sections are not allowed in order payload.')

    invalid_keys = [key for key in normalized_keys if key not in valid_keys]
    if invalid_keys:
        raise ValueError('One or more sections are invalid.')

    if set(normalized_keys) != set(valid_keys):
        raise ValueError('Section order payload is incomplete.')

    conn = get_db_connection()
    cursor = conn.cursor()
    for index, section_key in enumerate(normalized_keys):
        cursor.execute(
            "UPDATE home_page_sections SET sort_order = %s WHERE section_key = %s",
            (index, section_key)
        )
    conn.commit()
    cursor.close()
    conn.close()

    return fetch_home_page_sections()


_done_ensure_policy_table = False


def ensure_policy_table():
    """Create policies table if it doesn't exist."""
    global _done_ensure_policy_table
    if _done_ensure_policy_table:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS policies (
                id BIGSERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                icon VARCHAR(100) DEFAULT 'shield',
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS policies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                description TEXT NOT NULL,
                icon VARCHAR(100) DEFAULT 'shield',
                sort_order INT DEFAULT 0,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

    # Seed default policies if empty
    cursor.execute("SELECT COUNT(*) FROM policies")
    count = cursor.fetchone()[0]
    if count == 0:
        default_policies = [
            ('Satisfaction Guarantee', "If you're not completely satisfied with our service, let us know within 24 hours and we'll make it right—free of charge.", 'shield-check', 1),
            ('Cancellation Policy', 'We understand plans change. Cancel or reschedule at least 24 hours before your appointment to avoid any cancellation fees.', 'clock', 2),
            ('Payment Terms', 'Payment is due upon completion of service. We accept all major credit/debit cards, bank transfers, and cash payments.', 'credit-card', 3),
            ('Late Arrivals', "If we're running late, we'll notify you immediately. Traffic delays of more than 15 minutes? We'll offer a discount or reschedule at your convenience.", 'clock-alert', 4),
            ('Insurance & Liability', "We're fully insured. In the rare event of accidental damage during cleaning, we'll handle the claim process and cover repair or replacement costs.", 'shield', 5),
            ('Privacy Policy', 'Your personal information is safe with us. We never share your data with third parties and comply with all data protection regulations.', 'users', 6),
        ]
        for title, description, icon, sort_order in default_policies:
            if engine == 'postgres':
                cursor.execute(
                    "INSERT INTO policies (title, description, icon, sort_order, is_active) VALUES (%s, %s, %s, %s, TRUE)",
                    (title, description, icon, sort_order)
                )
            else:
                cursor.execute(
                    "INSERT INTO policies (title, description, icon, sort_order, is_active) VALUES (%s, %s, %s, %s, 1)",
                    (title, description, icon, sort_order)
                )

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_policy_table = True


def fetch_policies_from_db(include_inactive=False):
    """Fetch policies from database."""
    ensure_policy_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    if include_inactive:
        cursor.execute("SELECT * FROM policies ORDER BY sort_order ASC, id ASC")
    else:
        if engine == 'postgres':
            cursor.execute("SELECT * FROM policies WHERE is_active = TRUE ORDER BY sort_order ASC, id ASC")
        else:
            cursor.execute("SELECT * FROM policies WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
    
    policies = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # Convert boolean fields
    for policy in policies:
        policy['is_active'] = bool(policy.get('is_active'))
    
    return policies


_done_ensure_domestic_cleaning_tables = False


def ensure_domestic_cleaning_tables():
    """Create and seed domestic cleaning section tables."""
    global _done_ensure_domestic_cleaning_tables
    if _done_ensure_domestic_cleaning_tables:
        return
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        _ensure_domestic_cleaning_tables_inner(conn, cursor, engine)
    except Exception:
        app.logger.exception('ensure_domestic_cleaning_tables failed')
    finally:
        _done_ensure_domestic_cleaning_tables = True  # don't retry on every request
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _ensure_domestic_cleaning_tables_inner(conn, cursor, engine):
    if engine == 'postgres':
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domestic_cleaning_content (
                id BIGSERIAL PRIMARY KEY,
                section_title VARCHAR(255) NOT NULL,
                section_subtitle TEXT NOT NULL,
                intro_title VARCHAR(255) NOT NULL,
                intro_body TEXT NOT NULL,
                trust_body TEXT NOT NULL,
                continuity_body TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domestic_cleaning_cards (
                id BIGSERIAL PRIMARY KEY,
                card_key VARCHAR(80) NOT NULL UNIQUE,
                room_name VARCHAR(120) NOT NULL,
                lifestyle_copy TEXT NOT NULL,
                image_path TEXT,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domestic_cleaning_pricing (
                id BIGSERIAL PRIMARY KEY,
                plan_key VARCHAR(40) NOT NULL UNIQUE,
                plan_name VARCHAR(120) NOT NULL,
                price_per_hour NUMERIC(10,2) NOT NULL,
                per_label VARCHAR(120) DEFAULT 'per hour per cleaner',
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domestic_cleaning_content (
                id INT AUTO_INCREMENT PRIMARY KEY,
                section_title VARCHAR(255) NOT NULL,
                section_subtitle TEXT NOT NULL,
                intro_title VARCHAR(255) NOT NULL,
                intro_body TEXT NOT NULL,
                trust_body TEXT NOT NULL,
                continuity_body TEXT NOT NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domestic_cleaning_cards (
                id INT AUTO_INCREMENT PRIMARY KEY,
                card_key VARCHAR(80) NOT NULL UNIQUE,
                room_name VARCHAR(120) NOT NULL,
                lifestyle_copy TEXT NOT NULL,
                image_path TEXT,
                sort_order INT DEFAULT 0,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS domestic_cleaning_pricing (
                id INT AUTO_INCREMENT PRIMARY KEY,
                plan_key VARCHAR(40) NOT NULL UNIQUE,
                plan_name VARCHAR(120) NOT NULL,
                price_per_hour DECIMAL(10,2) NOT NULL,
                per_label VARCHAR(120) DEFAULT 'per hour per cleaner',
                sort_order INT DEFAULT 0,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

    # Commit DDL separately so tables exist even if seeding fails
    conn.commit()

    cursor.execute("SELECT COUNT(*) FROM domestic_cleaning_content")
    content_count = cursor.fetchone()[0]
    if content_count == 0:
        cursor.execute(
            """
            INSERT INTO domestic_cleaning_content
                (section_title, section_subtitle, intro_title, intro_body, trust_body, continuity_body, is_active)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                'Domestic Cleaning',
                'Lifestyle-led home care designed around your routine, with premium consistency and trusted professionals.',
                'Our Regular Domestic Cleaning Service',
                'Your home should feel calm, fresh and effortlessly welcoming. Done-Well tailors each visit to your priorities so every room feels restored, reset and ready for living.',
                'Every cleaner is reference-checked, fully vetted, trained, and legally eligible to work in the UK. Our teams arrive in company uniform with ID for complete peace of mind.',
                'Where possible, you keep the same cleaner to build trust and familiarity. If your regular cleaner is unavailable, we quickly arrange a suitable replacement to keep your routine uninterrupted.',
                True if engine == 'postgres' else 1
            )
        )

    cursor.execute("SELECT COUNT(*) FROM domestic_cleaning_cards")
    card_count = cursor.fetchone()[0]
    if card_count == 0:
        default_cards = [
            ('living_room', 'Living Room', 'We restore freshness and order to your sanctuary—lifting dust, polishing glass and mirrors, refreshing surfaces, and leaving your social space visibly calm and welcoming.', 1),
            ('kitchen', 'Kitchen Areas', 'We bring hygienic shine back to your kitchen with degreased touchpoints, refreshed worktops, polished sinks and taps, and a clean, ready-to-use cooking space.', 2),
            ('all_rooms', 'All Rooms Finishing', 'We handle the often-missed details—from handles, switches, skirting and ledges to polished mirrors—so your entire home feels consistently finished.', 3),
            ('bedrooms', 'Bedrooms', 'We create a restful bedroom environment with tidy surfaces, polished mirrors, refreshed floors and beautifully presented bedding for a hotel-like finish.', 4),
            ('bathrooms', 'Bathrooms / Toilets', 'We deliver a sanitised, sparkling bathroom standard—disinfected essentials, polished chrome, refreshed tiles and neatly arranged finishing touches.', 5),
        ]
        for key, name, copy, sort_order in default_cards:
            cursor.execute(
                """
                INSERT INTO domestic_cleaning_cards
                    (card_key, room_name, lifestyle_copy, sort_order, is_active)
                VALUES
                    (%s, %s, %s, %s, %s)
                """,
                (key, name, copy, sort_order, True if engine == 'postgres' else 1)
            )

    cursor.execute("SELECT COUNT(*) FROM domestic_cleaning_pricing")
    pricing_count = cursor.fetchone()[0]
    if pricing_count == 0:
        default_pricing = [
            ('weekly', 'Weekly Regular Domestic Cleaning', 17.99, 'per hour per cleaner', 1),
            ('fortnightly', 'Fortnightly Regular Domestic Cleaning', 19.99, 'per hour per cleaner', 2),
            ('monthly', 'Monthly Regular Domestic Cleaning', 24.99, 'per hour per cleaner', 3),
        ]
        for key, name, price, per_label, sort_order in default_pricing:
            cursor.execute(
                """
                INSERT INTO domestic_cleaning_pricing
                    (plan_key, plan_name, price_per_hour, per_label, sort_order, is_active)
                VALUES
                    (%s, %s, %s, %s, %s, %s)
                """,
                (key, name, price, per_label, sort_order, True if engine == 'postgres' else 1)
            )

    conn.commit()


def fetch_domestic_cleaning_data(include_inactive=False):
    ensure_domestic_cleaning_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    _empty = {'content': {}, 'cards': [], 'pricing': []}

    try:
        if include_inactive:
            cursor.execute("SELECT * FROM domestic_cleaning_content ORDER BY id ASC LIMIT 1")
        else:
            if engine == 'postgres':
                cursor.execute("SELECT * FROM domestic_cleaning_content WHERE is_active = TRUE ORDER BY id ASC LIMIT 1")
            else:
                cursor.execute("SELECT * FROM domestic_cleaning_content WHERE is_active = 1 ORDER BY id ASC LIMIT 1")
    except Exception:
        cursor.close()
        conn.close()
        return _empty
    content_row = cursor.fetchone() or {}

    if include_inactive:
        cursor.execute("SELECT * FROM domestic_cleaning_cards ORDER BY sort_order ASC, id ASC")
    else:
        if engine == 'postgres':
            cursor.execute("SELECT * FROM domestic_cleaning_cards WHERE is_active = TRUE ORDER BY sort_order ASC, id ASC")
        else:
            cursor.execute("SELECT * FROM domestic_cleaning_cards WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
    cards = cursor.fetchall() or []

    if include_inactive:
        cursor.execute("SELECT * FROM domestic_cleaning_pricing ORDER BY sort_order ASC, id ASC")
    else:
        if engine == 'postgres':
            cursor.execute("SELECT * FROM domestic_cleaning_pricing WHERE is_active = TRUE ORDER BY sort_order ASC, id ASC")
        else:
            cursor.execute("SELECT * FROM domestic_cleaning_pricing WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
    pricing = cursor.fetchall() or []

    cursor.close()
    conn.close()

    for row in cards:
        row['is_active'] = bool(row.get('is_active'))
    for row in pricing:
        row['is_active'] = bool(row.get('is_active'))

    if content_row:
        content_row['is_active'] = bool(content_row.get('is_active'))

    return {
        'content': content_row,
        'cards': cards,
        'pricing': pricing
    }


def migrate_domestic_to_services():
    """Auto-migrate domestic_cleaning_* data into the unified services table.

    Creates a 'Domestic Cleaning' service record with is_contract=True,
    copies room cards into service_room_cards, and drops the legacy tables.
    This is idempotent — skips if migration already happened.
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    # Check if domestic tables still exist
    try:
        cursor.execute("SELECT COUNT(*) AS cnt FROM domestic_cleaning_content")
        cursor.fetchone()
    except Exception:
        # Tables already dropped — migration already done
        # Rollback any aborted transaction before returning connection to pool
        try:
            conn.rollback()
        except Exception:
            pass
        cursor.close()
        conn.close()
        return

    # Check if we already migrated (look for a service with contract_section_title matching domestic content)
    cursor.execute(
        "SELECT id FROM services WHERE service_category = %s AND title LIKE %s LIMIT 1",
        ('contract', '%Domestic Cleaning%')
    )
    existing = cursor.fetchone()
    if existing:
        # Already migrated — proceed to drop legacy tables
        cursor.close()
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS domestic_cleaning_pricing")
        cursor.execute("DROP TABLE IF EXISTS domestic_cleaning_cards")
        cursor.execute("DROP TABLE IF EXISTS domestic_cleaning_content")
        conn.commit()
        cursor.close()
        conn.close()
        app.logger.info('Domestic tables already migrated (service exists). Dropped legacy tables.')
        return

    # Fetch all domestic data
    domestic = fetch_domestic_cleaning_data(include_inactive=True)
    content = domestic.get('content') or {}
    cards = domestic.get('cards') or []
    pricing = domestic.get('pricing') or []

    # Build contract_pricing_plans from domestic pricing
    contract_plans = []
    for plan in pricing:
        contract_plans.append({
            'key': plan.get('plan_key', ''),
            'label': plan.get('plan_name', ''),
            'price': float(plan.get('price_per_hour', 0))
        })
    if not contract_plans:
        contract_plans = default_contract_pricing_plans()

    # Build the service description from domestic content
    description = content.get('intro_body') or 'Regular domestic cleaning service tailored to your home.'

    is_pg = 'postgres' in engine
    active_val = 1
    is_contract_val = True if is_pg else 1
    inactive_val = 0

    cursor.close()
    cursor = conn.cursor()

    # Insert the unified service record
    if is_pg:
        cursor.execute(
            """
            INSERT INTO services (title, name, short_description, description, price,
                service_category, contract_pricing_plans, is_contract,
                contract_section_title, contract_section_subtitle,
                contract_intro_title, contract_intro_body,
                contract_trust_body, contract_continuity_body,
                pricing_model, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                content.get('section_title') or 'Domestic Cleaning',
                content.get('section_title') or 'Domestic Cleaning',
                (content.get('section_subtitle') or description)[:150],
                description,
                float(contract_plans[0]['price']) if contract_plans else 17.99,
                'contract',
                json.dumps(contract_plans),
                is_contract_val,
                content.get('section_title') or 'Domestic Cleaning',
                content.get('section_subtitle') or '',
                content.get('intro_title') or '',
                content.get('intro_body') or '',
                content.get('trust_body') or '',
                content.get('continuity_body') or '',
                'simple',
                active_val
            )
        )
        new_service_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            """
            INSERT INTO services (title, name, short_description, description, price,
                service_category, contract_pricing_plans, is_contract,
                contract_section_title, contract_section_subtitle,
                contract_intro_title, contract_intro_body,
                contract_trust_body, contract_continuity_body,
                pricing_model, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                content.get('section_title') or 'Domestic Cleaning',
                content.get('section_title') or 'Domestic Cleaning',
                (content.get('section_subtitle') or description)[:150],
                description,
                float(contract_plans[0]['price']) if contract_plans else 17.99,
                'contract',
                json.dumps(contract_plans),
                1,
                content.get('section_title') or 'Domestic Cleaning',
                content.get('section_subtitle') or '',
                content.get('intro_title') or '',
                content.get('intro_body') or '',
                content.get('trust_body') or '',
                content.get('continuity_body') or '',
                'simple',
                1
            )
        )
        new_service_id = cursor.lastrowid

    # Migrate room cards
    for card in cards:
        cursor.execute(
            """
            INSERT INTO service_room_cards (service_id, card_key, room_name, lifestyle_copy, image_path, sort_order, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                new_service_id,
                card.get('card_key', ''),
                card.get('room_name', ''),
                card.get('lifestyle_copy', ''),
                card.get('image_path'),
                card.get('sort_order', 0),
                active_val if card.get('is_active') else inactive_val
            )
        )

    conn.commit()

    # Drop legacy tables
    cursor.execute("DROP TABLE IF EXISTS domestic_cleaning_pricing")
    cursor.execute("DROP TABLE IF EXISTS domestic_cleaning_cards")
    cursor.execute("DROP TABLE IF EXISTS domestic_cleaning_content")
    conn.commit()

    cursor.close()
    conn.close()
    app.logger.info('Successfully migrated domestic cleaning data to service id=%s and dropped legacy tables.', new_service_id)


_done_ensure_residential_contract_service = False


def ensure_residential_contract_service():
    """Guarantee a dedicated Domestic Cleaning contract service exists."""
    global _done_ensure_residential_contract_service
    if _done_ensure_residential_contract_service:
        return
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    try:
        cursor.execute(
            """
            SELECT id
            FROM services
            WHERE service_category = %s
              AND (
                    LOWER(COALESCE(title, '')) LIKE %s
                 OR LOWER(COALESCE(name, '')) LIKE %s
              )
            LIMIT 1
            """,
            ('contract', '%domestic%', '%domestic%')
        )
        found = cursor.fetchone()
        if found:
            return

        default_plans = json.dumps(default_contract_pricing_plans())
        cursor.close()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO services (
                title, name, short_description, description, price,
                service_category, contract_pricing_plans, is_contract,
                contract_section_title, contract_section_subtitle,
                contract_intro_title, contract_intro_body,
                contract_trust_body, contract_continuity_body,
                pricing_model, is_active
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                'Domestic Cleaning',
                'Domestic Cleaning',
                'Recurring home cleaning plans tailored to your routine.',
                'Professional recurring domestic cleaning with flexible weekly, fortnightly, and monthly plans.',
                17.99,
                'contract',
                default_plans,
                (True if 'postgres' in engine else 1),
                'Domestic Cleaning',
                'Lifestyle-led home care designed around your routine.',
                'Our Regular Domestic Cleaning Service',
                'Choose a recurring cleaning plan that fits your schedule and home.',
                'Vetted, insured, and reliable cleaning professionals.',
                'Consistent quality and continuity for every visit.',
                'simple',
                1
            )
        )
        conn.commit()
        app.logger.info('Created fallback Domestic Cleaning contract service in services table.')
    finally:
        cursor.close()
        conn.close()
    _done_ensure_residential_contract_service = True


def _default_domestic_room_cards_seed():
    return [
        {
            'card_key': 'living_room',
            'room_name': 'Living Room',
            'lifestyle_copy': 'We restore freshness and order to your sanctuary—lifting dust, polishing glass and mirrors, refreshing surfaces, and leaving your social space visibly calm and welcoming.',
            'sort_order': 1,
            'is_active': True
        },
        {
            'card_key': 'kitchen',
            'room_name': 'Kitchen Areas',
            'lifestyle_copy': 'We bring hygienic shine back to your kitchen with degreased touchpoints, refreshed worktops, polished sinks and taps, and a clean, ready-to-use cooking space.',
            'sort_order': 2,
            'is_active': True
        },
        {
            'card_key': 'all_rooms',
            'room_name': 'All Rooms Finishing',
            'lifestyle_copy': 'We handle the often-missed details—from handles, switches, skirting and ledges to polished mirrors—so your entire home feels consistently finished.',
            'sort_order': 3,
            'is_active': True
        },
        {
            'card_key': 'bedrooms',
            'room_name': 'Bedrooms',
            'lifestyle_copy': 'We create a restful bedroom environment with tidy surfaces, polished mirrors, refreshed floors and beautifully presented bedding for a hotel-like finish.',
            'sort_order': 4,
            'is_active': True
        },
        {
            'card_key': 'bathrooms',
            'room_name': 'Bathrooms / Toilets',
            'lifestyle_copy': 'We deliver a sanitised, sparkling bathroom standard—disinfected essentials, polished chrome, refreshed tiles and neatly arranged finishing touches.',
            'sort_order': 5,
            'is_active': True
        }
    ]


def _default_domestic_pricing_seed():
    return [
        {
            'id': 1,
            'plan_key': 'weekly',
            'plan_name': 'Weekly Regular Domestic Cleaning',
            'price_per_hour': 17.99,
            'per_label': 'per hour per cleaner',
            'sort_order': 1,
            'is_active': True
        },
        {
            'id': 2,
            'plan_key': 'fortnightly',
            'plan_name': 'Fortnightly Regular Domestic Cleaning',
            'price_per_hour': 19.99,
            'per_label': 'per hour per cleaner',
            'sort_order': 2,
            'is_active': True
        },
        {
            'id': 3,
            'plan_key': 'monthly',
            'plan_name': 'Monthly Regular Domestic Cleaning',
            'price_per_hour': 24.99,
            'per_label': 'per hour per cleaner',
            'sort_order': 3,
            'is_active': True
        }
    ]


def _normalize_domestic_plan_row(item, fallback_id):
    if not isinstance(item, dict):
        return None
    plan_key = sanitize_text(item.get('plan_key') or item.get('key'), 40).lower().replace(' ', '_')
    if not plan_key:
        return None
    raw_name = item.get('plan_name') or item.get('label') or plan_key.replace('_', ' ').title()
    plan_name = sanitize_text(raw_name, 120)
    price = normalize_price_value(item.get('price_per_hour') if item.get('price_per_hour') is not None else item.get('price'))
    per_label = sanitize_text(item.get('per_label') or 'per hour per cleaner', 120)
    sort_order = int(item.get('sort_order') or fallback_id)
    is_active = str_to_bool(item.get('is_active', True))
    row_id = int(item.get('id') or fallback_id)
    return {
        'id': row_id,
        'plan_key': plan_key,
        'plan_name': plan_name or plan_key.replace('_', ' ').title(),
        'price_per_hour': price if price is not None else 0,
        'per_label': per_label or 'per hour per cleaner',
        'sort_order': sort_order,
        'is_active': bool(is_active)
    }


def _load_domestic_pricing_from_service(raw_value):
    parsed = raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except Exception:
            parsed = []
    if isinstance(parsed, dict):
        parsed = parsed.get('plans') or []
    if not isinstance(parsed, list):
        parsed = []

    rows = []
    for idx, item in enumerate(parsed, start=1):
        normalized = _normalize_domestic_plan_row(item, idx)
        if normalized:
            rows.append(normalized)

    if not rows:
        rows = [dict(row) for row in _default_domestic_pricing_seed()]

    rows.sort(key=lambda row: (int(row.get('sort_order') or 0), int(row.get('id') or 0)))
    return rows


def _serialize_domestic_pricing_for_service(rows):
    payload = []
    for row in rows:
        item = _normalize_domestic_plan_row(row, row.get('id') or 1)
        if not item:
            continue
        payload.append(
            {
                'id': item['id'],
                'plan_key': item['plan_key'],
                'plan_name': item['plan_name'],
                'price_per_hour': item['price_per_hour'],
                'per_label': item['per_label'],
                'sort_order': item['sort_order'],
                'is_active': item['is_active'],
                'key': item['plan_key'],
                'label': item['plan_name'],
                'price': item['price_per_hour']
            }
        )
    return json.dumps(payload)


def get_domestic_service_record(create_if_missing=True):
    if create_if_missing:
        ensure_residential_contract_service()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT *
        FROM services
        WHERE service_category = %s
          AND (
                LOWER(COALESCE(title, '')) LIKE %s
             OR LOWER(COALESCE(name, '')) LIKE %s
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        ('contract', '%domestic%', '%domestic%')
    )
    service = cursor.fetchone()

    if not service:
        cursor.close()
        conn.close()
        return None

    service_id = service.get('id')

    cursor.execute("SELECT COUNT(*) AS cnt FROM service_room_cards WHERE service_id=%s", (service_id,))
    card_count = int((cursor.fetchone() or {}).get('cnt') or 0)

    needs_content_defaults = not (sanitize_text(service.get('contract_intro_body')) and sanitize_text(service.get('contract_section_title')))
    needs_pricing_defaults = not service.get('contract_pricing_plans')

    if card_count == 0 or needs_content_defaults or needs_pricing_defaults:
        cursor.close()
        cursor = conn.cursor()
        if card_count == 0:
            for row in _default_domestic_room_cards_seed():
                cursor.execute(
                    """
                    INSERT INTO service_room_cards (service_id, card_key, room_name, lifestyle_copy, image_path, sort_order, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        service_id,
                        row['card_key'],
                        row['room_name'],
                        row['lifestyle_copy'],
                        None,
                        int(row['sort_order']),
                        True if (app.config.get('DB_ENGINE') or 'mysql').strip().lower() == 'postgres' else 1
                    )
                )

        if needs_content_defaults or needs_pricing_defaults:
            section_title = sanitize_text(service.get('contract_section_title'), 255) or 'Domestic Cleaning'
            section_subtitle = sanitize_text(service.get('contract_section_subtitle')) or 'Lifestyle-led home care designed around your routine, with premium consistency and trusted professionals.'
            intro_title = sanitize_text(service.get('contract_intro_title'), 255) or 'Our Regular Domestic Cleaning Service'
            intro_body = sanitize_text(service.get('contract_intro_body')) or 'Your home should feel calm, fresh and effortlessly welcoming. Done-Well tailors each visit to your priorities so every room feels restored, reset and ready for living.'
            trust_body = sanitize_text(service.get('contract_trust_body')) or 'Every cleaner is reference-checked, fully vetted, trained, and legally eligible to work in the UK. Our teams arrive in company uniform with ID for complete peace of mind.'
            continuity_body = sanitize_text(service.get('contract_continuity_body')) or 'Where possible, you keep the same cleaner to build trust and familiarity. If your regular cleaner is unavailable, we quickly arrange a suitable replacement to keep your routine uninterrupted.'
            pricing_json = service.get('contract_pricing_plans') or _serialize_domestic_pricing_for_service(_default_domestic_pricing_seed())

            cursor.execute(
                """
                UPDATE services
                SET title=%s,
                    name=%s,
                    contract_section_title=%s,
                    contract_section_subtitle=%s,
                    contract_intro_title=%s,
                    contract_intro_body=%s,
                    contract_trust_body=%s,
                    contract_continuity_body=%s,
                    contract_pricing_plans=%s,
                    service_category=%s,
                    is_contract=%s
                WHERE id=%s
                """,
                (
                    section_title,
                    section_title,
                    section_title,
                    section_subtitle,
                    intro_title,
                    intro_body,
                    trust_body,
                    continuity_body,
                    pricing_json,
                    'contract',
                    True if (app.config.get('DB_ENGINE') or 'mysql').strip().lower() == 'postgres' else 1,
                    service_id
                )
            )

        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM services WHERE id=%s", (service_id,))
        service = cursor.fetchone()

    cursor.close()
    conn.close()
    return service


_done_ensure_chat_tables = False


def ensure_chat_tables():
    """Create tables for public AI chat widget - sessions, messages, and persona settings."""
    global _done_ensure_chat_tables
    if _done_ensure_chat_tables:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        # Chat sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id BIGSERIAL PRIMARY KEY,
                session_id VARCHAR(64) UNIQUE NOT NULL,
                visitor_name VARCHAR(100),
                visitor_email VARCHAR(255),
                visitor_ip VARCHAR(45),
                user_agent TEXT,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                last_message_at TIMESTAMPTZ DEFAULT NOW(),
                is_resolved BOOLEAN DEFAULT FALSE,
                admin_notes TEXT,
                message_count INTEGER DEFAULT 0
            )
        """)
        
        # Chat messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id BIGSERIAL PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # AI persona settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_persona (
                id BIGSERIAL PRIMARY KEY,
                persona_name VARCHAR(100) DEFAULT 'Assistant',
                greeting_message TEXT DEFAULT 'Hello! How can I help you today?',
                persona_description TEXT,
                personality_traits TEXT,
                response_style VARCHAR(50) DEFAULT 'friendly',
                avatar_url TEXT,
                contact_email VARCHAR(255) DEFAULT 'support@sparkleclean.com',
                contact_phone VARCHAR(50) DEFAULT '1-800-SPLK-CLEAN',
                whatsapp_number VARCHAR(50) DEFAULT '+1-800-SPLK-CLEAN',
                is_enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Knowledge base entries (beyond FAQs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_knowledge_base (
                id BIGSERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                category VARCHAR(100) DEFAULT 'General',
                keywords TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Create index for faster session lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session 
            ON chat_messages(session_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_sessions_date 
            ON chat_sessions(started_at DESC)
        """)
        
    else:
        # MySQL versions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(64) UNIQUE NOT NULL,
                visitor_name VARCHAR(100),
                visitor_email VARCHAR(255),
                visitor_ip VARCHAR(45),
                user_agent TEXT,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_message_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                is_resolved TINYINT(1) DEFAULT 0,
                admin_notes TEXT,
                message_count INT DEFAULT 0,
                INDEX idx_session_id (session_id),
                INDEX idx_started_at (started_at)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                role VARCHAR(20) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_session_id (session_id)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_persona (
                id INT AUTO_INCREMENT PRIMARY KEY,
                persona_name VARCHAR(100) DEFAULT 'Assistant',
                greeting_message TEXT,
                persona_description TEXT,
                personality_traits TEXT,
                response_style VARCHAR(50) DEFAULT 'friendly',
                avatar_url TEXT,
                contact_email VARCHAR(255) DEFAULT 'support@sparkleclean.com',
                contact_phone VARCHAR(50) DEFAULT '1-800-SPLK-CLEAN',
                whatsapp_number VARCHAR(50) DEFAULT '+1-800-SPLK-CLEAN',
                is_enabled TINYINT(1) DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_knowledge_base (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                category VARCHAR(100) DEFAULT 'General',
                keywords TEXT,
                is_active TINYINT(1) DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # Add new contact columns to existing ai_persona table
    if engine == 'postgres':
        cursor.execute("ALTER TABLE ai_persona ADD COLUMN IF NOT EXISTS contact_email VARCHAR(255) DEFAULT 'support@sparkleclean.com'")
        cursor.execute("ALTER TABLE ai_persona ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(50) DEFAULT '1-800-SPLK-CLEAN'")
        cursor.execute("ALTER TABLE ai_persona ADD COLUMN IF NOT EXISTS whatsapp_number VARCHAR(50) DEFAULT '+1-800-SPLK-CLEAN'")
    else:
        cursor.execute("ALTER TABLE ai_persona ADD COLUMN contact_email VARCHAR(255) DEFAULT 'support@sparkleclean.com'")
        cursor.execute("ALTER TABLE ai_persona ADD COLUMN contact_phone VARCHAR(50) DEFAULT '1-800-SPLK-CLEAN'")
        cursor.execute("ALTER TABLE ai_persona ADD COLUMN whatsapp_number VARCHAR(50) DEFAULT '+1-800-SPLK-CLEAN'")

    # Insert default persona if not exists
    cursor.execute("SELECT COUNT(*) FROM ai_persona")
    count = cursor.fetchone()[0]
    if count == 0:
        cursor.execute("""
            INSERT INTO ai_persona (persona_name, greeting_message, persona_description, personality_traits, response_style, contact_email, contact_phone, whatsapp_number)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            'Sparkle',
            "Hi there! 👋 I'm Sparkle, your cleaning assistant. How can I help you today?",
            "A friendly and knowledgeable cleaning service assistant who helps visitors with questions about services, pricing, and scheduling.",
            "Friendly, Professional, Helpful, Knowledgeable about cleaning services",
            "friendly",
            "support@sparkleclean.com",
            "1-800-SPLK-CLEAN",
            "+1-800-SPLK-CLEAN"
        ))

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_chat_tables = True


def fetch_travel_settings():
    ensure_travel_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM settings ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row or {}


def serialize_travel_settings(raw):
    raw = raw or {}
    return {
        'id': raw.get('id'),
        'tomtom_api_key': raw.get('tomtom_api_key') or '',
        'price_per_minute': normalize_price_value(raw.get('price_per_minute')) or 0,
        'extended_price_per_minute': normalize_price_value(raw.get('extended_price_per_minute')) or 0,
        'max_service_radius_miles': normalize_price_value(raw.get('max_service_radius_miles')) or 15.0,
        'enable_travel_pricing': bool(raw.get('enable_travel_pricing')),
        'ask_for_postcode': bool(raw.get('ask_for_postcode'))
    }


def upsert_travel_settings(payload):
    ensure_travel_tables()
    tomtom_api_key = (payload.get('tomtom_api_key') or '').strip()
    price_per_minute = normalize_price_value(payload.get('price_per_minute'))
    extended_price_per_minute = normalize_price_value(payload.get('extended_price_per_minute'))
    max_service_radius_miles = normalize_price_value(payload.get('max_service_radius_miles'))
    enable_travel_pricing = int(bool(str_to_bool(payload.get('enable_travel_pricing'))))
    ask_for_postcode = int(bool(str_to_bool(payload.get('ask_for_postcode'))))

    if price_per_minute is None:
        price_per_minute = 0
    if extended_price_per_minute is None:
        extended_price_per_minute = 0
    if max_service_radius_miles is None or max_service_radius_miles <= 0:
        max_service_radius_miles = 15.0

    if enable_travel_pricing and not tomtom_api_key:
        raise ValueError('TomTom API key is required when enabling travel pricing.')

    conn = get_db_connection()
    cursor = conn.cursor()
    values = (
        tomtom_api_key or None,
        price_per_minute,
        extended_price_per_minute,
        max_service_radius_miles,
        enable_travel_pricing,
        ask_for_postcode
    )

    cursor.execute(
        """
        UPDATE settings
        SET tomtom_api_key=%s,
            price_per_minute=%s,
            extended_price_per_minute=%s,
            max_service_radius_miles=%s,
            enable_travel_pricing=%s,
            ask_for_postcode=%s
        WHERE id = 1
        """,
        values
    )
    if cursor.rowcount == 0:
        cursor.execute(
            """
            INSERT INTO settings (id, tomtom_api_key, price_per_minute, extended_price_per_minute, max_service_radius_miles, enable_travel_pricing, ask_for_postcode)
            VALUES (1, %s, %s, %s, %s, %s, %s)
            """,
            values
        )
    conn.commit()
    cursor.close()
    conn.close()
    return fetch_travel_settings()


def _get_app_base_url():
    configured = (os.getenv('APP_BASE_URL') or '').strip().rstrip('/')
    if configured:
        return configured
    try:
        host = (request.host_url or '').strip().rstrip('/')
        if host:
            return host
    except RuntimeError:
        pass
    return 'http://127.0.0.1:5000'


def _build_logo_url(logo_path: str) -> str:
    """Return an absolute URL for logo_path suitable for email <img> tags.
    Handles three cases:
      1. Already an absolute URL (Cloudinary / http) — return as-is.
      2. Relative path starting with 'static/' — prepend base URL only.
      3. Bare path (e.g. 'uploads/brand/x.jpg') — prepend base + '/static/'.
    """
    if not logo_path:
        return ''
    p = logo_path.strip()
    if p.startswith('http://') or p.startswith('https://'):
        return p
    base = _get_app_base_url().rstrip('/')
    if p.startswith('static/'):
        return f"{base}/{p}"
    return f"{base}/static/{p}"


def stripe_secret_key():
    return (os.getenv('STRIPE_SECRET_KEY') or '').strip()


def stripe_secret_key_valid():
    key = stripe_secret_key()
    return bool(key and key.startswith('sk_'))


def stripe_webhook_secret():
    return (os.getenv('STRIPE_WEBHOOK_SECRET') or '').strip()


def stripe_webhook_secret_valid():
    secret = stripe_webhook_secret()
    return bool(secret and secret.startswith('whsec_'))


def stripe_publishable_key():
    return (os.getenv('STRIPE_PUBLISHABLE_KEY') or '').strip()


def stripe_publishable_key_valid():
    key = stripe_publishable_key()
    return bool(key and key.startswith('pk_'))


def stripe_ready():
    return bool(stripe and stripe_secret_key_valid())


def stripe_object_to_dict(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    to_dict_recursive = getattr(value, 'to_dict_recursive', None)
    if callable(to_dict_recursive):
        try:
            converted = to_dict_recursive()
            return converted if isinstance(converted, dict) else {}
        except Exception:
            return {}
    try:
        return dict(value)
    except Exception:
        return {}


_done_ensure_payment_tables = False


def ensure_payment_tables():
    global _done_ensure_payment_tables
    if _done_ensure_payment_tables:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_settings (
                id BIGINT PRIMARY KEY,
                require_payment BOOLEAN DEFAULT FALSE,
                currency VARCHAR(10) DEFAULT 'gbp',
                prebook_discount_enabled BOOLEAN DEFAULT TRUE,
                prebook_discount_percent NUMERIC(5,2) DEFAULT 10.00,
                success_url VARCHAR(600),
                cancel_url VARCHAR(600),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cursor.execute("ALTER TABLE payment_settings ADD COLUMN IF NOT EXISTS prebook_discount_enabled BOOLEAN DEFAULT TRUE")
        cursor.execute("ALTER TABLE payment_settings ADD COLUMN IF NOT EXISTS prebook_discount_percent NUMERIC(5,2) DEFAULT 10.00")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id BIGSERIAL PRIMARY KEY,
                provider VARCHAR(30) DEFAULT 'stripe',
                checkout_session_id VARCHAR(255),
                payment_intent_id VARCHAR(255),
                status VARCHAR(50) DEFAULT 'initiated',
                currency VARCHAR(10) DEFAULT 'gbp',
                amount_total NUMERIC(10,2),
                customer_name VARCHAR(255),
                customer_email VARCHAR(255),
                service_summary TEXT,
                request_payload TEXT,
                prepared_payload TEXT,
                request_id BIGINT,
                service_request_id BIGINT,
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_tx_session ON payment_transactions(checkout_session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_payment_tx_status ON payment_transactions(status)")
        cursor.execute("ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS refund_id VARCHAR(255)")
        cursor.execute("ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS refund_status VARCHAR(50)")
        cursor.execute("ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ")
        cursor.execute("SELECT COUNT(*) FROM payment_settings")
        if (cursor.fetchone() or [0])[0] == 0:
            cursor.execute(
                """
                INSERT INTO payment_settings (id, require_payment, currency, prebook_discount_enabled, prebook_discount_percent, success_url, cancel_url)
                VALUES (1, FALSE, 'gbp', TRUE, 10.00, NULL, NULL)
                """
            )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_settings (
                id INT PRIMARY KEY,
                require_payment TINYINT(1) DEFAULT 0,
                currency VARCHAR(10) DEFAULT 'gbp',
                prebook_discount_enabled TINYINT(1) DEFAULT 1,
                prebook_discount_percent DECIMAL(5,2) DEFAULT 10.00,
                success_url VARCHAR(600),
                cancel_url VARCHAR(600),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("SHOW COLUMNS FROM payment_settings LIKE 'prebook_discount_enabled'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE payment_settings ADD COLUMN prebook_discount_enabled TINYINT(1) DEFAULT 1 AFTER currency")
        cursor.execute("SHOW COLUMNS FROM payment_settings LIKE 'prebook_discount_percent'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE payment_settings ADD COLUMN prebook_discount_percent DECIMAL(5,2) DEFAULT 10.00 AFTER prebook_discount_enabled")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payment_transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                provider VARCHAR(30) DEFAULT 'stripe',
                checkout_session_id VARCHAR(255),
                payment_intent_id VARCHAR(255),
                status VARCHAR(50) DEFAULT 'initiated',
                currency VARCHAR(10) DEFAULT 'gbp',
                amount_total DECIMAL(10,2),
                customer_name VARCHAR(255),
                customer_email VARCHAR(255),
                service_summary TEXT,
                request_payload LONGTEXT,
                prepared_payload LONGTEXT,
                request_id BIGINT,
                service_request_id BIGINT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_payment_tx_session (checkout_session_id),
                INDEX idx_payment_tx_status (status)
            )
            """
        )
        cursor.execute("SELECT COUNT(*) FROM payment_settings")
        if (cursor.fetchone() or [0])[0] == 0:
            cursor.execute(
                """
                INSERT INTO payment_settings (id, require_payment, currency, prebook_discount_enabled, prebook_discount_percent, success_url, cancel_url)
                VALUES (1, 0, 'gbp', 1, 10.00, NULL, NULL)
                """
            )

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_payment_tables = True


def fetch_payment_settings():
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM payment_settings WHERE id = 1")
    row = cursor.fetchone() or {}
    cursor.close()
    conn.close()

    success_url = (row.get('success_url') or '').strip() if row else ''
    cancel_url = (row.get('cancel_url') or '').strip() if row else ''
    base = _get_app_base_url()

    return {
        'id': 1,
        'require_payment': bool(row.get('require_payment')),
        'currency': (row.get('currency') or 'gbp').lower(),
        'prebook_discount_enabled': bool(row.get('prebook_discount_enabled')) if row.get('prebook_discount_enabled') is not None else True,
        'prebook_discount_percent': float(row.get('prebook_discount_percent')) if row.get('prebook_discount_percent') is not None else float(PREBOOK_DISCOUNT_PERCENT),
        'success_url': success_url or f"{base}/payment/callback/success?session_id={{CHECKOUT_SESSION_ID}}",
        'cancel_url': cancel_url or f"{base}/payment/callback/cancel",
        'stripe_enabled': stripe_ready(),
        'stripe_key_detected': bool(stripe_secret_key()),
        'stripe_secret_key_valid': stripe_secret_key_valid(),
        'webhook_configured': stripe_webhook_secret_valid(),
        'webhook_secret_detected': bool(stripe_webhook_secret()),
        'publishable_key': stripe_publishable_key(),
        'publishable_key_configured': stripe_publishable_key_valid()
    }


def upsert_payment_settings(payload):
    ensure_payment_tables()
    require_payment = bool(str_to_bool(payload.get('require_payment')))
    currency = (payload.get('currency') or 'gbp').strip().lower()[:10] or 'gbp'
    prebook_discount_enabled = bool(str_to_bool(payload.get('prebook_discount_enabled', True)))
    raw_discount_percent = payload.get('prebook_discount_percent', PREBOOK_DISCOUNT_PERCENT)
    try:
        prebook_discount_percent = round(float(raw_discount_percent), 2)
    except (TypeError, ValueError):
        raise ValueError('Pre-book discount percent must be a valid number.')
    if prebook_discount_percent < 0 or prebook_discount_percent > 100:
        raise ValueError('Pre-book discount percent must be between 0 and 100.')

    success_url = (payload.get('success_url') or '').strip()[:600]
    cancel_url = (payload.get('cancel_url') or '').strip()[:600]
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if not success_url:
        success_url = f"{_get_app_base_url()}/payment/callback/success?session_id={{CHECKOUT_SESSION_ID}}"
    if not cancel_url:
        cancel_url = f"{_get_app_base_url()}/payment/callback/cancel"

    require_payment_value = require_payment if engine == 'postgres' else (1 if require_payment else 0)
    discount_enabled_value = prebook_discount_enabled if engine == 'postgres' else (1 if prebook_discount_enabled else 0)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE payment_settings
        SET require_payment=%s,
            currency=%s,
            prebook_discount_enabled=%s,
            prebook_discount_percent=%s,
            success_url=%s,
            cancel_url=%s,
            updated_at=CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (require_payment_value, currency, discount_enabled_value, prebook_discount_percent, success_url, cancel_url)
    )
    if cursor.rowcount == 0:
        cursor.execute(
            """
            INSERT INTO payment_settings (id, require_payment, currency, prebook_discount_enabled, prebook_discount_percent, success_url, cancel_url)
            VALUES (1, %s, %s, %s, %s, %s, %s)
            """,
            (require_payment_value, currency, discount_enabled_value, prebook_discount_percent, success_url, cancel_url)
        )
    conn.commit()
    cursor.close()
    conn.close()
    return fetch_payment_settings()


def create_payment_transaction(amount_total, currency, customer_name, customer_email, service_summary, request_payload, prepared_payload):
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO payment_transactions
        (provider, status, currency, amount_total, customer_name, customer_email, service_summary, request_payload, prepared_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        ('stripe', 'initiated', currency, amount_total, customer_name, customer_email, service_summary, request_payload, prepared_payload)
    )
    tx_id = cursor.lastrowid
    if not tx_id:
        cursor.execute("SELECT LASTVAL()")
        fetched = cursor.fetchone()
        tx_id = fetched[0] if fetched else None
    conn.commit()
    cursor.close()
    conn.close()
    return tx_id


def update_payment_transaction(tx_id, **fields):
    if not tx_id or not fields:
        return
    set_parts = []
    values = []
    for key, value in fields.items():
        set_parts.append(f"{key} = %s")
        values.append(value)
    set_parts.append("updated_at = CURRENT_TIMESTAMP")
    values.append(tx_id)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE payment_transactions SET {', '.join(set_parts)} WHERE id = %s",
        values
    )
    conn.commit()
    cursor.close()
    conn.close()


def fetch_payment_transaction_by_session(session_id):
    if not session_id:
        return None
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM payment_transactions WHERE checkout_session_id = %s ORDER BY id DESC LIMIT 1",
        (session_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def fetch_payment_transaction_by_payment_intent(payment_intent_id):
    if not payment_intent_id:
        return None
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM payment_transactions WHERE payment_intent_id = %s ORDER BY id DESC LIMIT 1",
        (payment_intent_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def fetch_payment_transaction_by_id(transaction_id):
    if not transaction_id:
        return None
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM payment_transactions WHERE id = %s LIMIT 1",
        (transaction_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def fetch_payment_transactions(limit=200):
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT id, provider, checkout_session_id, payment_intent_id, status, currency, amount_total,
               customer_name, customer_email, service_summary, request_id, service_request_id, error_message,
               created_at, updated_at
        FROM payment_transactions
        ORDER BY id DESC
        LIMIT %s
        """,
        (max(1, min(int(limit or 200), 1000)),)
    )
    rows = cursor.fetchall() or []
    cursor.close()
    conn.close()
    for row in rows:
        amount = normalize_price_value(row.get('amount_total'))
        row['amount_total'] = amount if amount is not None else None
        for key in ('created_at', 'updated_at'):
            dt_val = row.get(key)
            if isinstance(dt_val, datetime):
                row[key] = dt_val.isoformat()
    return rows


def fetch_operating_bases(include_inactive=False):
    ensure_travel_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if include_inactive:
        clause = ''
        params = ()
    else:
        condition, cond_params = build_active_true_condition('is_active', engine)
        clause = f"WHERE {condition}"
        params = cond_params
    cursor.execute(
        f"""
        SELECT id, name, postcode, latitude, longitude, is_active, created_at, updated_at
        FROM operating_bases
        {clause}
        ORDER BY id ASC
        """,
        params
    )
    bases = cursor.fetchall()
    cursor.close()
    conn.close()
    normalized = []
    for base in bases or []:
        if base.get('latitude') is not None:
            try:
                base['latitude'] = float(base['latitude'])
            except (TypeError, ValueError):
                base['latitude'] = None
        if base.get('longitude') is not None:
            try:
                base['longitude'] = float(base['longitude'])
            except (TypeError, ValueError):
                base['longitude'] = None
        base['is_active'] = bool(base.get('is_active'))
        normalized.append(base)
    return normalized


def upsert_operating_base(payload, base_id=None):
    ensure_travel_tables()
    name = sanitize_text(payload.get('name'), 150)
    postcode = sanitize_text(payload.get('postcode'), 255)
    is_active = bool(str_to_bool(payload.get('is_active', True)))

    if not name or not postcode:
        raise ValueError('Name and postcode are required for an operating base.')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    latitude = None
    longitude = None
    try:
        settings = fetch_travel_settings()
        api_key = settings.get('tomtom_api_key')
        geocoded = geocode_postcode_tomtom(postcode, api_key) if api_key else None
        if geocoded:
            latitude = geocoded.get('lat')
            longitude = geocoded.get('lng')
    except Exception:
        app.logger.warning('Unable to geocode base %s', postcode, exc_info=True)

    if base_id:
        cursor.execute(
            """
            UPDATE operating_bases
            SET name=%s, postcode=%s, latitude=%s, longitude=%s, is_active=%s
            WHERE id = %s
            """,
            (name, postcode, latitude, longitude, is_active, base_id)
        )
    else:
        cursor.execute(
            """
            INSERT INTO operating_bases (name, postcode, latitude, longitude, is_active)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (name, postcode, latitude, longitude, is_active)
        )
        base_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()
    return base_id


def set_operating_base_active(base_id, is_active=True):
    ensure_travel_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE operating_bases SET is_active=%s WHERE id=%s",
        (1 if is_active else 0, base_id)
    )
    conn.commit()
    cursor.close()
    conn.close()


def delete_operating_base(base_id):
    ensure_travel_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM operating_bases WHERE id=%s", (base_id,))
    conn.commit()
    cursor.close()
    conn.close()


def _normalize_postcode_key(postcode):
    return (postcode or '').strip().upper().replace(' ', '')


def _get_cached_travel_quote(postcode):
    key = _normalize_postcode_key(postcode)
    if not key:
        return None
    now = time.time()
    with _travel_cache_lock:
        entry = _travel_quote_cache.get(key)
        if not entry:
            return None
        if entry.get('expires_at', 0) <= now:
            _travel_quote_cache.pop(key, None)
            return None
        return dict(entry['quote'])


def _store_cached_travel_quote(postcode, quote):
    key = _normalize_postcode_key(postcode)
    if not key or not isinstance(quote, dict):
        return
    payload = dict(quote)
    expires_at = time.time() + TRAVEL_CACHE_TTL_SECONDS
    with _travel_cache_lock:
        _travel_quote_cache[key] = {'quote': payload, 'expires_at': expires_at}


def _store_session_travel_quote(postcode, quote):
    key = _normalize_postcode_key(postcode)
    if not key or not isinstance(quote, dict):
        return
    try:
        cache = session.get('travel_quote_cache') or {}
        cache[key] = {
            'quote': quote,
            'ts': int(time.time())
        }
        if len(cache) > 5:
            ordered = sorted(cache.items(), key=lambda item: item[1].get('ts', 0))
            for stale_key, _ in ordered[:-5]:
                cache.pop(stale_key, None)
        session['travel_quote_cache'] = cache
    except Exception as exc:
        app.logger.debug('Unable to persist travel quote in session: %s', exc)


def _get_session_travel_quote(postcode):
    key = _normalize_postcode_key(postcode)
    if not key:
        return None
    cache = session.get('travel_quote_cache') or {}
    entry = cache.get(key)
    if not entry:
        return None
    ts = entry.get('ts') or 0
    if time.time() - ts > SESSION_TRAVEL_CACHE_TTL_SECONDS:
        cache.pop(key, None)
        session['travel_quote_cache'] = cache
        return None
    quote = entry.get('quote')
    if isinstance(quote, dict):
        _store_cached_travel_quote(postcode, quote)
        return dict(quote)
    return None


def geocode_postcode_nominatim(postcode):
    """Geocode a postcode via Nominatim (no API key required). Returns {'lat': float, 'lng': float} or None."""
    if not postcode:
        return None
    try:
        url = 'https://nominatim.openstreetmap.org/search'
        params = {'format': 'json', 'limit': '1', 'q': postcode}
        headers = {'User-Agent': 'CleaningApp/1.0'}
        resp = requests.get(url, params=params, headers=headers, timeout=8)
        resp.raise_for_status()
        results = resp.json() or []
        if results:
            return {'lat': float(results[0]['lat']), 'lng': float(results[0]['lon'])}
    except Exception:
        pass
    return None


def geocode_bases_if_needed(bases):
    """For any bases missing lat/lng, geocode via Nominatim and persist the result to DB."""
    if not bases:
        return bases
    needs_geocode = [b for b in bases if b.get('latitude') is None or b.get('longitude') is None]
    if not needs_geocode:
        return bases
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        import time as _time
        for base in needs_geocode:
            coords = geocode_postcode_nominatim(base.get('postcode', ''))
            if coords:
                base['latitude'] = coords['lat']
                base['longitude'] = coords['lng']
                cursor.execute(
                    "UPDATE operating_bases SET latitude=%s, longitude=%s WHERE id=%s",
                    (coords['lat'], coords['lng'], base['id'])
                )
            _time.sleep(1.1)  # Nominatim usage policy: max 1 req/sec
        conn.commit()
        cursor.close()
        conn.close()
    except Exception:
        app.logger.exception('Failed to geocode operating bases')
    return bases


def geocode_postcode_tomtom(postcode, api_key):
    if not postcode or not api_key:
        return None
    url = f"https://api.tomtom.com/search/2/geocode/{urllib.parse.quote(postcode)}.json"
    response = requests.get(url, params={"key": api_key, "limit": 1}, timeout=10)
    response.raise_for_status()
    data = response.json() or {}
    results = data.get("results") or []
    if not results:
        return None
    position = results[0].get("position") or {}
    lat = position.get("lat")
    lon = position.get("lon")
    if lat is None or lon is None:
        return None
    return {"lat": float(lat), "lng": float(lon)}


def calculate_route_tomtom(origin, destination, api_key):
    if not origin or not destination or not api_key:
        return None
    route_url = "https://api.tomtom.com/routing/1/calculateRoute/{},{}:{},{}".format(
        origin['lat'], origin['lng'], destination['lat'], destination['lng']
    )
    params = {
        "key": api_key,
        "travelMode": "car"
    }
    try:
        response = requests.get(f"{route_url}/json", params=params, timeout=12)
        response.raise_for_status()
    except requests.exceptions.RequestException as req_err:
        # Gracefully handle all TomTom errors (unreachable route, bad coordinates, timeouts, etc.)
        status = None
        detail = None
        if hasattr(req_err, 'response') and req_err.response is not None:
            status = req_err.response.status_code
            try:
                detail = (req_err.response.json() or {}).get('errorText')
            except Exception:
                detail = str(req_err)[:100]
        else:
            detail = str(req_err)[:100]
        app.logger.debug("TomTom routing failed: status=%s detail=%s", status, detail)
        # Return None for any routing failure - don't raise
        return None
    payload = response.json() or {}
    routes = payload.get('routes') or []
    if not routes:
        return None
    summary = routes[0].get('summary') or {}
    distance_meters = summary.get('lengthInMeters')
    travel_seconds = summary.get('travelTimeInSeconds')
    distance_miles = round(float(distance_meters) / 1609.344, 2) if distance_meters is not None else None
    travel_minutes = round(float(travel_seconds) / 60) if travel_seconds is not None else None
    return {
        'distance_miles': distance_miles,
        'travel_time_minutes': travel_minutes,
        'pricing_method': 'tomtom'
    }


def _geocode_base_if_needed(base_row, api_key):
    if not api_key:
        return None
    if base_row.get('latitude') is not None and base_row.get('longitude') is not None:
        return {'lat': float(base_row['latitude']), 'lng': float(base_row['longitude'])}

    origin = geocode_postcode_tomtom(base_row.get('postcode'), api_key)
    if not origin:
        return None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE operating_bases SET latitude=%s, longitude=%s WHERE id=%s",
            (origin['lat'], origin['lng'], base_row.get('id'))
        )
        conn.commit()
    except Exception:
        app.logger.debug('Failed to cache geocode for base %s', base_row.get('id'))
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass
    return origin


def resolve_travel_time(user_postcode, settings=None):
    settings = settings or fetch_travel_settings()
    api_key = (settings or {}).get('tomtom_api_key')
    price_per_minute = normalize_price_value((settings or {}).get('price_per_minute')) or 0
    max_radius = normalize_price_value((settings or {}).get('max_service_radius_miles')) or 15.0
    user_postcode = sanitize_text(user_postcode, 255)

    if not user_postcode:
        raise ValueError('Customer postcode/address is required to calculate travel distance.')
    if not api_key:
        raise ValueError('Travel pricing is unavailable. Please add a TomTom API key.')

    bases = fetch_operating_bases(include_inactive=False)
    if not bases:
        raise ValueError('Travel pricing is unavailable. Please configure at least one operating base.')

    try:
        destination = geocode_postcode_tomtom(user_postcode, api_key)
    except Exception:
        app.logger.warning('TomTom geocoding failed for: %s', user_postcode)
        destination = None

    if not destination:
        raise ValueError('Unable to locate the address provided. Please enter a valid UK postcode or address.')

    best_within_radius = None
    best_overall = None  # Track closest base even if outside radius
    routing_failures = 0  # Track consecutive routing failures
    max_routing_failures = 3  # Stop early if destination is likely unreachable
    
    app.logger.info('Calculating travel from %d bases to: %s', len(bases), user_postcode)
    
    for base in bases:
        # Early exit if routing keeps failing (likely unreachable destination like overseas)
        if routing_failures >= max_routing_failures and best_overall is None:
            app.logger.info('Stopping route checks after %d failures - destination may be unreachable: %s', routing_failures, user_postcode)
            break
        
        origin = None
        try:
            origin = _geocode_base_if_needed(base, api_key)
        except Exception:
            app.logger.debug('Failed to geocode base %s', base.get('id'))
            origin = None

        if not origin:
            app.logger.debug('Skipping base %s (%s) - no coordinates', base.get('id'), base.get('name'))
            continue

        route = calculate_route_tomtom(origin, destination, api_key)

        if not route:
            routing_failures += 1
            app.logger.debug('Routing failed for base %s (%s)', base.get('id'), base.get('name'))
            continue
        
        # Reset failure count on success
        routing_failures = 0

        distance = route.get('distance_miles')
        travel_time = route.get('travel_time_minutes')
        is_within_radius = not (max_radius and distance is not None and distance > max_radius)
        
        app.logger.info('Base %s (%s): distance=%.1f miles, time=%d mins, within_radius=%s', 
                       base.get('id'), base.get('name'), distance or 0, travel_time or 0, is_within_radius)
        
        # Track best within radius
        if is_within_radius:
            if best_within_radius is None:
                best_within_radius = {'base': base, 'route': route}
            else:
                current_best_time = best_within_radius['route'].get('travel_time_minutes')
                if current_best_time is None and travel_time is not None:
                    best_within_radius = {'base': base, 'route': route}
                elif current_best_time is not None and travel_time is not None and travel_time < current_best_time:
                    best_within_radius = {'base': base, 'route': route}
        
        # Track best overall (closest base regardless of radius)
        if best_overall is None:
            best_overall = {'base': base, 'route': route}
        else:
            current_overall_time = best_overall['route'].get('travel_time_minutes')
            if current_overall_time is None and travel_time is not None:
                best_overall = {'base': base, 'route': route}
            elif current_overall_time is not None and travel_time is not None and travel_time < current_overall_time:
                best_overall = {'base': base, 'route': route}

    # Use best within radius if available, otherwise use closest base (extended coverage)
    best = best_within_radius
    is_extended_coverage = False
    
    if not best and best_overall:
        # Customer is outside normal radius but we can still serve them
        best = best_overall
        is_extended_coverage = True
    
    if not best:
        # All routing attempts failed - likely unreachable (overseas, etc.)
        # Build service area names for the error message
        service_area_names = [b.get('name') for b in bases if b.get('name')]
        if service_area_names:
            areas_text = ', '.join(service_area_names[:5])
            if len(service_area_names) > 5:
                areas_text += f' and {len(service_area_names) - 5} more'
            raise ValueError(f'We cannot reach this location by road. Our service areas include: {areas_text}. Please enter a valid UK address.')
        raise ValueError('We cannot reach this location by road. Please enter a UK address within our service area.')

    # Log the final selection
    app.logger.info('Selected base: %s (%s) - distance=%.1f miles, time=%d mins, extended_coverage=%s',
                   best['base'].get('id'), best['base'].get('name'),
                   best['route'].get('distance_miles') or 0, 
                   best['route'].get('travel_time_minutes') or 0,
                   is_extended_coverage)

    route = best['route']
    route['pricing_method'] = 'tomtom'
    route['base_id'] = best['base'].get('id')
    route['base_name'] = best['base'].get('name')
    route['base_postcode'] = best['base'].get('postcode')
    route['max_service_radius_miles'] = max_radius
    route['price_per_minute'] = price_per_minute
    route['is_extended_coverage'] = is_extended_coverage
    # Include customer destination coordinates for map link
    route['customer_lat'] = destination.get('lat') if destination else None
    route['customer_lng'] = destination.get('lng') if destination else None
    route['customer_postcode'] = user_postcode
    return route


def calculate_travel_cost(user_postcode):
    cached = _get_cached_travel_quote(user_postcode)
    if cached:
        return cached

    settings = fetch_travel_settings() or {}

    api_key = (settings.get('tomtom_api_key') or '').strip()
    price_per_minute = normalize_price_value(settings.get('price_per_minute')) or 0
    extended_price_per_minute = normalize_price_value(settings.get('extended_price_per_minute')) or 0
    enable_pricing = bool(settings.get('enable_travel_pricing'))

    if not enable_pricing:
        return {
            'travel_fee': 0,
            'distance_miles': None,
            'travel_time_minutes': None,
            'pricing_method': 'disabled'
        }

    user_postcode = sanitize_text(user_postcode, 255)
    if not user_postcode:
        raise ValueError('Please provide a postcode or address for travel pricing.')
    if not api_key:
        raise ValueError('Travel pricing is temporarily unavailable. Please contact the team.')

    travel_data = resolve_travel_time(user_postcode, settings)
    travel_minutes = travel_data.get('travel_time_minutes')
    if travel_minutes is None:
        raise ValueError('Unable to calculate travel time right now.')

    is_extended_coverage = travel_data.get('is_extended_coverage', False)
    
    if is_extended_coverage and extended_price_per_minute > 0:
        # Extended coverage: charge for round-trip (2x travel time) at extended rate
        travel_fee = round((travel_minutes * 2) * extended_price_per_minute, 2)
    else:
        # Normal coverage: one-way travel at standard rate
        travel_fee = round(travel_minutes * price_per_minute, 2)
    
    result = {
        'travel_fee': travel_fee,
        'distance_miles': travel_data.get('distance_miles'),
        'travel_time_minutes': travel_minutes,
        'pricing_method': travel_data.get('pricing_method') or 'tomtom',
        'base_id': travel_data.get('base_id'),
        'base_name': travel_data.get('base_name'),
        'base_postcode': travel_data.get('base_postcode'),
        'max_service_radius_miles': travel_data.get('max_service_radius_miles'),
        'is_extended_coverage': is_extended_coverage
    }
    _store_cached_travel_quote(user_postcode, result)
    return result


def parse_price_input(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        cleaned = str(value).strip()
        if not cleaned:
            return None
        return float(cleaned)


def normalize_service_category(value):
    normalized = sanitize_text(value, 40)
    normalized = (normalized or '').strip().lower().replace('-', '_').replace(' ', '_')
    if normalized in {'contract', 'contract_based', 'contracted'}:
        return 'contract'
    if normalized == 'hybrid':
        return 'hybrid'
    return 'one_time'


def normalize_contract_frequency(value):
    normalized = sanitize_text(value, 30)
    normalized = (normalized or '').strip().lower().replace('-', '_').replace(' ', '_')
    if normalized in {'weekly', 'fortnightly', 'monthly'}:
        return normalized
    return ''


def format_contract_frequency_label(value):
    normalized = normalize_contract_frequency(value)
    if normalized == 'weekly':
        return 'Weekly'
    if normalized == 'fortnightly':
        return 'Fortnightly (Every 2 weeks)'
    if normalized == 'monthly':
        return 'Monthly'
    return ''


def add_months_to_date(base_date, months):
    if not isinstance(base_date, date):
        return base_date
    total_month = (base_date.month - 1) + int(months or 0)
    year = base_date.year + (total_month // 12)
    month = (total_month % 12) + 1
    day = min(base_date.day, [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def advance_contract_date(base_date, frequency):
    normalized = normalize_contract_frequency(frequency)
    if not isinstance(base_date, date):
        return base_date
    if normalized == 'weekly':
        return base_date + timedelta(days=7)
    if normalized == 'fortnightly':
        return base_date + timedelta(days=14)
    if normalized == 'monthly':
        return add_months_to_date(base_date, 1)
    return base_date


def combine_contract_datetime(service_date, preferred_time='09:00'):
    if not isinstance(service_date, date):
        return None
    time_text = sanitize_text(preferred_time, 32) or '09:00'
    try:
        hour_part, minute_part = [int(part) for part in time_text.split(':', 1)]
    except Exception:
        hour_part, minute_part = 9, 0
    hour_part = max(0, min(23, hour_part))
    minute_part = max(0, min(59, minute_part))
    return datetime(service_date.year, service_date.month, service_date.day, hour_part, minute_part)


def default_contract_pricing_plans():
    return [
        {'key': 'weekly', 'label': 'Weekly', 'price': 17.99},
        {'key': 'fortnightly', 'label': 'Fortnightly', 'price': 19.99},
        {'key': 'monthly', 'label': 'Monthly', 'price': 24.99}
    ]


def parse_contract_pricing_plans(raw_value):
    if raw_value in (None, ''):
        return []

    plans = default_contract_pricing_plans()
    parsed = raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except Exception:
            return []

    if isinstance(parsed, dict):
        parsed = parsed.get('plans') or []

    if not isinstance(parsed, list) or not parsed:
        return []

    keyed = {item['key']: dict(item) for item in plans}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        key = normalize_contract_frequency(item.get('key'))
        if key not in keyed:
            continue
        price = normalize_price_value(item.get('price'))
        keyed[key]['price'] = price
        if item.get('label'):
            keyed[key]['label'] = sanitize_text(item.get('label'), 80) or keyed[key]['label']

    return [keyed['weekly'], keyed['fortnightly'], keyed['monthly']]


def build_contract_pricing_plans_from_form(form_data):
    return [
        {'key': 'weekly', 'label': 'Weekly', 'price': normalize_price_value(form_data.get('contract_price_weekly'))},
        {'key': 'fortnightly', 'label': 'Fortnightly', 'price': normalize_price_value(form_data.get('contract_price_fortnightly'))},
        {'key': 'monthly', 'label': 'Monthly', 'price': normalize_price_value(form_data.get('contract_price_monthly'))}
    ]


def calculate_next_reminder_at(preferred_date_value, frequency):
    if not preferred_date_value or not frequency:
        return None
    if isinstance(preferred_date_value, datetime):
        service_date = preferred_date_value.date()
    else:
        service_date = parse_preferred_date(preferred_date_value)
    if not service_date:
        return None

    reminder_date = service_date - timedelta(days=1)
    reminder_dt = datetime.combine(reminder_date, datetime.min.time()) + timedelta(hours=9)
    if reminder_dt <= datetime.now(timezone.utc).replace(tzinfo=None):
        if frequency == 'weekly':
            service_date = service_date + timedelta(days=7)
        elif frequency == 'fortnightly':
            service_date = service_date + timedelta(days=14)
        elif frequency == 'monthly':
            month = service_date.month + 1
            year = service_date.year
            if month > 12:
                month = 1
                year += 1
            day = min(service_date.day, calendar.monthrange(year, month)[1])
            service_date = service_date.replace(year=year, month=month, day=day)
        reminder_date = service_date - timedelta(days=1)
        reminder_dt = datetime.combine(reminder_date, datetime.min.time()) + timedelta(hours=9)
    return reminder_dt

def fetch_services_from_db(include_inactive=False):
    try:
        migrate_domestic_to_services()
    except Exception:
        app.logger.exception('Error during domestic-to-services migration (fetch_services_from_db)')

    try:
        ensure_residential_contract_service()
    except Exception:
        app.logger.exception('Error ensuring fallback Residential Cleaning contract service')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if include_inactive:
        where_clause = ""
        params = ()
    else:
        condition, cond_params = build_active_true_condition('s.is_active', engine)
        where_clause = f"WHERE {condition}"
        params = cond_params
    cursor.execute(
        f"""
        SELECT
            s.id,
            s.title,
            s.name,
            s.short_description,
            s.description,
            s.price,
            s.discount_threshold,
            s.discount_percent,
            s.service_category,
            s.contract_pricing_plans,
            s.is_contract,
            s.contract_section_title,
            s.contract_section_subtitle,
            s.contract_intro_title,
            s.contract_intro_body,
            s.contract_trust_body,
            s.contract_trust_image,
            s.contract_continuity_body,
            s.contract_continuity_image,
            s.pricing_model,
            s.table_header_col1,
            s.table_header_col2,
            s.table_header_col3,
            s.allow_multiselect,
            s.image_path,
            s.is_active,
            s.created_at,
            s.updated_at
        FROM services s
        {where_clause}
        ORDER BY s.id ASC
        """,
        params
    )
    services_rows = cursor.fetchall()

    services = OrderedDict()
    for row in services_rows:
        created_at = row.get('created_at')
        updated_at = row.get('updated_at')
        service_id = row.get('id')
        _raw_cat = normalize_service_category(row.get('service_category'))
        is_contract = bool(row.get('is_contract')) or _raw_cat == 'contract'
        # Hybrid: stored as is_contract=True but category='hybrid' — preserve that
        if _raw_cat == 'hybrid':
            _svc_category = 'hybrid'
            is_contract = True  # hybrid still gets contract pricing plans
        else:
            _svc_category = 'contract' if is_contract else _raw_cat
        services[service_id] = {
            'id': service_id,
            'title': row.get('title'),
            'name': row.get('name') or row.get('title'),
            'short_description': row.get('short_description') or '',
            'description': row.get('description'),
            'price': normalize_price_value(row.get('price')),
            'discount_threshold': normalize_price_value(row.get('discount_threshold')),
            'discount_percent': normalize_price_value(row.get('discount_percent')),
            'service_category': _svc_category,
            'is_contract': is_contract,
            'contract_pricing_plans': parse_contract_pricing_plans(row.get('contract_pricing_plans')),
            'contract_section_title': row.get('contract_section_title') or '',
            'contract_section_subtitle': row.get('contract_section_subtitle') or '',
            'contract_intro_title': row.get('contract_intro_title') or '',
            'contract_intro_body': row.get('contract_intro_body') or '',
            'contract_trust_body': row.get('contract_trust_body') or '',
            'contract_trust_image': row.get('contract_trust_image') or '',
            'contract_continuity_body': row.get('contract_continuity_body') or '',
            'contract_continuity_image': row.get('contract_continuity_image') or '',
            'room_cards': [],
            'pricing_model': row.get('pricing_model') or 'simple',
            'table_header_col1': row.get('table_header_col1') or 'Property Type',
            'table_header_col2': row.get('table_header_col2') or 'Standard Price',
            'table_header_col3': row.get('table_header_col3') or 'Upgrade Option',
            'allow_multiselect': row.get('allow_multiselect') or 0,
            'image_path': row.get('image_path'),
            'is_active': bool(row.get('is_active')),
            'created_at': created_at.isoformat() if isinstance(created_at, datetime) else created_at,
            'updated_at': updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
            'options': [],
            'pricing_items': [],
            'pricing_tiers': [],
            'tenancy_rates': [],
            'pricing_type': None
        }

    service_ids = list(services.keys())
    if service_ids:
        placeholders = ','.join(['%s'] * len(service_ids))

        # Legacy options
        option_where = f"WHERE o.service_id IN ({placeholders})"
        option_params = list(service_ids)
        if not include_inactive:
            condition, cond_params = build_active_true_condition('o.is_active', engine)
            option_where += f" AND {condition}"
            option_params.extend(cond_params)
        cursor.execute(
            f"""
            SELECT o.id, o.service_id, o.label, o.price, o.sort_order, o.is_active
            FROM service_options o
            {option_where}
            ORDER BY o.service_id ASC, o.sort_order ASC, o.id ASC
            """,
            option_params
        )
        for opt in cursor.fetchall():
            svc = services.get(opt['service_id'])
            if svc:
                svc['options'].append({
                    'id': opt['id'],
                    'label': opt.get('label'),
                    'price': normalize_price_value(opt.get('price')),
                    'sort_order': opt.get('sort_order') or 0,
                    'is_active': bool(opt.get('is_active'))
                })

        # Pricing items (carpet/upholstery)
        cursor.execute(
            f"""
            SELECT id, service_id, item_name, price
            FROM service_pricing_items
            WHERE service_id IN ({placeholders})
            ORDER BY service_id ASC, id ASC
            """,
            service_ids
        )
        for item in cursor.fetchall():
            svc = services.get(item['service_id'])
            if svc:
                svc['pricing_items'].append({
                    'id': item['id'],
                    'item_name': item.get('item_name'),
                    'price': normalize_price_value(item.get('price'))
                })

        # Pricing tiers (deep cleaning hourly)
        cursor.execute(
            f"""
            SELECT id, service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee
            FROM service_pricing_tiers
            WHERE service_id IN ({placeholders})
            ORDER BY service_id ASC, id ASC
            """,
            service_ids
        )
        for tier in cursor.fetchall():
            svc = services.get(tier['service_id'])
            if svc:
                svc['pricing_tiers'].append({
                    'id': tier['id'],
                    'tier_name': tier.get('tier_name'),
                    'hourly_rate': normalize_price_value(tier.get('hourly_rate')),
                    'min_staff': tier.get('min_staff'),
                    'equipment_fee': normalize_price_value(tier.get('equipment_fee')),
                    'detergent_fee': normalize_price_value(tier.get('detergent_fee'))
                })

        # Tenancy rates
        cursor.execute(
            f"""
            SELECT id, service_id, label, standard_price, deep_clean_price, is_blocker, blocker_msg
            FROM service_tenancy_rates
            WHERE service_id IN ({placeholders})
            ORDER BY service_id ASC, id ASC
            """,
            service_ids
        )
        for rate in cursor.fetchall():
            svc = services.get(rate['service_id'])
            if svc:
                svc['tenancy_rates'].append({
                    'id': rate['id'],
                    'label': rate.get('label'),
                    'standard_price': normalize_price_value(rate.get('standard_price')),
                    'deep_clean_price': normalize_price_value(rate.get('deep_clean_price')),
                    'is_blocker': bool(rate.get('is_blocker')),
                    'blocker_msg': rate.get('blocker_msg')
                })

        # Room cards for contract services
        room_card_where = f"WHERE rc.service_id IN ({placeholders})"
        room_card_params = list(service_ids)
        if not include_inactive:
            condition, cond_params = build_active_true_condition('rc.is_active', engine)
            room_card_where += f" AND {condition}"
            room_card_params.extend(cond_params)
        cursor.execute(
            f"""
            SELECT rc.id, rc.service_id, rc.card_key, rc.room_name, rc.lifestyle_copy, rc.image_path, rc.sort_order, rc.is_active
            FROM service_room_cards rc
            {room_card_where}
            ORDER BY rc.service_id ASC, rc.sort_order ASC, rc.id ASC
            """,
            room_card_params
        )
        for card in cursor.fetchall():
            svc = services.get(card['service_id'])
            if svc:
                svc['room_cards'].append({
                    'id': card['id'],
                    'service_id': card['service_id'],
                    'card_key': card.get('card_key'),
                    'room_name': card.get('room_name'),
                    'lifestyle_copy': card.get('lifestyle_copy'),
                    'image_path': card.get('image_path'),
                    'sort_order': card.get('sort_order', 0),
                    'is_active': bool(card.get('is_active'))
                })

        # Set pricing_type based on pricing_model or infer from data for backwards compatibility
        for svc in services.values():
            pricing_model = svc.get('pricing_model')
            if pricing_model and pricing_model != 'simple':
                # Use explicit pricing_model as pricing_type
                svc['pricing_type'] = pricing_model
            elif svc['tenancy_rates']:
                svc['pricing_type'] = 'tenancy'
            elif svc['pricing_tiers']:
                svc['pricing_type'] = 'deep'
            elif svc['pricing_items']:
                svc['pricing_type'] = 'itemized'
            elif svc['options']:
                svc['pricing_type'] = 'options'
            else:
                svc['pricing_type'] = 'simple'

    cursor.close()
    conn.close()
    return list(services.values())


def format_currency_label(value):
    normalized = normalize_price_value(value)
    if normalized is None:
        return 'Custom quote'
    return f"£{normalized:,.2f}"


def format_friendly_datetime(dt_value):
    """Format datetime as 'Thursday 12th December 2025, 12:30 PM'."""
    if not dt_value:
        return ''
    if isinstance(dt_value, str):
        try:
            dt_value = datetime.fromisoformat(dt_value.replace('Z', '+00:00'))
        except ValueError:
            return dt_value
    if not isinstance(dt_value, datetime):
        return str(dt_value) if dt_value else ''
    
    day = dt_value.day
    # Add ordinal suffix
    if 11 <= day <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    
    return dt_value.strftime(f'%A {day}{suffix} %B %Y, %I:%M %p')


def parse_preferred_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def resolve_service_selections(raw_selections):
    if not isinstance(raw_selections, list) or not raw_selections:
        raise ValueError('Please select at least one service to continue.')

    # Collect service ids
    service_ids = []
    for entry in raw_selections:
        if isinstance(entry, dict) and entry.get('service_id'):
            service_id_raw = entry.get('service_id')
            if isinstance(service_id_raw, str) and service_id_raw.startswith('domestic_'):
                continue
            try:
                service_ids.append(int(service_id_raw))
            except (TypeError, ValueError):
                continue
    service_ids = list({sid for sid in service_ids if sid})

    # Load services with pricing data
    services = fetch_services_from_db(include_inactive=False)
    service_map = {svc['id']: svc for svc in services if svc['id'] in service_ids}

    resolved = []
    pricing_details = []
    subtotal = 0.0
    has_custom_price = False

    for entry in raw_selections:
        if not isinstance(entry, dict):
            continue

        service_id_raw = entry.get('service_id')
        if isinstance(service_id_raw, str) and service_id_raw.startswith('domestic_'):
            continue

        service_id = service_id_raw
        try:
            service_id = int(service_id)
        except (TypeError, ValueError):
            service_id = None

        if not service_id or service_id not in service_map:
            raise ValueError('One of the selected services is invalid or unavailable.')

        service = service_map[service_id]
        pricing_model = entry.get('pricing_model') or entry.get('model_type') or service.get('pricing_type')
        payload = entry.get('pricing_payload') or {}
        is_survey_request = bool(payload.get('is_survey_request') or entry.get('is_survey_request'))

        resolved_item = {
            'service_id': service_id,
            'service_name': service.get('name') or service.get('title'),
            'service_category': normalize_service_category(service.get('service_category')),
            'service_option_id': None,
            'option_label': None,
            'price': None,
            'is_survey_request': is_survey_request
        }

        detail = {}

        if is_survey_request:
            resolved_item.update({
                'service_option_id': None,
                'option_label': sanitize_text(entry.get('option_label') or 'Survey required', 150),
                'price': None
            })
            detail = {
                'model': 'survey_required',
                'is_survey_request': True,
                'option_label': resolved_item['option_label']
            }
            has_custom_price = True
            resolved.append(resolved_item)
            pricing_details.append({**detail, 'service_id': service_id})
            continue

        if pricing_model in ('tenancy', 'tenancy_rates'):
            rate_id = payload.get('rate_id') or payload.get('selection')
            variant = (payload.get('variant') or 'standard').lower()
            rate = next((r for r in service.get('tenancy_rates', []) if int(r['id']) == int(rate_id)), None)
            if not rate:
                raise ValueError('Selected property size is no longer available.')
            if rate.get('is_blocker'):
                raise ValueError(rate.get('blocker_msg') or 'This property requires a survey. Please contact the team.')
            price_value = normalize_price_value(rate.get('deep_clean_price') if variant == 'deep' else rate.get('standard_price'))
            # Build detailed option label with property size and clean type
            property_label = rate.get('label') or 'Property'
            clean_type = 'Deep Clean' if variant == 'deep' else 'Standard Clean'
            detailed_label = f"{property_label} • {clean_type}"
            resolved_item.update({
                'service_option_id': None,
                'option_label': detailed_label,
                'price': price_value
            })
            detail = {
                'model': 'tenancy',
                'rate_id': rate.get('id'),
                'variant': variant,
                'label': rate.get('label'),
                'price': price_value
            }

        elif pricing_model in ('deep', 'deep_tiers', 'airbnb'):
            tier_id = payload.get('tier_id') or payload.get('selection') or payload.get('option_id')
            tier = next((t for t in service.get('pricing_tiers', []) if int(t['id']) == int(tier_id)), None)
            if not tier:
                raise ValueError('Selected deep cleaning tier is no longer available.')
            min_staff = int(tier.get('min_staff') or 1)
            hours = float(payload.get('hours') or 0)
            staff = int(payload.get('staff') or min_staff)
            if staff < min_staff:
                raise ValueError(f"Minimum staff for this tier is {min_staff}.")
            if hours <= 0:
                raise ValueError('Please provide estimated hours greater than 0.')
            hourly_rate = normalize_price_value(tier.get('hourly_rate'))
            if hourly_rate is None:
                raise ValueError('This tier does not have a valid hourly rate yet.')
            equipment_fee = normalize_price_value(tier.get('equipment_fee')) or 0
            detergent_fee = normalize_price_value(tier.get('detergent_fee')) or 0
            price_value = round((hourly_rate * staff * hours) + equipment_fee + detergent_fee, 2)
            # Build detailed option label with all parameters
            tier_name = tier.get('tier_name') or 'Deep clean'
            detail_parts = [tier_name, f"Staff: {staff}", f"Hours: {hours}"]
            detailed_label = ' • '.join(detail_parts)
            resolved_item.update({
                'service_option_id': None,
                'option_label': detailed_label,
                'price': price_value
            })
            detail = {
                'model': 'airbnb' if pricing_model == 'airbnb' else 'deep',
                'tier_id': tier.get('id'),
                'tier_name': tier.get('tier_name'),
                'staff': staff,
                'hours': hours,
                'hourly_rate': hourly_rate,
                'equipment_fee': equipment_fee,
                'detergent_fee': detergent_fee,
                'price': price_value
            }

        elif pricing_model in ('itemized', 'itemized_discount'):
            quantities = payload.get('quantities') or {}
            if not isinstance(quantities, dict) or not quantities:
                raise ValueError('Please select at least one item.')
            itemized_subtotal = 0.0
            lines = []
            item_details = []  # For readable display
            for item in service.get('pricing_items', []):
                qty = 0
                try:
                    qty = int(quantities.get(str(item['id'])) or quantities.get(item['id']) or 0)
                except (TypeError, ValueError):
                    qty = 0
                price = normalize_price_value(item.get('price')) or 0
                if qty > 0:
                    itemized_subtotal += qty * price
                    lines.append(f"{qty} × {item.get('item_name')}")
                    item_details.append(f"{item.get('item_name')} (x{qty})")
            if itemized_subtotal <= 0:
                raise ValueError('Please select at least one item to continue.')

            discount_threshold = normalize_price_value(service.get('discount_threshold')) or 0
            discount_percent = normalize_price_value(service.get('discount_percent')) or 0
            discount_amount = 0.0
            if discount_threshold and discount_percent and itemized_subtotal > discount_threshold:
                discount_amount = round(itemized_subtotal * (discount_percent / 100), 2)
            price_value = round(itemized_subtotal - discount_amount, 2)

            # Generate detailed label from actual items instead of 'Custom selection'
            detailed_label = ', '.join(item_details) if item_details else 'Custom selection'

            resolved_item.update({
                'service_option_id': None,
                'option_label': detailed_label,
                'price': price_value
            })
            detail = {
                'model': 'itemized',
                'quantities': quantities,
                'lines': lines,
                'item_details': item_details,
                'subtotal': itemized_subtotal,
                'discount_threshold': discount_threshold,
                'discount_percent': discount_percent,
                'discount_amount': discount_amount,
                'price': price_value
            }

        elif payload.get('type') == 'multiselect_options' or (payload.get('selectedOptions') and isinstance(payload.get('selectedOptions'), list)):
            # Multiselect options with discount support
            selected_options = payload.get('selectedOptions', [])
            if not selected_options:
                raise ValueError('Please select at least one option.')
            
            # Validate all selected options exist and are active
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
            option_ids = [opt.get('id') for opt in selected_options if opt.get('id')]
            if not option_ids:
                cursor.close()
                conn.close()
                raise ValueError('Invalid option selection.')
            
            placeholders = ','.join(['%s'] * len(option_ids))
            condition, cond_params = build_active_true_condition('o.is_active', engine)
            cursor.execute(
                f"""
                SELECT o.id AS option_id, o.label AS option_label, o.price AS option_price, o.is_active AS option_active
                FROM service_options o
                WHERE o.id IN ({placeholders}) AND o.service_id = %s AND {condition}
                """,
                (*option_ids, service_id, *cond_params)
            )
            valid_options = {row['option_id']: row for row in cursor.fetchall()}
            cursor.close()
            conn.close()

            # Calculate subtotal from valid options
            subtotal_multiselect = 0.0
            option_labels = []
            for opt in selected_options:
                opt_id = opt.get('id')
                if opt_id not in valid_options:
                    raise ValueError('One of the selected options is no longer available.')
                db_opt = valid_options[opt_id]
                opt_price = normalize_price_value(db_opt.get('option_price')) or 0
                subtotal_multiselect += opt_price
                option_labels.append(db_opt.get('option_label'))

            if subtotal_multiselect <= 0:
                raise ValueError('Please select at least one priced option.')

            # Apply discount if threshold met
            discount_threshold = normalize_price_value(service.get('discount_threshold')) or 0
            discount_percent = normalize_price_value(service.get('discount_percent')) or 0
            discount_amount = 0.0
            if discount_threshold > 0 and discount_percent > 0 and subtotal_multiselect > discount_threshold:
                discount_amount = round(subtotal_multiselect * (discount_percent / 100), 2)
            price_value = round(subtotal_multiselect - discount_amount, 2)

            resolved_item.update({
                'service_option_id': option_ids[0] if option_ids else None,  # Keep first for backwards compat
                'option_label': ', '.join(option_labels),
                'price': price_value
            })
            detail = {
                'model': 'multiselect_options',
                'selected_option_ids': option_ids,
                'option_labels': option_labels,
                'subtotal': subtotal_multiselect,
                'discount_threshold': discount_threshold,
                'discount_percent': discount_percent,
                'discount_amount': discount_amount,
                'price': price_value
            }

        else:
            # Legacy options fallback
            option_id = entry.get('service_option_id') or entry.get('option_id')
            custom_label = sanitize_text(entry.get('option_label'), 150)
            price_value = entry.get('price')
            if option_id:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute(
                    """
                    SELECT o.id AS option_id, o.label AS option_label, o.price AS option_price, o.is_active AS option_active,
                           o.service_id, s.name AS service_name, s.title AS service_title, s.is_active AS service_active
                    FROM service_options o
                    JOIN services s ON s.id = o.service_id
                    WHERE o.id = %s
                    """,
                    (option_id,)
                )
                option_row = cursor.fetchone()
                cursor.close()
                conn.close()
                if not option_row or not option_row.get('option_active') or not option_row.get('service_active'):
                    raise ValueError('One of the selected options is no longer available.')
                resolved_item.update({
                    'service_option_id': option_row['option_id'],
                    'option_label': option_row.get('option_label'),
                    'price': normalize_price_value(option_row.get('option_price'))
                })
                detail = {'model': 'options', 'option_id': option_row['option_id'], 'price': resolved_item['price']}
            else:
                resolved_item.update({
                    'service_option_id': None,
                    'option_label': custom_label or 'Custom package',
                    'price': normalize_price_value(price_value)
                })
                detail = {'model': 'custom', 'price': resolved_item['price']}

        if resolved_item['price'] is None:
            has_custom_price = True
        else:
            subtotal += resolved_item['price']

        resolved.append(resolved_item)
        pricing_details.append({**detail, 'service_id': service_id})

    if not resolved:
        raise ValueError('Please select at least one valid service option.')

    return resolved, subtotal, has_custom_price, pricing_details


def persist_service_request_bundle(customer, schedule, notes, selections, total_price, has_custom, legacy_request_id=None, travel=None, pricing_details=None, status_value='pending', payment_type='in_person', is_paid=False):
    conn = get_db_connection()
    cursor = conn.cursor()
    db_engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    pg_mode = db_engine == 'postgres'
    try:
        normalized_status = status_value if status_value in REQUEST_STATUSES else 'pending'
        insert_request_sql = """
            INSERT INTO service_requests (
                customer_name, email, phone, address,
                preferred_date, preferred_time, notes,
                total_price, pricing_details, status, legacy_request_id,
                travel_fee, distance_miles, travel_time_minutes, pricing_method,
                assigned_base_id, assigned_base_name,
                payment_type, is_paid
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        if pg_mode:
            insert_request_sql += " RETURNING id"

        normalized_payment_type = normalize_stored_payment_type(payment_type)

        cursor.execute(
            insert_request_sql,
            (
                customer.get('name'),
                customer.get('email') or None,
                customer.get('phone') or None,
                customer.get('address') or None,
                schedule.get('preferred_date'),
                schedule.get('preferred_time'),
                notes or None,
                total_price if not has_custom else None,
                json.dumps(pricing_details or []),
                normalized_status,
                legacy_request_id,
                (travel or {}).get('travel_fee'),
                (travel or {}).get('distance_miles'),
                (travel or {}).get('travel_time_minutes'),
                (travel or {}).get('pricing_method'),
                (travel or {}).get('base_id'),
                (travel or {}).get('base_name'),
                normalized_payment_type,
                bool(is_paid)
            )
        )
        if pg_mode:
            new_row = cursor.fetchone()
            if not new_row:
                raise RuntimeError('Unable to retrieve service_request id (postgres).')
            service_request_id = new_row[0]
        else:
            service_request_id = cursor.lastrowid

        for item in selections:
            cursor.execute(
                """
                INSERT INTO service_request_items (
                    service_request_id, service_id, service_option_id, option_label, price
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    service_request_id,
                    item.get('service_id'),
                    item.get('service_option_id'),
                    item.get('option_label'),
                    item.get('price')
                )
            )

        conn.commit()
        return service_request_id
    finally:
        cursor.close()
        conn.close()


def attach_service_request_reference(request_id, service_request_id):
    if not request_id or not service_request_id:
        return
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT metadata FROM requests WHERE id = %s", (request_id,))
    row = cursor.fetchone()
    metadata = {}
    if row and row.get('metadata'):
        try:
            metadata = json.loads(row['metadata'])
        except json.JSONDecodeError:
            metadata = {}
    cursor.close()

    service_flow = metadata.get('service_flow') or {}
    service_flow['service_request_id'] = service_request_id
    metadata['service_flow'] = service_flow

    cursor = conn.cursor()
    cursor.execute(
        "UPDATE requests SET metadata = %s WHERE id = %s",
        (json.dumps(metadata), request_id)
    )
    conn.commit()
    cursor.close()
    conn.close()


def fetch_service_request_detail(legacy_request_id):
    if not legacy_request_id:
        return None
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM service_requests WHERE legacy_request_id = %s", (legacy_request_id,))
    service_request = cursor.fetchone()
    if not service_request:
        cursor.close()
        conn.close()
        return None

    cursor.execute(
        "SELECT id, service_id, service_option_id, option_label, price FROM service_request_items WHERE service_request_id = %s ORDER BY id ASC",
        (service_request['id'],)
    )
    items = cursor.fetchall()
    cursor.close()
    conn.close()

    created_at = service_request.get('created_at')
    updated_at = service_request.get('updated_at')
    if isinstance(created_at, datetime):
        service_request['created_at'] = created_at.isoformat()
    if isinstance(updated_at, datetime):
        service_request['updated_at'] = updated_at.isoformat()
    service_request['total_price'] = normalize_price_value(service_request.get('total_price'))
    service_request['payment_type'] = normalize_stored_payment_type(service_request.get('payment_type'))
    service_request['is_paid'] = bool(service_request.get('is_paid'))
    service_request['payment_status_label'] = resolve_payment_status_label(service_request.get('payment_type'), service_request.get('is_paid'))
    service_request['payment_badge'] = resolve_payment_badge(service_request.get('payment_type'), service_request.get('is_paid'))
    for item in items:
        item['price'] = normalize_price_value(item.get('price'))
    service_request['items'] = items
    return service_request


def sync_service_request_status(request_id, new_status):
    if not request_id or not new_status or new_status not in REQUEST_STATUSES:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE service_requests SET status = %s WHERE legacy_request_id = %s",
        (new_status, request_id)
    )
    if cursor.rowcount:
        conn.commit()
    cursor.close()
    conn.close()

def fetch_job_positions_from_db():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, title, description, image_path FROM job_positions")
    jobs = cursor.fetchall()
    cursor.close()
    conn.close()
    return jobs

def fetch_testimonials_from_db(shuffle=False, include_pending=False):
    """Fetch testimonials from database.
    
    Args:
        shuffle: If True, randomize the order
        include_pending: If True, include pending testimonials (for admin). 
                        If False, only return approved testimonials (for public site).
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if include_pending:
        cursor.execute("""
            SELECT id, name, message, image_url, rating, status, is_verified_customer, email, created_at 
            FROM testimonials 
            ORDER BY created_at DESC
        """)
    else:
        cursor.execute("""
            SELECT id, name, message, image_url, rating, is_verified_customer 
            FROM testimonials 
            WHERE status = 'approved'
            ORDER BY created_at DESC
        """)
    
    testimonials = cursor.fetchall()
    cursor.close()
    conn.close()
    if shuffle and testimonials:
        random.shuffle(testimonials)
    return testimonials


def log_analytics_event(event_type, event_data=None):
    if not event_type:
        return
    conn = None
    cursor = None
    pg_conn = None
    try:
        target = (app.config.get('ANALYTICS_DB') or 'mysql').strip().lower()

        if target == 'postgres' and (app.config.get('POSTGRES_URL') or '').strip():
            pg_conn = get_pg_connection()
            ensure_pg_analytics_table(pg_conn)
            with pg_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO analytics (event_type, event_data) VALUES (%s, %s)",
                    (event_type, PGJson(event_data) if (PGJson and event_data is not None) else None)
                )
            pg_conn.commit()
        else:
            conn = get_db_connection()
            cursor = conn.cursor()
            payload = json.dumps(event_data) if event_data is not None else None
            cursor.execute(
                "INSERT INTO analytics (event_type, event_data) VALUES (%s, %s)",
                (event_type, payload)
            )
            conn.commit()
    except Exception:
        app.logger.exception('Failed to log analytics event: %s', event_type)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        if pg_conn:
            try:
                pg_conn.close()
            except Exception:
                pass


DEFAULT_HERO_CONTENT = {
    'id': 1,
    'title': 'Professional Cleaning Services',
    'subtitle': 'Reliable, affordable cleaning for your home or office. Get your free, no-obligation quote today!',
    'tagline': 'Trusted Cleaning Experts',
    'small_text_line1': 'Over 150 satisfied clients nationwide',
    'small_text_line2': 'Eco-friendly products and flexible scheduling',
    'small_text_line3': 'Fully vetted, professional cleaning teams',
    'stat1_text': '98% · Client satisfaction score',
    'stat2_text': '24h · Response time for every request',
    'stat3_text': 'Emergency · Cleanups available when you need them',
    'hero_background_image': None,
    'content_offset_x': 0,
    'content_offset_y': 0,
    'tagline_offset_x': 0,
    'tagline_offset_y': 0,
    'title_offset_x': 0,
    'title_offset_y': 0,
    'subtitle_offset_x': 0,
    'subtitle_offset_y': 0,
    'meta_offset_x': 0,
    'meta_offset_y': 0,
    'card1_offset_x': 0,
    'card1_offset_y': 0,
    'card2_offset_x': 0,
    'card2_offset_y': 0,
    'card3_offset_x': 0,
    'card3_offset_y': 0,
    'tagline_bg_color': '#16a34a',
    'tagline_text_color': '#ffffff',
    'title_color': '#2563eb',
    'title_size_px': 72,
    'title_weight': 800,
    'subtitle_color': '#ffffff',
    'subtitle_size_px': 18,
    'subtitle_weight': 600,
    'content_bg_color': '',
    'meta_text_color': '#ffffff',
    'meta_bg_color': '#0f172a'
}



DEFAULT_SITE_SETTINGS = {
    'id': 1,
    'company_name': 'Clean Co.',
    'logo_path': None
}


DEFAULT_TELEGRAM_SETTINGS = {
    'id': 1,
    'bot_token': '',
    'chat_id': '',
    'is_active': 0,
    'notify_email_success': 1,
    'notify_email_error': 1,
    'notify_admin_login': 1,
    'notify_login_failure': 1,
    'notify_error_logs': 1
}


def fetch_hero_content():
    ensure_hero_content_schema()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT
            id, title, subtitle, tagline,
            small_text_line1, small_text_line2, small_text_line3,
            stat1_text, stat2_text, stat3_text,
            hero_background_image,
            content_offset_x, content_offset_y,
            tagline_offset_x, tagline_offset_y,
            title_offset_x, title_offset_y,
            subtitle_offset_x, subtitle_offset_y,
            meta_offset_x, meta_offset_y,
            card1_offset_x, card1_offset_y,
            card2_offset_x, card2_offset_y,
            card3_offset_x, card3_offset_y,
            tagline_bg_color, tagline_text_color,
            title_color, title_size_px, title_weight,
            subtitle_color, subtitle_size_px, subtitle_weight,
            content_bg_color,
            meta_text_color, meta_bg_color
        FROM hero_content
        WHERE id = 1
        """
    )
    hero = cursor.fetchone()
    if not hero:
        cursor.close()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO hero_content (
                id, title, subtitle, tagline,
                small_text_line1, small_text_line2, small_text_line3,
                stat1_text, stat2_text, stat3_text,
                hero_background_image,
                content_offset_x, content_offset_y,
                tagline_offset_x, tagline_offset_y,
                title_offset_x, title_offset_y,
                subtitle_offset_x, subtitle_offset_y,
                meta_offset_x, meta_offset_y,
                card1_offset_x, card1_offset_y,
                card2_offset_x, card2_offset_y,
                card3_offset_x, card3_offset_y,
                tagline_bg_color, tagline_text_color,
                title_color, title_size_px, title_weight,
                subtitle_color, subtitle_size_px, subtitle_weight,
                content_bg_color,
                meta_text_color, meta_bg_color
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s,
                %s, %s
            )
            """,
            (
                DEFAULT_HERO_CONTENT['id'],
                DEFAULT_HERO_CONTENT['title'],
                DEFAULT_HERO_CONTENT['subtitle'],
                DEFAULT_HERO_CONTENT['tagline'],
                DEFAULT_HERO_CONTENT['small_text_line1'],
                DEFAULT_HERO_CONTENT['small_text_line2'],
                DEFAULT_HERO_CONTENT['small_text_line3'],
                DEFAULT_HERO_CONTENT['stat1_text'],
                DEFAULT_HERO_CONTENT['stat2_text'],
                DEFAULT_HERO_CONTENT['stat3_text'],
                DEFAULT_HERO_CONTENT['hero_background_image'],
                DEFAULT_HERO_CONTENT['content_offset_x'],
                DEFAULT_HERO_CONTENT['content_offset_y'],
                DEFAULT_HERO_CONTENT['tagline_offset_x'],
                DEFAULT_HERO_CONTENT['tagline_offset_y'],
                DEFAULT_HERO_CONTENT['title_offset_x'],
                DEFAULT_HERO_CONTENT['title_offset_y'],
                DEFAULT_HERO_CONTENT['subtitle_offset_x'],
                DEFAULT_HERO_CONTENT['subtitle_offset_y'],
                DEFAULT_HERO_CONTENT['meta_offset_x'],
                DEFAULT_HERO_CONTENT['meta_offset_y'],
                DEFAULT_HERO_CONTENT['card1_offset_x'],
                DEFAULT_HERO_CONTENT['card1_offset_y'],
                DEFAULT_HERO_CONTENT['card2_offset_x'],
                DEFAULT_HERO_CONTENT['card2_offset_y'],
                DEFAULT_HERO_CONTENT['card3_offset_x'],
                DEFAULT_HERO_CONTENT['card3_offset_y'],
                DEFAULT_HERO_CONTENT['tagline_bg_color'],
                DEFAULT_HERO_CONTENT['tagline_text_color'],
                DEFAULT_HERO_CONTENT['title_color'],
                DEFAULT_HERO_CONTENT['title_size_px'],
                DEFAULT_HERO_CONTENT['title_weight'],
                DEFAULT_HERO_CONTENT['subtitle_color'],
                DEFAULT_HERO_CONTENT['subtitle_size_px'],
                DEFAULT_HERO_CONTENT['subtitle_weight'],
                DEFAULT_HERO_CONTENT['content_bg_color'],
                DEFAULT_HERO_CONTENT['meta_text_color'],
                DEFAULT_HERO_CONTENT['meta_bg_color']
            )
        )
        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                id, title, subtitle, tagline,
                small_text_line1, small_text_line2, small_text_line3,
                stat1_text, stat2_text, stat3_text,
                hero_background_image,
                content_offset_x, content_offset_y,
                tagline_offset_x, tagline_offset_y,
                title_offset_x, title_offset_y,
                subtitle_offset_x, subtitle_offset_y,
                meta_offset_x, meta_offset_y,
                card1_offset_x, card1_offset_y,
                card2_offset_x, card2_offset_y,
                card3_offset_x, card3_offset_y,
                tagline_bg_color, tagline_text_color,
                title_color, title_size_px, title_weight,
                subtitle_color, subtitle_size_px, subtitle_weight,
                content_bg_color,
                meta_text_color, meta_bg_color
            FROM hero_content
            WHERE id = 1
            """
        )
        hero = cursor.fetchone()
    cursor.close()
    conn.close()

    merged = DEFAULT_HERO_CONTENT.copy()
    if hero:
        merged.update({key: value for key, value in hero.items() if value is not None})
    return merged


_done_ensure_hero_content_schema = False


def ensure_hero_content_schema():
    global _done_ensure_hero_content_schema
    if _done_ensure_hero_content_schema:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hero_content (
                id BIGINT PRIMARY KEY,
                title VARCHAR(255),
                subtitle TEXT,
                tagline VARCHAR(255),
                small_text_line1 VARCHAR(255),
                small_text_line2 VARCHAR(255),
                small_text_line3 VARCHAR(255),
                stat1_text VARCHAR(255),
                stat2_text VARCHAR(255),
                stat3_text VARCHAR(255),
                hero_background_image TEXT,
                content_offset_x INTEGER DEFAULT 0,
                content_offset_y INTEGER DEFAULT 0,
                tagline_offset_x INTEGER DEFAULT 0,
                tagline_offset_y INTEGER DEFAULT 0,
                title_offset_x INTEGER DEFAULT 0,
                title_offset_y INTEGER DEFAULT 0,
                subtitle_offset_x INTEGER DEFAULT 0,
                subtitle_offset_y INTEGER DEFAULT 0,
                meta_offset_x INTEGER DEFAULT 0,
                meta_offset_y INTEGER DEFAULT 0,
                card1_offset_x INTEGER DEFAULT 0,
                card1_offset_y INTEGER DEFAULT 0,
                card2_offset_x INTEGER DEFAULT 0,
                card2_offset_y INTEGER DEFAULT 0,
                card3_offset_x INTEGER DEFAULT 0,
                card3_offset_y INTEGER DEFAULT 0,
                tagline_bg_color VARCHAR(20) DEFAULT '#16a34a',
                tagline_text_color VARCHAR(20) DEFAULT '#ffffff',
                title_color VARCHAR(20) DEFAULT '#2563eb',
                title_size_px INTEGER DEFAULT 72,
                title_weight INTEGER DEFAULT 800,
                subtitle_color VARCHAR(20) DEFAULT '#ffffff',
                subtitle_size_px INTEGER DEFAULT 18,
                subtitle_weight INTEGER DEFAULT 600,
                content_bg_color VARCHAR(20),
                meta_text_color VARCHAR(20) DEFAULT '#ffffff',
                meta_bg_color VARCHAR(20) DEFAULT '#0f172a'
            )
            """
        )

        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS hero_background_image TEXT")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS content_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS content_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS tagline_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS tagline_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS title_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS title_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS subtitle_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS subtitle_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS meta_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS meta_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS card1_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS card1_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS card2_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS card2_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS card3_offset_x INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS card3_offset_y INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS tagline_bg_color VARCHAR(20) DEFAULT '#16a34a'")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS tagline_text_color VARCHAR(20) DEFAULT '#ffffff'")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS title_color VARCHAR(20) DEFAULT '#2563eb'")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS title_size_px INTEGER DEFAULT 72")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS title_weight INTEGER DEFAULT 800")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS subtitle_color VARCHAR(20) DEFAULT '#ffffff'")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS subtitle_size_px INTEGER DEFAULT 18")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS subtitle_weight INTEGER DEFAULT 600")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS content_bg_color VARCHAR(20)")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS meta_text_color VARCHAR(20) DEFAULT '#ffffff'")
        cursor.execute("ALTER TABLE hero_content ADD COLUMN IF NOT EXISTS meta_bg_color VARCHAR(20) DEFAULT '#0f172a'")
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS hero_content (
                id INT PRIMARY KEY,
                title VARCHAR(255),
                subtitle TEXT,
                tagline VARCHAR(255),
                small_text_line1 VARCHAR(255),
                small_text_line2 VARCHAR(255),
                small_text_line3 VARCHAR(255),
                stat1_text VARCHAR(255),
                stat2_text VARCHAR(255),
                stat3_text VARCHAR(255),
                hero_background_image TEXT,
                content_offset_x INT DEFAULT 0,
                content_offset_y INT DEFAULT 0,
                tagline_offset_x INT DEFAULT 0,
                tagline_offset_y INT DEFAULT 0,
                title_offset_x INT DEFAULT 0,
                title_offset_y INT DEFAULT 0,
                subtitle_offset_x INT DEFAULT 0,
                subtitle_offset_y INT DEFAULT 0,
                meta_offset_x INT DEFAULT 0,
                meta_offset_y INT DEFAULT 0,
                card1_offset_x INT DEFAULT 0,
                card1_offset_y INT DEFAULT 0,
                card2_offset_x INT DEFAULT 0,
                card2_offset_y INT DEFAULT 0,
                card3_offset_x INT DEFAULT 0,
                card3_offset_y INT DEFAULT 0,
                tagline_bg_color VARCHAR(20) DEFAULT '#16a34a',
                tagline_text_color VARCHAR(20) DEFAULT '#ffffff',
                title_color VARCHAR(20) DEFAULT '#2563eb',
                title_size_px INT DEFAULT 72,
                title_weight INT DEFAULT 800,
                subtitle_color VARCHAR(20) DEFAULT '#ffffff',
                subtitle_size_px INT DEFAULT 18,
                subtitle_weight INT DEFAULT 600,
                content_bg_color VARCHAR(20) NULL,
                meta_text_color VARCHAR(20) DEFAULT '#ffffff',
                meta_bg_color VARCHAR(20) DEFAULT '#0f172a'
            )
            """
        )

        mysql_columns = {
            'hero_background_image': "TEXT NULL",
            'content_offset_x': "INT DEFAULT 0",
            'content_offset_y': "INT DEFAULT 0",
            'tagline_offset_x': "INT DEFAULT 0",
            'tagline_offset_y': "INT DEFAULT 0",
            'title_offset_x': "INT DEFAULT 0",
            'title_offset_y': "INT DEFAULT 0",
            'subtitle_offset_x': "INT DEFAULT 0",
            'subtitle_offset_y': "INT DEFAULT 0",
            'meta_offset_x': "INT DEFAULT 0",
            'meta_offset_y': "INT DEFAULT 0",
            'card1_offset_x': "INT DEFAULT 0",
            'card1_offset_y': "INT DEFAULT 0",
            'card2_offset_x': "INT DEFAULT 0",
            'card2_offset_y': "INT DEFAULT 0",
            'card3_offset_x': "INT DEFAULT 0",
            'card3_offset_y': "INT DEFAULT 0",
            'tagline_bg_color': "VARCHAR(20) DEFAULT '#16a34a'",
            'tagline_text_color': "VARCHAR(20) DEFAULT '#ffffff'",
            'title_color': "VARCHAR(20) DEFAULT '#2563eb'",
            'title_size_px': "INT DEFAULT 72",
            'title_weight': "INT DEFAULT 800",
            'subtitle_color': "VARCHAR(20) DEFAULT '#ffffff'",
            'subtitle_size_px': "INT DEFAULT 18",
            'subtitle_weight': "INT DEFAULT 600",
            'content_bg_color': "VARCHAR(20) NULL",
            'meta_text_color': "VARCHAR(20) DEFAULT '#ffffff'",
            'meta_bg_color': "VARCHAR(20) DEFAULT '#0f172a'"
        }

        for column_name, column_sql in mysql_columns.items():
            cursor.execute("SHOW COLUMNS FROM hero_content LIKE %s", (column_name,))
            if not cursor.fetchone():
                cursor.execute(f"ALTER TABLE hero_content ADD COLUMN {column_name} {column_sql}")

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_hero_content_schema = True


def fetch_hero_badges(include_inactive=False):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, image_path, created_at FROM hero_badges ORDER BY created_at ASC")
    badges = cursor.fetchall()
    cursor.close()
    conn.close()
    return badges


def fetch_contact_info():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, headline, phone, email, operating_hours, service_areas, quote_background FROM contact_info WHERE id = 1"
    )
    info = cursor.fetchone()
    if not info:
        cursor.close()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO contact_info (id, headline, phone, email, operating_hours, service_areas, quote_background) VALUES (1, %s, %s, %s, %s, %s, NULL)",
            (
                'Talk to Us',
                '+233 24 000 0000',
                'hello@cleanco.com',
                'Monday – Saturday, 7:00am to 7:00pm. Emergency cleanups available on request.',
                'We serve nationwide across major cities and suburbs.'
            )
        )
        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, headline, phone, email, operating_hours, service_areas, quote_background FROM contact_info WHERE id = 1"
        )
        info = cursor.fetchone()
    cursor.close()
    conn.close()
    return info


def fetch_footer_info():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, phone, email, location, facebook, instagram, twitter FROM footer_info WHERE id = 1"
    )
    footer = cursor.fetchone()
    if not footer:
        cursor.close()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO footer_info (id, phone, email, location, facebook, instagram, twitter) VALUES (1, %s, %s, %s, '', '', '')",
            ('+233 24 000 0000', 'hello@cleanco.com', 'Accra, Ghana')
        )
        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, phone, email, location, facebook, instagram, twitter FROM footer_info WHERE id = 1"
        )
        footer = cursor.fetchone()
    cursor.close()
    conn.close()
    return footer


_done_ensure_site_content_table = False


def ensure_site_content_table():
    """Create site_content table if it doesn't exist."""
    global _done_ensure_site_content_table
    if _done_ensure_site_content_table:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if 'postgres' in engine:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS site_content (
                section_key VARCHAR(100) PRIMARY KEY,
                content_text TEXT,
                content_json TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS site_content (
                section_key VARCHAR(100) PRIMARY KEY,
                content_text TEXT,
                content_json TEXT,
                is_active TINYINT(1) DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_site_content_table = True


def fetch_site_content():
    """Fetch all site content sections as a dictionary keyed by section_key."""
    ensure_site_content_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    condition, params = build_active_true_condition('is_active', engine)
    cursor.execute(
        f"SELECT section_key, content_text, content_json FROM site_content WHERE {condition}",
        params
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    content = {}
    for row in rows:
        key = row.get('section_key')
        if not key:
            continue
        text_value = row.get('content_text') or ''
        json_value = row.get('content_json')
        if json_value:
            try:
                if isinstance(json_value, str):
                    content[key] = json.loads(json_value)
                else:
                    content[key] = json_value
            except (json.JSONDecodeError, TypeError):
                content[key] = text_value
        else:
            content[key] = text_value
    return content


def fetch_site_settings():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, company_name, logo_path FROM site_settings WHERE id = 1"
    )
    settings = cursor.fetchone()
    if not settings:
        cursor.close()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO site_settings (id, company_name, logo_path) VALUES (%s, %s, %s)",
            (
                DEFAULT_SITE_SETTINGS['id'],
                DEFAULT_SITE_SETTINGS['company_name'],
                DEFAULT_SITE_SETTINGS['logo_path']
            )
        )
        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, company_name, logo_path FROM site_settings WHERE id = 1"
        )
        settings = cursor.fetchone()
    cursor.close()
    conn.close()
    if not settings:
        return DEFAULT_SITE_SETTINGS.copy()
    normalized = {
        'id': settings.get('id') or DEFAULT_SITE_SETTINGS['id'],
        'company_name': sanitize_text(settings.get('company_name'), 255) or DEFAULT_SITE_SETTINGS['company_name'],
        'logo_path': settings.get('logo_path') or None
    }
    return normalized


def _bool_from_db(value):
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return False


_done_ensure_telegram_settings_schema = False


def ensure_telegram_settings_schema():
    global _done_ensure_telegram_settings_schema
    if _done_ensure_telegram_settings_schema:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if 'postgres' in engine:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_settings (
                id BIGINT PRIMARY KEY,
                bot_token TEXT,
                chat_id TEXT,
                is_active SMALLINT DEFAULT 0,
                notify_email_success SMALLINT DEFAULT 1,
                notify_email_error SMALLINT DEFAULT 1,
                notify_admin_login SMALLINT DEFAULT 1,
                notify_login_failure SMALLINT DEFAULT 1,
                notify_error_logs SMALLINT DEFAULT 1,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
        cursor.execute("ALTER TABLE telegram_settings ADD COLUMN IF NOT EXISTS notify_error_logs SMALLINT DEFAULT 1")
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_settings (
                id BIGINT PRIMARY KEY,
                bot_token TEXT,
                chat_id VARCHAR(255),
                is_active TINYINT(1) DEFAULT 0,
                notify_email_success TINYINT(1) DEFAULT 1,
                notify_email_error TINYINT(1) DEFAULT 1,
                notify_admin_login TINYINT(1) DEFAULT 1,
                notify_login_failure TINYINT(1) DEFAULT 1,
                notify_error_logs TINYINT(1) DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute("SHOW COLUMNS FROM telegram_settings LIKE %s", ('notify_error_logs',))
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE telegram_settings ADD COLUMN notify_error_logs TINYINT(1) DEFAULT 1")

    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_telegram_settings_schema = True


def fetch_telegram_settings():
    ensure_telegram_settings_schema()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT id, bot_token, chat_id, is_active, notify_email_success, notify_email_error, notify_admin_login, notify_login_failure, notify_error_logs FROM telegram_settings WHERE id = 1"
    )
    settings = cursor.fetchone()
    if not settings:
        cursor.close()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO telegram_settings (id, is_active, notify_email_success, notify_email_error, notify_admin_login, notify_login_failure, notify_error_logs) VALUES (1, 0, 1, 1, 1, 1, 1)"
        )
        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, bot_token, chat_id, is_active, notify_email_success, notify_email_error, notify_admin_login, notify_login_failure, notify_error_logs FROM telegram_settings WHERE id = 1"
        )
        settings = cursor.fetchone()
    cursor.close()
    conn.close()

    if not settings:
        return DEFAULT_TELEGRAM_SETTINGS.copy()

    normalized = DEFAULT_TELEGRAM_SETTINGS.copy()
    normalized.update({
        'id': settings.get('id') or DEFAULT_TELEGRAM_SETTINGS['id'],
        'bot_token': (settings.get('bot_token') or '').strip(),
        'chat_id': (settings.get('chat_id') or '').strip(),
        'is_active': _bool_from_db(settings.get('is_active')),
        'notify_email_success': _bool_from_db(settings.get('notify_email_success')),
        'notify_email_error': _bool_from_db(settings.get('notify_email_error')),
        'notify_admin_login': _bool_from_db(settings.get('notify_admin_login')),
        'notify_login_failure': _bool_from_db(settings.get('notify_login_failure')),
        'notify_error_logs': _bool_from_db(settings.get('notify_error_logs'))
    })
    return normalized


def update_telegram_settings(payload):
    ensure_telegram_settings_schema()
    if not payload:
        raise ValueError('No data provided.')

    bot_token = sanitize_text(payload.get('bot_token'), 255)
    chat_id = sanitize_text(payload.get('chat_id'), 128)
    is_active = 1 if str_to_bool(payload.get('is_active')) else 0
    notify_email_success = 1 if str_to_bool(payload.get('notify_email_success')) else 0
    notify_email_error = 1 if str_to_bool(payload.get('notify_email_error')) else 0
    notify_admin_login = 1 if str_to_bool(payload.get('notify_admin_login')) else 0
    notify_login_failure = 1 if str_to_bool(payload.get('notify_login_failure')) else 0
    notify_error_logs = 1 if str_to_bool(payload.get('notify_error_logs')) else 0

    if is_active and (not bot_token or not chat_id):
        raise ValueError('Bot token and chat ID are required when Telegram notifications are active.')

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE telegram_settings
            SET bot_token=%s,
                chat_id=%s,
                is_active=%s,
                notify_email_success=%s,
                notify_email_error=%s,
                notify_admin_login=%s,
                notify_login_failure=%s,
                notify_error_logs=%s
            WHERE id = 1
            """,
            (
                bot_token or None,
                chat_id or None,
                is_active,
                notify_email_success,
                notify_email_error,
                notify_admin_login,
                notify_login_failure,
                notify_error_logs
            )
        )
        conn.commit()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return fetch_telegram_settings()


def send_telegram_notification(preference_key, message_lines):
    try:
        settings = fetch_telegram_settings()
    except Exception:
        app.logger.exception('Failed to load Telegram settings for notification dispatch.')
        return False

    if not settings.get('is_active'):
        return False
    if preference_key and not settings.get(preference_key, True):
        return False

    bot_token = (settings.get('bot_token') or '').strip()
    chat_id = (settings.get('chat_id') or '').strip()
    if not bot_token or not chat_id:
        return False

    if isinstance(message_lines, str):
        lines = [message_lines]
    else:
        lines = list(message_lines or [])

    cleaned_lines = [normalize_message(line) for line in lines if normalize_message(line)]
    if not cleaned_lines:
        return False

    message = '\n'.join(cleaned_lines).strip()
    if not message:
        return False

    max_length = 3500
    if len(message) > max_length:
        message = f"{message[:max_length - 3]}..."

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'disable_notification': False,
        'disable_web_page_preview': True
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        if not response.ok:
            app.logger.warning('Telegram notification failed: %s', response.text)
            return False
    except RequestException:
        app.logger.exception('Unable to send Telegram notification.')
        return False

    return True


def _fetch_telegram_error_alert_target_quietly():
    try:
        ensure_telegram_settings_schema()
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT bot_token, chat_id, is_active, notify_error_logs FROM telegram_settings WHERE id = 1"
        )
        row = cursor.fetchone() or {}
        cursor.close()
        conn.close()

        is_active = _bool_from_db(row.get('is_active'))
        notify_enabled = _bool_from_db(row.get('notify_error_logs'))
        bot_token = (row.get('bot_token') or '').strip()
        chat_id = (row.get('chat_id') or '').strip()
        if not (is_active and notify_enabled and bot_token and chat_id):
            return None
        return {
            'bot_token': bot_token,
            'chat_id': chat_id
        }
    except Exception:
        return None


class TelegramErrorLogHandler(logging.Handler):
    def __init__(self, throttle_seconds=120, health_report_hours=6):
        super().__init__(level=logging.ERROR)
        self.throttle_seconds = max(30, int(throttle_seconds or 120))
        self.health_report_hours = max(1, int(health_report_hours or 6))
        self._last_sent_at = 0.0
        self._last_fingerprint = ''
        self._lock = threading.Lock()
        self._local = threading.local()
        self._last_error_at = 0.0          # tracks when last real error occurred
        self._health_reported_at = 0.0     # tracks when last "all clear" was sent
        self._health_timer = None

    def _schedule_health_report(self):
        """Schedule a health report if no further errors arrive within the window."""
        if self._health_timer is not None:
            self._health_timer.cancel()
        interval = self.health_report_hours * 3600
        self._health_timer = threading.Timer(interval, self._send_health_report)
        self._health_timer.daemon = True
        self._health_timer.start()

    def _send_health_report(self):
        try:
            target = _fetch_telegram_error_alert_target_quietly()
            if not target:
                return
            now = time.time()
            hours = self.health_report_hours
            text = (
                f"✅ System Health Report\n"
                f"No errors logged in the past {hours} hour{'s' if hours != 1 else ''}.\n"
                f"All systems appear healthy."
            )
            url = f"https://api.telegram.org/bot{target['bot_token']}/sendMessage"
            requests.post(url, json={
                'chat_id': target['chat_id'],
                'text': text,
                'disable_web_page_preview': True,
                'disable_notification': True
            }, timeout=5)
            with self._lock:
                self._health_reported_at = now
        except Exception:
            pass

    def emit(self, record):
        if record.levelno < logging.ERROR:
            return
        if getattr(self._local, 'in_emit', False):
            return

        self._local.in_emit = True
        try:
            target = _fetch_telegram_error_alert_target_quietly()
            if not target:
                return

            message = (record.getMessage() or '').strip()
            if not message:
                return

            # Only suppress invalid signature noise — webhook errors are real now
            suppressed_fragments = (
                'Invalid Stripe webhook signature',
            )
            if any(fragment in message for fragment in suppressed_fragments):
                return

            fingerprint_source = f"{record.name}|{record.levelno}|{message}"
            fingerprint = hashlib.sha256(fingerprint_source.encode('utf-8', errors='ignore')).hexdigest()
            now = time.time()
            with self._lock:
                if fingerprint == self._last_fingerprint and (now - self._last_sent_at) < self.throttle_seconds:
                    return
                self._last_fingerprint = fingerprint
                self._last_sent_at = now
                self._last_error_at = now

            lines = [
                '🚨 Application Error Detected',
                f"Level: {record.levelname}",
                f"Logger: {record.name}",
                f"Location: {os.path.basename(record.pathname)}:{record.lineno}",
                f"Message: {message[:1000]}"
            ]
            if record.exc_info:
                lines.append('Traceback attached in server logs.')

            text = '\n'.join(lines)
            if len(text) > 3500:
                text = text[:3497] + '...'

            url = f"https://api.telegram.org/bot{target['bot_token']}/sendMessage"
            payload = {
                'chat_id': target['chat_id'],
                'text': text,
                'disable_web_page_preview': True,
                'disable_notification': False
            }
            requests.post(url, json=payload, timeout=5)

            # Reschedule health report — it fires only if no new errors arrive
            self._schedule_health_report()
        except Exception:
            return
        finally:
            self._local.in_emit = False


_telegram_error_log_handler_attached = False


def configure_telegram_error_log_handler():
    global _telegram_error_log_handler_attached
    if _telegram_error_log_handler_attached:
        return
    try:
        handler = TelegramErrorLogHandler(throttle_seconds=120, health_report_hours=1)
        # Attach to both app logger and root logger to catch errors from all modules
        app.logger.addHandler(handler)
        logging.getLogger().addHandler(handler)
        _telegram_error_log_handler_attached = True
    except Exception:
        pass


def fetch_admin_user(username):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, username, password_hash FROM admin_users WHERE username = %s LIMIT 1",
            (username,)
        )
        return cursor.fetchone()
    except Exception:
        app.logger.exception('Failed to fetch admin user %s', username)
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def normalize_message(text):
    return re.sub(r'\s+', ' ', str(text)).strip()

# --- API Routes (Frontend) ---

@app.route('/api/requests', methods=['POST'])
def create_request_entry():
    is_json = request.content_type and 'application/json' in request.content_type
    payload = request.get_json(silent=True) if is_json else request.form.to_dict()
    payload = payload or {}
    if 'request_type' not in payload:
        inferred_type = payload.get('type') or payload.get('form_type')
        if inferred_type:
            payload['request_type'] = inferred_type

    uploaded_files = []
    if not is_json and request.files:
        for key in request.files:
            uploaded_files.extend([file_item for file_item in request.files.getlist(key) if file_item and file_item.filename])

    try:
        response_payload = process_request_submission(payload, uploaded_files, request.remote_addr)
        return jsonify(response_payload), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to process incoming request.')
        return jsonify({'error': 'We were unable to process your request right now.'}), 500


@app.route('/api/book-service', methods=['POST'])
def book_service():
    data = request.get_json(silent=True) or {}
    payload = {
        'request_type': 'service',
        'name': data.get('name'),
        'phone': data.get('phone'),
        'service': data.get('service'),
        'message': data.get('message'),
        'email': data.get('email'),
        'source': 'legacy-api'
    }
    try:
        response_payload = process_request_submission(payload, None, request.remote_addr)
        return jsonify(response_payload), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Legacy book_service endpoint failed.')
        return jsonify({'error': 'Unable to process booking at this time.'}), 500


@app.route('/api/apply-job', methods=['POST'])
def apply_job():
    if request.content_type and 'multipart/form-data' in request.content_type:
        payload = request.form.to_dict()
        payload['request_type'] = 'job'
        attachments = []
        for key in request.files:
            attachments.extend([file_item for file_item in request.files.getlist(key) if file_item and file_item.filename])
    else:
        data = request.get_json(silent=True) or {}
        payload = {
            'request_type': 'job',
            'name': data.get('name'),
            'email': data.get('email'),
            'phone': data.get('phone'),
            'position': data.get('position'),
            'message': data.get('message'),
            'source': 'legacy-api'
        }
        attachments = None

    try:
        response_payload = process_request_submission(payload, attachments, request.remote_addr)
        return jsonify(response_payload), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to submit job application')
        return jsonify({'error': 'Unable to submit application at this time.'}), 500


@app.route('/api/services', methods=['GET'])
def get_services():
    try:
        include_inactive = str_to_bool(request.args.get('include_inactive', '0'))
        services = fetch_services_from_db(include_inactive=include_inactive)
        return jsonify(services)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/travel-quote', methods=['POST'])
def travel_quote():
    data = request.get_json(silent=True) or {}
    postcode = sanitize_text(data.get('postcode'), 255)
    base_amount = normalize_price_value(data.get('base_amount'))
    try:
        quote = calculate_travel_cost(postcode)
        total = None
        if base_amount is not None and quote.get('travel_fee') is not None:
            total = round(base_amount + quote.get('travel_fee'), 2)
        quote['total'] = total
        session_quote = dict(quote)
        session_quote.pop('total', None)
        _store_session_travel_quote(postcode, session_quote)
        return jsonify(quote)
    except ValueError as exc:
        message = str(exc)
        payload = {'error': message}
        if 'out of area' in message.lower():
            payload['code'] = 'out_of_area'
        return jsonify(payload), 400
    except Exception:
        app.logger.exception('Failed to calculate travel quote')
        return jsonify({'error': 'Unable to calculate travel cost right now.'}), 500


def prepare_service_booking(payload):
    payload = payload or {}
    selections_raw = payload.get('selections') or payload.get('cart')
    selections, subtotal, has_custom, pricing_details = resolve_service_selections(selections_raw)
    has_survey_request = any(item.get('is_survey_request') for item in selections)
    selected_categories = sorted({normalize_service_category(item.get('service_category')) for item in selections})
    # Hybrid services only become contract-based if the customer chose a frequency
    _contract_frequency_raw = normalize_contract_frequency(
        (payload.get('schedule') or {}).get('contract_frequency') or payload.get('contract_frequency')
    )
    has_hybrid_service = 'hybrid' in selected_categories
    has_contract_service = 'contract' in selected_categories or (has_hybrid_service and bool(_contract_frequency_raw))

    user_postcode = sanitize_text(payload.get('postcode'), 255)
    if user_postcode:
        travel_quote = _get_session_travel_quote(user_postcode)
        if not travel_quote:
            travel_quote = calculate_travel_cost(user_postcode)
    else:
        travel_quote = {
            'travel_fee': 0,
            'distance_miles': None,
            'travel_time_minutes': None,
            'pricing_method': 'disabled'
        }

    customer_data = payload.get('customer') or {}
    schedule_data = payload.get('schedule') or {}

    customer_name = sanitize_text(customer_data.get('name'), 150)
    if not customer_name:
        raise ValueError('Please provide your full name.')
    customer_email = sanitize_email(customer_data.get('email'))
    customer_phone = sanitize_phone(customer_data.get('phone'))
    if not customer_phone:
        raise ValueError('Please enter a phone number so we can reach you.')
    customer_address = sanitize_text(customer_data.get('address') or payload.get('address'), 255)

    preferred_date_raw = schedule_data.get('preferred_date') or payload.get('preferred_date')
    preferred_time = sanitize_text(schedule_data.get('preferred_time') or payload.get('preferred_time'), 32)
    contract_frequency = normalize_contract_frequency(schedule_data.get('contract_frequency') or payload.get('contract_frequency'))
    parsed_date = parse_preferred_date(preferred_date_raw)
    if preferred_date_raw and not parsed_date:
        raise ValueError('Please choose a valid preferred date.')
    if has_contract_service and not contract_frequency and not has_hybrid_service:
        raise ValueError('Please choose a contract frequency for contract-based services.')

    notes = sanitize_text(payload.get('notes') or schedule_data.get('notes'), 1000)
    contract_data = payload.get('contract_agreement') or {}
    contract_signer_name = sanitize_text(contract_data.get('signer_name') or customer_name, 150)
    contract_terms_agreed = bool(str_to_bool(contract_data.get('agreed')))
    contract_service_day = sanitize_text(contract_data.get('service_day'), 20)
    contract_service_day = contract_service_day.title() if contract_service_day else ''
    if contract_service_day not in set(calendar.day_name):
        contract_service_day = ''
    if has_contract_service and not contract_service_day:
        contract_service_day = calendar.day_name[datetime.now(timezone.utc).weekday()]
    if has_contract_service and not contract_terms_agreed:
        raise ValueError('Contract terms must be accepted for contract-based services.')

    travel_fee_value = travel_quote.get('travel_fee') if travel_quote else None
    services_subtotal_display = 'To be confirmed (survey required)' if has_survey_request else (format_currency_label(subtotal) if not has_custom else 'Custom quote')
    total_with_travel = None
    if not has_custom and not has_survey_request and subtotal is not None:
        total_with_travel = subtotal
        if travel_fee_value is not None:
            total_with_travel = round(subtotal + travel_fee_value, 2)

    payment_option = normalize_payment_option(payload.get('payment_option') or payload.get('payment_type'))
    if has_custom or has_survey_request:
        app.logger.info(f"prepare_service_booking - Forcing payment_option to 'in_person' due to has_custom={has_custom} or has_survey_request={has_survey_request}")
        payment_option = PAYMENT_OPTION_IN_PERSON
    
    app.logger.info(f"prepare_service_booking - Incoming payment_option value: {payload.get('payment_option')} or {payload.get('payment_type')}")
    app.logger.info(f"prepare_service_booking - Normalized payment_option: {payment_option}")
    app.logger.info(f"prepare_service_booking - has_custom: {has_custom}, has_survey_request: {has_survey_request}")
    
    payment_settings = fetch_payment_settings()
    configured_discount_enabled = bool(payment_settings.get('prebook_discount_enabled', True))
    try:
        configured_discount_percent = float(payment_settings.get('prebook_discount_percent', PREBOOK_DISCOUNT_PERCENT))
    except (TypeError, ValueError):
        configured_discount_percent = float(PREBOOK_DISCOUNT_PERCENT)
    configured_discount_percent = max(0.0, min(100.0, configured_discount_percent))

    is_prebook_payment = payment_option == PAYMENT_OPTION_PREBOOK

    discounted_total = None
    prebook_discount_amount = None
    effective_prebook_discount_percent = configured_discount_percent if configured_discount_enabled else 0.0
    if is_prebook_payment and total_with_travel is not None:
        discounted_total, prebook_discount_amount = calculate_prebook_discount_with_percent(total_with_travel, effective_prebook_discount_percent)

    payable_total = discounted_total if (is_prebook_payment and discounted_total is not None) else total_with_travel
    payment_type_for_db = 'stripe' if is_prebook_payment else 'in_person'

    summary_lines = ['Selections:']
    for item in selections:
        summary_lines.append(
            f"- {item['service_name']} · {item['option_label']} ({format_currency_label(item.get('price'))})"
        )
    summary_lines.append('')
    summary_lines.append(f"Services subtotal: {services_subtotal_display}")
    if travel_fee_value is not None and not has_survey_request:
        summary_lines.append(f"Logistics & Area Fee: {format_currency_label(travel_fee_value)}")
    if has_survey_request:
        summary_lines.append('Pricing: Survey required before confirming total. No payment taken.')
    elif total_with_travel is not None:
        summary_lines.append(f"Estimated total: {format_currency_label(total_with_travel)}")
        if is_prebook_payment and discounted_total is not None and prebook_discount_amount is not None:
            if effective_prebook_discount_percent > 0 and prebook_discount_amount > 0:
                summary_lines.append(f"Pre-book & Save ({effective_prebook_discount_percent}% off): -{format_currency_label(prebook_discount_amount)}")
            else:
                summary_lines.append("Pre-book payment selected (discount currently disabled)")
            summary_lines.append(f"Pay now total: {format_currency_label(discounted_total)}")
        else:
            summary_lines.append('Payment choice: Pay in Person (no online payment required)')
    summary_lines.append('')
    summary_lines.append(f"Preferred date: {preferred_date_raw or 'Flexible'}")
    summary_lines.append(f"Preferred time: {preferred_time or 'Flexible'}")
    if has_contract_service and contract_frequency:
        summary_lines.append(f"Contract frequency: {format_contract_frequency_label(contract_frequency)}")
    if has_contract_service:
        summary_lines.append(f"Contract signer: {contract_signer_name or customer_name}")
        summary_lines.append(f"Contract service day: {contract_service_day}")
        summary_lines.append(f"Contract terms accepted: {'Yes' if contract_terms_agreed else 'No'}")
    if customer_address:
        summary_lines.append(f"Address: {customer_address}")
    if notes:
        summary_lines.append('')
        summary_lines.append(f"Notes: {notes}")
    if travel_quote:
        summary_lines.append('')
        if travel_quote.get('distance_miles') is not None:
            summary_lines.append(f"Distance: {travel_quote.get('distance_miles')} miles")
        if travel_quote.get('base_name'):
            summary_lines.append(f"Assigned base: {travel_quote.get('base_name')}")

    service_metadata = {
        'customer': {
            'name': customer_name,
            'email': customer_email,
            'phone': customer_phone,
            'address': customer_address
        },
        'schedule': {
            'preferred_date': preferred_date_raw,
            'preferred_time': preferred_time,
            'contract_frequency': contract_frequency
        },
        'notes': notes,
        'selections': selections,
        'service_categories': selected_categories,
        'has_contract_service': has_contract_service,
        'contract': {
            'signer_name': contract_signer_name,
            'service_day': contract_service_day,
            'terms_agreed': contract_terms_agreed
        },
        'pricing_details': pricing_details,
        'totals': {
            'amount': subtotal if (not has_custom and not has_survey_request) else None,
            'has_custom_pricing': has_custom,
            'is_survey_request': has_survey_request,
            'display': services_subtotal_display,
            'travel_fee': travel_fee_value,
            'total_with_travel': total_with_travel,
            'prebook_discount_percent': effective_prebook_discount_percent if is_prebook_payment else 0,
            'prebook_discount_amount': prebook_discount_amount,
            'payable_total': payable_total
        },
        'payment': {
            'option': payment_option,
            'payment_type': payment_type_for_db,
            'is_paid': False,
            'status_label': resolve_payment_status_label(payment_type_for_db, False)
        },
        'travel': travel_quote
    }

    primary_service_name = selections[0]['service_name'] if selections else 'Cleaning package'
    public_payload = {
        'request_type': 'service',
        'name': customer_name,
        'email': customer_email,
        'phone': customer_phone,
        'service_name': primary_service_name,
        'message': '\n'.join(summary_lines),
        'context_page': payload.get('context_page') or '/#services',
        'source': payload.get('source') or 'service-flow'
    }

    customer_bundle = {
        'name': customer_name,
        'email': customer_email,
        'phone': customer_phone,
        'address': customer_address
    }
    schedule_bundle = {
        'preferred_date': parsed_date,
        'preferred_time': preferred_time
    }
    stored_total_for_db = payable_total if not has_custom and not has_survey_request else None

    return {
        'incoming_payload': payload,
        'selections': selections,
        'subtotal': subtotal,
        'has_custom': has_custom,
        'pricing_details': pricing_details,
        'has_survey_request': has_survey_request,
        'travel_quote': travel_quote,
        'total_with_travel': total_with_travel,
        'payable_total': payable_total,
        'prebook_discount_amount': prebook_discount_amount,
        'payment_option': payment_option,
        'payment_type_for_db': payment_type_for_db,
        'public_payload': public_payload,
        'service_metadata': service_metadata,
        'customer_bundle': customer_bundle,
        'schedule_bundle': schedule_bundle,
        'stored_total_for_db': stored_total_for_db,
        'notes': notes,
        'primary_service_name': primary_service_name,
        'customer_name': customer_name,
        'customer_email': customer_email,
        'has_contract_service': has_contract_service,
        'selected_service_categories': selected_categories
    }


def finalize_prepared_service_booking(prepared, remote_addr=None, mark_paid=False):
    prepared = prepared or {}
    existing_request_id = prepared.get('existing_request_id')

    if existing_request_id:
        clean_payload = prepare_request_payload(prepared.get('public_payload') or {}, remote_addr)
        metadata = clean_payload.get('metadata') or {}
        metadata.update({'service_flow': prepared.get('service_metadata') or {}})
        clean_payload['metadata'] = metadata
        status_value = 'survey_needed' if prepared.get('has_survey_request') else 'pending'

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE requests
            SET request_type=%s,
                name=%s,
                email=%s,
                phone=%s,
                subject=%s,
                service_name=%s,
                job_position=%s,
                context_page=%s,
                status=%s,
                source=%s,
                message=%s,
                metadata=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=%s
            """,
            (
                clean_payload.get('request_type'),
                clean_payload.get('name'),
                clean_payload.get('email') or None,
                clean_payload.get('phone') or None,
                clean_payload.get('subject') or None,
                clean_payload.get('service_name') or None,
                clean_payload.get('job_position') or None,
                clean_payload.get('context_page') or None,
                status_value,
                'stripe_checkout_paid',
                clean_payload.get('message') or None,
                json.dumps(clean_payload.get('metadata') or {}),
                existing_request_id
            )
        )
        conn.commit()
        cursor.close()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM requests WHERE id = %s", (existing_request_id,))
        request_record = cursor.fetchone()
        cursor.close()
        conn.close()

        if not request_record:
            raise ValueError('Unable to load provisional request for Stripe finalization.')

        queue_request_notifications(request_record, [])
        log_analytics_event('request_submission', {
            'request_type': clean_payload.get('request_type'),
            'source': 'stripe_checkout_paid',
            'service_name': clean_payload.get('service_name'),
            'job_position': clean_payload.get('job_position')
        })

        submission = {
            'message': 'Your request has been received.',
            'request_id': request_record.get('id'),
            'reference': request_record.get('ref_id'),
            'status': request_record.get('status'),
            'emails': {'queued': True}
        }
    else:
        submission = process_request_submission(
            prepared.get('public_payload') or {},
            uploaded_files=None,
            remote_addr=remote_addr,
            extra_metadata={'service_flow': prepared.get('service_metadata') or {}},
            status_override='survey_needed' if prepared.get('has_survey_request') else None
        )

    service_request_id = persist_service_request_bundle(
        prepared.get('customer_bundle') or {},
        prepared.get('schedule_bundle') or {},
        prepared.get('notes'),
        prepared.get('selections') or [],
        prepared.get('stored_total_for_db') if prepared.get('stored_total_for_db') is not None else prepared.get('subtotal'),
        bool(prepared.get('has_custom')),
        submission.get('request_id'),
        prepared.get('travel_quote') or {},
        prepared.get('pricing_details') or [],
        status_value='survey_needed' if prepared.get('has_survey_request') else 'pending',
        payment_type=prepared.get('payment_type_for_db') or 'in_person',
        is_paid=bool(mark_paid)
    )
    attach_service_request_reference(submission.get('request_id'), service_request_id)

    crm_request_id = None
    contract_id = None
    if prepared.get('has_contract_service'):
        try:
            crm_request_id = route_contract_booking_to_crm(prepared, submission, service_request_id)
        except Exception:
            app.logger.exception('Failed to route contract booking to CRM for request %s', submission.get('request_id'))
        try:
            contract_id = persist_contract_record(prepared, submission, service_request_id)
        except Exception:
            app.logger.exception('Failed to persist contract record for request %s', submission.get('request_id'))

    submission['service_request_id'] = service_request_id
    if crm_request_id:
        submission['crm_request_id'] = crm_request_id
    if contract_id:
        submission['contract_id'] = contract_id
    submission['travel'] = prepared.get('travel_quote') or {}
    if prepared.get('total_with_travel') is not None:
        submission['total_with_travel'] = prepared.get('total_with_travel')
    if prepared.get('payable_total') is not None:
        submission['payable_total'] = prepared.get('payable_total')
    submission['payment_type'] = prepared.get('payment_type_for_db') or 'in_person'
    submission['is_paid'] = bool(mark_paid)
    submission['payment_status_label'] = resolve_payment_status_label(submission.get('payment_type'), submission.get('is_paid'))
    submission['message'] = 'Thanks! Your survey request has been received. We will call to arrange a visit.' if prepared.get('has_survey_request') else 'Thanks! Your booking request has been received.'
    return submission


def create_stripe_pending_request(prepared, tx_id, remote_addr=None):
    clean_payload = prepare_request_payload(prepared.get('public_payload') or {}, remote_addr)
    clean_payload['source'] = 'stripe_checkout_pending'
    metadata = clean_payload.get('metadata') or {}
    metadata.update({
        'service_flow': prepared.get('service_metadata') or {},
        'stripe': {
            'transaction_id': tx_id,
            'status': 'checkout_pending'
        }
    })
    clean_payload['metadata'] = metadata
    request_record, _ = store_request(clean_payload, uploaded_files=None, status_override='pending')
    return request_record


def move_stale_stripe_pending_requests_to_draft(max_age_minutes=STRIPE_PENDING_TO_DRAFT_MINUTES, limit=100):
    ensure_payment_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if 'postgres' in engine:
        cursor.execute(
            """
            SELECT tx.id AS tx_id, tx.request_id
            FROM payment_transactions tx
            JOIN requests r ON r.id = tx.request_id
            WHERE tx.request_id IS NOT NULL
              AND tx.status IN ('initiated', 'checkout_created')
              AND r.status = 'pending'
              AND r.source = 'stripe_checkout_pending'
              AND tx.created_at <= NOW() - (%s * INTERVAL '1 minute')
            ORDER BY tx.created_at ASC
            LIMIT %s
            """,
            (max_age_minutes, max(1, int(limit)))
        )
    else:
        cursor.execute(
            """
            SELECT tx.id AS tx_id, tx.request_id
            FROM payment_transactions tx
            JOIN requests r ON r.id = tx.request_id
            WHERE tx.request_id IS NOT NULL
              AND tx.status IN ('initiated', 'checkout_created')
              AND r.status = 'pending'
              AND r.source = 'stripe_checkout_pending'
              AND tx.created_at <= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            ORDER BY tx.created_at ASC
            LIMIT %s
            """,
            (max_age_minutes, max(1, int(limit)))
        )

    stale_rows = cursor.fetchall() or []
    if stale_rows:
        writer = conn.cursor()
        for row in stale_rows:
            request_id = row.get('request_id')
            tx_id = row.get('tx_id')
            if request_id:
                writer.execute(
                    """
                    UPDATE requests
                    SET status = 'draft', updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'pending' AND source = 'stripe_checkout_pending'
                    """,
                    (request_id,)
                )
            if tx_id:
                writer.execute(
                    """
                    UPDATE payment_transactions
                    SET status = %s,
                        error_message = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    ('stale_pending', 'Moved to draft after payment timeout window.', tx_id)
                )
        conn.commit()
        writer.close()

    cursor.close()
    conn.close()


@app.route('/api/service-requests', methods=['POST'])
def create_service_request():
    payload = request.get_json(silent=True) or {}

    try:
        payment_settings = fetch_payment_settings()
        if payment_settings.get('require_payment'):
            return jsonify({'error': 'Payment is required before booking. Please proceed via checkout.'}), 402

        prepared = prepare_service_booking(payload)
        submission = finalize_prepared_service_booking(prepared, remote_addr=request.remote_addr)
        return jsonify(submission), 201
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to process service flow request')
        return jsonify({'error': 'Unable to submit request at this time.'}), 500


@app.route('/api/payments/start-checkout', methods=['POST'])
def start_stripe_checkout():
    payload = request.get_json(silent=True) or {}
    tx_id = None
    pending_request = None
    try:
        prepared = prepare_service_booking(payload)
        payment_settings = fetch_payment_settings()
        payment_option = prepared.get('payment_option') or PAYMENT_OPTION_IN_PERSON
        
        # DEBUG: Log the payment option for troubleshooting
        app.logger.info(f"start_stripe_checkout - Received payment_option from payload: {payload.get('payment_option')}")
        app.logger.info(f"start_stripe_checkout - Prepared payment_option: {prepared.get('payment_option')}")
        app.logger.info(f"start_stripe_checkout - Final payment_option after fallback: {payment_option}")
        app.logger.info(f"start_stripe_checkout - PAYMENT_OPTION_PREBOOK constant: {PAYMENT_OPTION_PREBOOK}")
        app.logger.info(f"start_stripe_checkout - Comparison result (payment_option != PAYMENT_OPTION_PREBOOK): {payment_option != PAYMENT_OPTION_PREBOOK}")
        app.logger.info(f"start_stripe_checkout - Payment settings: {payment_settings}")

        if payment_option != PAYMENT_OPTION_PREBOOK:
            submission = finalize_prepared_service_booking(prepared, remote_addr=request.remote_addr, mark_paid=False)
            submission['mode'] = 'direct'
            return jsonify(submission), 201

        if prepared.get('has_survey_request'):
            return jsonify({'error': 'This request requires a survey first, so payment cannot be taken yet.'}), 400
        if prepared.get('has_custom'):
            return jsonify({'error': 'This selection needs a custom quote before payment.'}), 400
        payable_total = normalize_price_value(prepared.get('payable_total'))
        if payable_total is None or payable_total <= 0:
            return jsonify({'error': 'Unable to calculate a payable total for this booking.'}), 400

        success_url = (payment_settings.get('success_url') or '').strip()
        cancel_url = (payment_settings.get('cancel_url') or '').strip()
        if not success_url or not cancel_url:
            app.logger.error(f"start_stripe_checkout - Missing Stripe URLs: success_url={success_url}, cancel_url={cancel_url}")
            return jsonify({'error': 'Stripe payment URLs are not configured. Please contact support.'}), 500

        if not stripe_ready():
            return jsonify({'error': 'Stripe is not configured. Set STRIPE_SECRET_KEY in environment variables.'}), 500

        stripe.api_key = stripe_secret_key()
        currency = (payment_settings.get('currency') or 'gbp').lower()
        amount_minor = money_to_minor_units(payable_total)
        service_label = prepared.get('primary_service_name') or 'Cleaning service'
        
        app.logger.info(f"start_stripe_checkout - Stripe setup: currency={currency}, payable_total={payable_total}, amount_minor={amount_minor}, service_label={service_label}")
        app.logger.info(f"start_stripe_checkout - Payment settings: success_url={payment_settings.get('success_url')}, cancel_url={payment_settings.get('cancel_url')}")

        tx_id = create_payment_transaction(
            amount_total=payable_total,
            currency=currency,
            customer_name=prepared.get('customer_name') or '',
            customer_email=prepared.get('customer_email') or '',
            service_summary=service_label,
            request_payload=json.dumps(payload),
            prepared_payload=json.dumps(prepared, default=str)
        )

        try:
            pending_request = create_stripe_pending_request(prepared, tx_id, request.remote_addr)
            if pending_request and pending_request.get('id'):
                update_payment_transaction(tx_id, request_id=pending_request.get('id'))
        except Exception:
            app.logger.exception('Unable to create provisional request for Stripe transaction %s', tx_id)
        
        app.logger.info(f"start_stripe_checkout - Payment transaction created: tx_id={tx_id}")

        app.logger.info(f"start_stripe_checkout - Calling stripe.checkout.Session.create()...")
        session_data = stripe.checkout.Session.create(
            mode='payment',
            payment_method_types=['card'],
            customer_email=prepared.get('customer_email') or None,
            success_url=payment_settings.get('success_url'),
            cancel_url=payment_settings.get('cancel_url'),
            payment_intent_data={
                'metadata': {
                    'transaction_id': str(tx_id)
                }
            },
            line_items=[
                {
                    'price_data': {
                        'currency': currency,
                        'product_data': {
                            'name': f"{service_label} booking",
                            'description': 'Booking is confirmed after successful payment.'
                        },
                        'unit_amount': amount_minor
                    },
                    'quantity': 1
                }
            ],
            metadata={
                'transaction_id': str(tx_id)
            }
        )
        
        session_id_val = session_data.id
        session_url_val = session_data.url
        app.logger.info(f"start_stripe_checkout - Stripe session created successfully: session_id={session_id_val}, url={session_url_val}")

        update_payment_transaction(
            tx_id,
            checkout_session_id=session_id_val,
            status='checkout_created',
            request_id=(pending_request or {}).get('id')
        )

        return jsonify({
            'mode': 'checkout',
            'checkout_url': session_url_val,
            'session_id': session_id_val,
            'transaction_id': tx_id,
            'request_id': (pending_request or {}).get('id'),
            'reference': (pending_request or {}).get('ref_id'),
            'amount_total': payable_total,
            'original_amount': prepared.get('total_with_travel'),
            'discount_amount': prepared.get('prebook_discount_amount'),
            'currency': currency
        }), 200
    except ValueError as exc:
        app.logger.error(f"start_stripe_checkout - ValueError: {str(exc)}")
        return jsonify({'error': str(exc)}), 400
    except Exception as exc:
        app.logger.error(f"start_stripe_checkout - Exception occurred: {type(exc).__name__}: {str(exc)}")
        app.logger.exception('start_stripe_checkout - Full traceback:')
        if tx_id:
            try:
                update_payment_transaction(tx_id, status='checkout_failed', error_message=str(exc)[:1500])
            except Exception:
                pass
        if pending_request and pending_request.get('id'):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE requests SET status = 'draft', source = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    ('stripe_checkout_failed', pending_request.get('id'))
                )
                conn.commit()
                cursor.close()
                conn.close()
            except Exception:
                pass
        return jsonify({'error': 'Unable to start payment right now.'}), 500


def process_completed_payment_session(session_obj):
    session_id = session_obj.get('id')
    tx = fetch_payment_transaction_by_session(session_id)
    if not tx:
        app.logger.warning('Stripe webhook session %s has no matching local transaction.', session_id)
        return

    if tx.get('request_id') and tx.get('service_request_id'):
        update_payment_transaction(
            tx.get('id'),
            status='processed',
            payment_intent_id=session_obj.get('payment_intent')
        )
        return

    update_payment_transaction(
        tx.get('id'),
        status='paid',
        payment_intent_id=session_obj.get('payment_intent')
    )

    try:
        prepared_payload = tx.get('prepared_payload') or '{}'
        prepared = json.loads(prepared_payload)
        if not prepared.get('payment_type_for_db'):
            prepared['payment_type_for_db'] = 'stripe'

        service_metadata = prepared.get('service_metadata') if isinstance(prepared.get('service_metadata'), dict) else {}
        payment_meta = service_metadata.get('payment') if isinstance(service_metadata.get('payment'), dict) else {}
        payment_meta.update({
            'option': PAYMENT_OPTION_PREBOOK,
            'payment_type': 'stripe',
            'is_paid': True,
            'status_label': resolve_payment_status_label('stripe', True),
            'transaction_id': tx.get('id'),
            'stripe_checkout_session_id': session_obj.get('id') or tx.get('checkout_session_id'),
            'stripe_payment_intent_id': session_obj.get('payment_intent') or tx.get('payment_intent_id'),
            'stripe_payment_status': session_obj.get('payment_status')
        })
        service_metadata['payment'] = payment_meta
        prepared['service_metadata'] = service_metadata

        if tx.get('request_id'):
            prepared['existing_request_id'] = tx.get('request_id')

        submission = finalize_prepared_service_booking(prepared, remote_addr='stripe-webhook', mark_paid=True)
        update_payment_transaction(
            tx.get('id'),
            status='processed',
            request_id=submission.get('request_id'),
            service_request_id=submission.get('service_request_id')
        )
    except Exception as exc:
        app.logger.exception('Failed to finalize paid booking for transaction %s', tx.get('id'))
        update_payment_transaction(
            tx.get('id'),
            status='processing_failed',
            error_message=str(exc)[:1500]
        )


def resolve_stripe_event_object(event, event_type):
    raw_data_object = (event.get('data') or {}).get('object')
    if isinstance(raw_data_object, dict) and raw_data_object.get('id'):
        return raw_data_object

    related = event.get('related_object') or {}
    related_id = ''
    if isinstance(related, dict):
        related_id = sanitize_text(related.get('id'), 255)

    if not related_id and isinstance(raw_data_object, str):
        related_id = sanitize_text(raw_data_object, 255)

    if not related_id:
        return raw_data_object if isinstance(raw_data_object, dict) else {}

    try:
        stripe.api_key = stripe_secret_key()
        if event_type.startswith('checkout.session.'):
            session = stripe.checkout.Session.retrieve(related_id)
            return stripe_object_to_dict(session)
        if event_type == 'payment_intent.payment_failed':
            payment_intent = stripe.PaymentIntent.retrieve(related_id)
            return stripe_object_to_dict(payment_intent)
    except Exception:
        app.logger.exception('Failed to expand Stripe thin event object for %s', event_type)

    return raw_data_object if isinstance(raw_data_object, dict) else {}


@app.route('/api/payments/stripe/webhook', methods=['POST'])
def stripe_webhook_endpoint():
    # CRITICAL: Always return 200 to Stripe, even on config/processing errors.
    # Returning non-200 causes Stripe to retry and eventually disable the endpoint.

    if not stripe_ready():
        app.logger.error('Stripe webhook received but Stripe is not configured (missing secret key).')
        return jsonify({'received': True}), 200

    signing_secret = stripe_webhook_secret()
    if not stripe_webhook_secret_valid():
        app.logger.error('Stripe webhook received but webhook secret (whsec_) is not configured.')
        return jsonify({'received': True}), 200

    payload = request.get_data()
    signature = request.headers.get('Stripe-Signature', '')
    try:
        event = stripe.Webhook.construct_event(payload, signature, signing_secret)
        # Newer Stripe SDK returns a StripeObject, not a plain dict — convert immediately
        # so all downstream code can use plain .get() / dict access safely.
        if not isinstance(event, dict):
            event = json.loads(payload)
    except Exception:
        # Signature mismatch — likely a forged/replayed request. Return 400 is correct here
        # as this is NOT a Stripe delivery failure; it's an invalid request we should reject.
        app.logger.warning('Invalid Stripe webhook signature for /api/payments/stripe/webhook.')
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event.get('type', '')
    try:
        event_data = resolve_stripe_event_object(event, event_type)

        if event_type == 'checkout.session.completed':
            process_completed_payment_session(event_data)

        elif event_type == 'payment_intent.succeeded':
            # Fallback: finalize booking via PaymentIntent if checkout.session.completed was missed/failed
            pi_id = sanitize_text(event_data.get('id'), 255)
            metadata = event_data.get('metadata') or {}
            tx_id_raw = metadata.get('transaction_id') if isinstance(metadata, dict) else None
            tx = None
            if tx_id_raw:
                try:
                    tx = fetch_payment_transaction_by_id(int(str(tx_id_raw).strip()))
                except (TypeError, ValueError):
                    pass
            if not tx and pi_id:
                tx = fetch_payment_transaction_by_payment_intent(pi_id)
            if tx and tx.get('status') not in ('processed', 'paid'):
                fake_session = {
                    'id': tx.get('checkout_session_id') or '',
                    'payment_intent': pi_id,
                    'payment_status': 'paid',
                }
                process_completed_payment_session(fake_session)

        elif event_type in ('checkout.session.expired', 'payment_intent.payment_failed'):
            tx = None
            session_id = sanitize_text(event_data.get('id') or event_data.get('checkout_session'), 255)
            payment_intent_id = ''

            if event_type == 'checkout.session.expired':
                tx = fetch_payment_transaction_by_session(session_id)
            else:
                payment_intent_id = sanitize_text(event_data.get('id') or event_data.get('payment_intent'), 255)
                if payment_intent_id:
                    tx = fetch_payment_transaction_by_payment_intent(payment_intent_id)

                metadata = event_data.get('metadata') or {}
                tx_id_raw = metadata.get('transaction_id') if isinstance(metadata, dict) else None
                if not tx and tx_id_raw:
                    try:
                        tx_id = int(str(tx_id_raw).strip())
                        tx = fetch_payment_transaction_by_id(tx_id)
                    except (TypeError, ValueError):
                        tx = None

                if not tx and session_id:
                    tx = fetch_payment_transaction_by_session(session_id)

            if tx:
                error_message = ''
                if event_type == 'payment_intent.payment_failed':
                    last_error = event_data.get('last_payment_error') or {}
                    if isinstance(last_error, dict):
                        error_message = sanitize_text(last_error.get('message'), 1500)

                update_payment_transaction(
                    tx.get('id'),
                    status='failed',
                    payment_intent_id=payment_intent_id or tx.get('payment_intent_id'),
                    error_message=error_message or tx.get('error_message')
                )

        elif event_type == 'charge.succeeded':
            # Acknowledged — no action needed; checkout.session.completed handles booking finalization
            pass

        else:
            # Unrecognised event type — log and acknowledge
            app.logger.info('Stripe webhook: unhandled event type %s (acknowledged OK)', event_type)

    except Exception:
        # Log the full traceback but still return 200 so Stripe doesn't retry/disable
        app.logger.exception('Stripe webhook processing error for event type %s', event_type)

    return jsonify({'received': True}), 200


@app.route('/payment/callback/success', methods=['GET'])
def payment_callback_success():
    session_id = sanitize_text(request.args.get('session_id'), 255)
    target = url_for('index')

    if session_id:
        try:
            tx = fetch_payment_transaction_by_session(session_id)
            already_finalized = tx and tx.get('request_id') and tx.get('service_request_id')
            already_failed = tx and (tx.get('status') or '') == 'processing_failed'
            if tx and not already_finalized and not already_failed and stripe_ready():
                stripe.api_key = stripe_secret_key()
                session_obj = stripe.checkout.Session.retrieve(session_id)
                # Use getattr directly — stripe_object_to_dict can drop nested fields
                payment_status = (getattr(session_obj, 'payment_status', None) or '').lower()
                payment_intent_raw = getattr(session_obj, 'payment_intent', None)
                payment_intent_id = (
                    payment_intent_raw if isinstance(payment_intent_raw, str)
                    else getattr(payment_intent_raw, 'id', None) if payment_intent_raw else None
                )
                session_id_val = getattr(session_obj, 'id', None) or session_id
                app.logger.info(
                    'Success callback for session %s: payment_status=%s, pi=%s, tx_status=%s',
                    session_id_val, payment_status, payment_intent_id, tx.get('status')
                )
                if payment_status == 'paid':
                    session_data = {
                        'id': session_id_val,
                        'payment_status': payment_status,
                        'payment_intent': payment_intent_id,
                    }
                    process_completed_payment_session(session_data)
                elif tx.get('status') == 'paid' and not already_finalized:
                    # Webhook already marked it paid but finalization may not have run
                    session_data = {
                        'id': session_id_val,
                        'payment_status': 'paid',
                        'payment_intent': payment_intent_id or tx.get('payment_intent_id'),
                    }
                    process_completed_payment_session(session_data)
        except Exception:
            app.logger.exception('Success callback fallback finalization failed for session %s', session_id)

    return redirect(f"{target}?payment=success")


@app.route('/payment/callback/cancel', methods=['GET'])
def payment_callback_cancel():
    target = url_for('index')
    return redirect(f"{target}?payment=cancelled")


@app.route('/api/job-positions', methods=['GET'])
def get_job_positions():
    try:
        jobs = fetch_job_positions_from_db()
        return jsonify(jobs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/services/add', methods=['POST'])
@admin_login_required
def add_service():
    try:
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        service_name = sanitize_text(request.form.get('name') or request.form.get('title'), 120)
        short_description = (request.form.get('short_description') or '').strip()
        description = (request.form.get('description') or '').strip()
        raw_price = request.form.get('price')
        raw_discount_threshold = request.form.get('discount_threshold')
        raw_discount_percent = request.form.get('discount_percent')
        is_contract = str_to_bool(request.form.get('is_contract', '0'))
        _raw_sc = normalize_service_category(request.form.get('service_category'))
        if _raw_sc == 'hybrid':
            service_category = 'hybrid'
            is_contract = True  # hybrid gets contract pricing plans
        else:
            service_category = 'contract' if is_contract else _raw_sc
        pricing_model = (request.form.get('pricing_model') or 'simple').strip().lower()
        is_active = str_to_bool(request.form.get('is_active', '1'))

        # New config fields
        table_header_col1 = (request.form.get('table_header_col1') or 'Property Type').strip()[:100]
        table_header_col2 = (request.form.get('table_header_col2') or 'Standard Price').strip()[:100]
        table_header_col3 = (request.form.get('table_header_col3') or 'Upgrade Option').strip()[:100]
        allow_multiselect = str_to_bool(request.form.get('allow_multiselect', '0'))
        contract_plans = build_contract_pricing_plans_from_form(request.form)
        contract_pricing_plans_json = json.dumps(contract_plans)

        # Contract intro fields (residential-style detail page content)
        contract_section_title = sanitize_text(request.form.get('contract_section_title'), 255)
        contract_section_subtitle = sanitize_text(request.form.get('contract_section_subtitle'))
        contract_intro_title = sanitize_text(request.form.get('contract_intro_title'), 255)
        contract_intro_body = sanitize_text(request.form.get('contract_intro_body'))
        contract_trust_body = sanitize_text(request.form.get('contract_trust_body'))
        contract_continuity_body = sanitize_text(request.form.get('contract_continuity_body'))

        if not service_name:
            return jsonify({'error': 'Service name is required.'}), 400
        if not description:
            return jsonify({'error': 'Description is required.'}), 400

        # Validate pricing_model
        valid_pricing_models = ('simple', 'options', 'tenancy', 'deep', 'airbnb', 'itemized')
        if pricing_model not in valid_pricing_models:
            pricing_model = 'simple'

        if not short_description:
            short_description = description[:150]

        try:
            price = float(raw_price) if raw_price not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Price must be a number or left blank for custom quotes.'}), 400

        try:
            discount_threshold = float(raw_discount_threshold) if raw_discount_threshold not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Discount threshold must be a valid number.'}), 400

        try:
            discount_percent = float(raw_discount_percent) if raw_discount_percent not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Discount percent must be a valid number.'}), 400

        try:
            image_path = upload_service_image()
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

        is_pg = 'postgres' in engine
        contract_value = True if is_contract else False  # is_contract is boolean in PG
        multiselect_value = 1 if allow_multiselect else 0  # smallint in PG
        active_value = 1 if is_active else 0  # smallint in PG

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO services (title, name, short_description, description, price, discount_threshold, discount_percent,
                service_category, contract_pricing_plans, is_contract,
                contract_section_title, contract_section_subtitle, contract_intro_title, contract_intro_body, contract_trust_body, contract_continuity_body,
                pricing_model, table_header_col1, table_header_col2, table_header_col3, allow_multiselect, image_path, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                service_name,
                service_name,
                short_description,
                description,
                price,
                discount_threshold,
                discount_percent,
                service_category,
                contract_pricing_plans_json,
                contract_value,
                contract_section_title,
                contract_section_subtitle,
                contract_intro_title,
                contract_intro_body,
                contract_trust_body,
                contract_continuity_body,
                pricing_model,
                table_header_col1,
                table_header_col2,
                table_header_col3,
                multiselect_value,
                image_path or None,
                active_value
            )
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Service added!'}), 201
    except Exception as e:
        app.logger.exception('Failed to add service')
        return jsonify({'error': str(e)}), 500


@app.route('/api/services/edit/<int:service_id>', methods=['PUT'])
@admin_login_required
def edit_service(service_id):
    try:
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        service_name = sanitize_text(request.form.get('name') or request.form.get('title'), 120)
        short_description = (request.form.get('short_description') or '').strip()
        description = (request.form.get('description') or '').strip()
        raw_price = request.form.get('price')
        raw_discount_threshold = request.form.get('discount_threshold')
        raw_discount_percent = request.form.get('discount_percent')
        is_contract = str_to_bool(request.form.get('is_contract', '0'))
        _raw_sc = normalize_service_category(request.form.get('service_category'))
        if _raw_sc == 'hybrid':
            service_category = 'hybrid'
            is_contract = True  # hybrid gets contract pricing plans
        else:
            service_category = 'contract' if is_contract else _raw_sc
        pricing_model = (request.form.get('pricing_model') or '').strip().lower()
        existing_image = request.form.get('existing_image', '')
        is_active = str_to_bool(request.form.get('is_active', '1'))

        # Config fields
        table_header_col1 = (request.form.get('table_header_col1') or 'Property Type').strip()[:100]
        table_header_col2 = (request.form.get('table_header_col2') or 'Standard Price').strip()[:100]
        table_header_col3 = (request.form.get('table_header_col3') or 'Upgrade Option').strip()[:100]
        allow_multiselect = str_to_bool(request.form.get('allow_multiselect', '0'))
        contract_plans = build_contract_pricing_plans_from_form(request.form)
        contract_pricing_plans_json = json.dumps(contract_plans)

        # Contract intro fields
        contract_section_title = sanitize_text(request.form.get('contract_section_title'), 255)
        contract_section_subtitle = sanitize_text(request.form.get('contract_section_subtitle'))
        contract_intro_title = sanitize_text(request.form.get('contract_intro_title'), 255)
        contract_intro_body = sanitize_text(request.form.get('contract_intro_body'))
        contract_trust_body = sanitize_text(request.form.get('contract_trust_body'))
        contract_continuity_body = sanitize_text(request.form.get('contract_continuity_body'))

        # Trust / continuity card images
        existing_trust_image = request.form.get('existing_trust_image', '').strip()
        existing_continuity_image = request.form.get('existing_continuity_image', '').strip()
        remove_trust_image = str_to_bool(request.form.get('remove_trust_image', 'false'))
        remove_continuity_image = str_to_bool(request.form.get('remove_continuity_image', 'false'))

        if remove_trust_image:
            if existing_trust_image:
                delete_uploaded_file(existing_trust_image)
            contract_trust_image = None
        else:
            contract_trust_image = handle_upload('contract_trust_image', SERVICE_UPLOAD_FOLDER, existing_trust_image) or None

        if remove_continuity_image:
            if existing_continuity_image:
                delete_uploaded_file(existing_continuity_image)
            contract_continuity_image = None
        else:
            contract_continuity_image = handle_upload('contract_continuity_image', SERVICE_UPLOAD_FOLDER, existing_continuity_image) or None

        if not service_name:
            return jsonify({'error': 'Service name is required.'}), 400
        if not description:
            return jsonify({'error': 'Description is required.'}), 400

        # Validate pricing_model
        valid_pricing_models = ('simple', 'options', 'tenancy', 'deep', 'airbnb', 'itemized')
        if pricing_model and pricing_model not in valid_pricing_models:
            pricing_model = None

        if not short_description:
            short_description = description[:150]

        try:
            price = float(raw_price) if raw_price not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Price must be a number or left blank for custom quotes.'}), 400

        try:
            discount_threshold = float(raw_discount_threshold) if raw_discount_threshold not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Discount threshold must be a valid number.'}), 400

        try:
            discount_percent = float(raw_discount_percent) if raw_discount_percent not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'Discount percent must be a valid number.'}), 400

        try:
            image_path = upload_service_image(existing_image)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

        if image_path and existing_image and image_path != existing_image:
            delete_uploaded_file(existing_image)

        is_pg = 'postgres' in engine
        contract_value = True if is_contract else False  # is_contract is boolean in PG
        multiselect_value = 1 if allow_multiselect else 0  # smallint in PG
        active_value = 1 if is_active else 0  # smallint in PG

        conn = get_db_connection()
        cursor = conn.cursor()
        set_clauses = [
            "title=%s", "name=%s", "short_description=%s", "description=%s",
            "price=%s", "discount_threshold=%s", "discount_percent=%s",
            "service_category=%s", "contract_pricing_plans=%s", "is_contract=%s",
            "contract_section_title=%s", "contract_section_subtitle=%s",
            "contract_intro_title=%s", "contract_intro_body=%s",
            "contract_trust_body=%s", "contract_trust_image=%s",
            "contract_continuity_body=%s", "contract_continuity_image=%s",
            "table_header_col1=%s", "table_header_col2=%s", "table_header_col3=%s",
            "allow_multiselect=%s", "is_active=%s"
        ]
        params = [
            service_name, service_name, short_description, description,
            price, discount_threshold, discount_percent,
            service_category, contract_pricing_plans_json, contract_value,
            contract_section_title, contract_section_subtitle,
            contract_intro_title, contract_intro_body,
            contract_trust_body, contract_trust_image,
            contract_continuity_body, contract_continuity_image,
            table_header_col1, table_header_col2, table_header_col3,
            multiselect_value, active_value
        ]
        if pricing_model:
            set_clauses.append("pricing_model=%s")
            params.append(pricing_model)
        if image_path:
            set_clauses.append("image_path=%s")
            params.append(image_path)
        params.append(service_id)
        cursor.execute(
            f"UPDATE services SET {', '.join(set_clauses)} WHERE id=%s",
            params
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Service updated!'})
    except Exception as e:
        app.logger.exception('Failed to update service')
        return jsonify({'error': str(e)}), 500


@app.route('/api/services/delete/<int:service_id>', methods=['DELETE'])
@admin_login_required
def delete_service(service_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT image_path FROM services WHERE id=%s", (service_id,))
        row = cursor.fetchone()
        old_image = row.get('image_path', '') if row else ''
        # Collect room card images for cleanup
        cursor.execute("SELECT image_path FROM service_room_cards WHERE service_id=%s", (service_id,))
        room_card_images = [r.get('image_path') for r in cursor.fetchall() if r.get('image_path')]
        cursor.close()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM service_room_cards WHERE service_id=%s", (service_id,))
        cursor.execute("DELETE FROM services WHERE id=%s", (service_id,))
        conn.commit()
        cursor.close()
        conn.close()
        if old_image:
            delete_uploaded_file(old_image)
        for img in room_card_images:
            delete_uploaded_file(img)
        return jsonify({'message': 'Service deleted!'})
    except Exception as e:
        app.logger.exception('Failed to delete service')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/services/<int:service_id>/options', methods=['POST'])
@admin_login_required
def add_service_option(service_id):
    payload = request.get_json(silent=True) or {}
    label = sanitize_text(payload.get('label'), 150)
    sort_order = payload.get('sort_order', 0)
    is_active = str_to_bool(payload.get('is_active', '1'))

    try:
        sort_order = int(sort_order)
    except (TypeError, ValueError):
        sort_order = 0

    if not label:
        return jsonify({'error': 'Option label is required.'}), 400

    try:
        price = parse_price_input(payload.get('price'))
    except ValueError:
        return jsonify({'error': 'Invalid price supplied.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM services WHERE id = %s", (service_id,))
    service_row = cursor.fetchone()
    if not service_row:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Service not found.'}), 404

    cursor.close()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO service_options (service_id, label, price, sort_order, is_active)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (service_id, label, price, sort_order, 1 if is_active else 0)
    )
    option_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({
        'id': option_id,
        'label': label,
        'price': normalize_price_value(price),
        'sort_order': sort_order,
        'is_active': bool(is_active)
    }), 201


@app.route('/admin/api/services/<int:service_id>/options/<int:option_id>', methods=['PUT'])
@admin_login_required
def update_service_option(service_id, option_id):
    payload = request.get_json(silent=True) or {}
    label = sanitize_text(payload.get('label'), 150)
    sort_order = payload.get('sort_order')
    is_active = payload.get('is_active')

    try:
        price = parse_price_input(payload.get('price'))
    except ValueError:
        return jsonify({'error': 'Invalid price supplied.'}), 400

    updates = []
    params = []
    if label:
        updates.append('label = %s')
        params.append(label)
    if price is not None or payload.get('price') in (None, ''):
        updates.append('price = %s')
        params.append(price)
    if sort_order is not None:
        try:
            sort_order = int(sort_order)
        except (TypeError, ValueError):
            sort_order = 0
        params.append(sort_order)
    if is_active is not None:
        updates.append('is_active = %s')
        params.append(1 if str_to_bool(is_active) else 0)

    if not updates:
        return jsonify({'error': 'No updates supplied.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    updates.append('updated_at = CURRENT_TIMESTAMP')
    set_clause = ', '.join(updates)
    cursor.execute(
        f"UPDATE service_options SET {set_clause} WHERE id = %s AND service_id = %s",
        (*params, option_id, service_id)
    )
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Option not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Option updated.'})


@app.route('/admin/api/services/<int:service_id>/options/<int:option_id>', methods=['DELETE'])
@admin_login_required
def delete_service_option(service_id, option_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM service_options WHERE id = %s AND service_id = %s",
        (option_id, service_id)
    )
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Option not found.'}), 404
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Option deleted.'})


@app.route('/admin/api/services/<int:service_id>/options/reorder', methods=['POST'])
@admin_login_required
def reorder_service_options(service_id):
    payload = request.get_json(silent=True) or {}
    order = payload.get('order')
    if not isinstance(order, list) or not order:
        return jsonify({'error': 'Option order must be an array of IDs.'}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    for index, option_id in enumerate(order, start=1):
        cursor.execute(
            "UPDATE service_options SET sort_order = %s WHERE id = %s AND service_id = %s",
            (index, option_id, service_id)
        )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Option order updated.'})


# Pricing items (e.g., carpet/upholstery line items)
@app.route('/admin/api/services/<int:service_id>/pricing/items', methods=['POST'])
@admin_login_required
def add_pricing_item(service_id):
    payload = request.get_json(silent=True) or {}
    item_name = sanitize_text(payload.get('item_name'), 255)
    try:
        price = parse_price_input(payload.get('price'))
    except ValueError:
        return jsonify({'error': 'Invalid price supplied.'}), 400

    if not item_name:
        return jsonify({'error': 'Item name is required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM services WHERE id = %s", (service_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({'error': 'Service not found.'}), 404

    cursor.close()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO service_pricing_items (service_id, item_name, price)
        VALUES (%s, %s, %s)
        """,
        (service_id, item_name, price)
    )
    conn.commit()
    item_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return jsonify({'message': 'Pricing item added.', 'id': item_id}), 201


@app.route('/admin/api/pricing/items/<int:item_id>', methods=['PUT'])
@admin_login_required
def update_pricing_item(item_id):
    payload = request.get_json(silent=True) or {}
    updates = []
    params = []

    item_name = sanitize_text(payload.get('item_name'), 255)
    if item_name:
        updates.append('item_name = %s')
        params.append(item_name)

    if 'price' in payload:
        try:
            price = parse_price_input(payload.get('price'))
        except ValueError:
            return jsonify({'error': 'Invalid price supplied.'}), 400
        updates.append('price = %s')
        params.append(price)

    if not updates:
        return jsonify({'error': 'No updates supplied.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    set_clause = ', '.join(updates)
    cursor.execute(
        f"UPDATE service_pricing_items SET {set_clause} WHERE id = %s",
        (*params, item_id)
    )
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Pricing item not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Pricing item updated.'})


@app.route('/admin/api/pricing/items/<int:item_id>', methods=['DELETE'])
@admin_login_required
def delete_pricing_item(item_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM service_pricing_items WHERE id = %s", (item_id,))
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Pricing item not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Pricing item deleted.'})


# Pricing tiers (e.g., deep cleaning hourly tiers)
@app.route('/admin/api/services/<int:service_id>/pricing/tiers', methods=['POST'])
@admin_login_required
def add_pricing_tier(service_id):
    payload = request.get_json(silent=True) or {}
    tier_name = sanitize_text(payload.get('tier_name'), 255)
    try:
        hourly_rate = parse_price_input(payload.get('hourly_rate'))
        equipment_fee = parse_price_input(payload.get('equipment_fee'))
        detergent_fee = parse_price_input(payload.get('detergent_fee'))
    except ValueError:
        return jsonify({'error': 'Invalid price supplied.'}), 400

    try:
        min_staff = int(payload.get('min_staff')) if payload.get('min_staff') not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'min_staff must be a whole number or blank.'}), 400

    if not tier_name:
        return jsonify({'error': 'Tier name is required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM services WHERE id = %s", (service_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({'error': 'Service not found.'}), 404

    cursor.close()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO service_pricing_tiers (service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (service_id, tier_name, hourly_rate, min_staff, equipment_fee, detergent_fee)
    )
    conn.commit()
    tier_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return jsonify({'message': 'Pricing tier added.', 'id': tier_id}), 201


@app.route('/admin/api/pricing/tiers/<int:tier_id>', methods=['PUT'])
@admin_login_required
def update_pricing_tier(tier_id):
    payload = request.get_json(silent=True) or {}
    updates = []
    params = []

    tier_name = sanitize_text(payload.get('tier_name'), 255)
    if tier_name:
        updates.append('tier_name = %s')
        params.append(tier_name)

    if 'hourly_rate' in payload:
        try:
            hourly_rate = parse_price_input(payload.get('hourly_rate'))
        except ValueError:
            return jsonify({'error': 'Invalid price supplied for hourly_rate.'}), 400
        updates.append('hourly_rate = %s')
        params.append(hourly_rate)

    if 'equipment_fee' in payload:
        try:
            equipment_fee = parse_price_input(payload.get('equipment_fee'))
        except ValueError:
            return jsonify({'error': 'Invalid price supplied for equipment_fee.'}), 400
        updates.append('equipment_fee = %s')
        params.append(equipment_fee)

    if 'detergent_fee' in payload:
        try:
            detergent_fee = parse_price_input(payload.get('detergent_fee'))
        except ValueError:
            return jsonify({'error': 'Invalid price supplied for detergent_fee.'}), 400
        updates.append('detergent_fee = %s')
        params.append(detergent_fee)

    if 'min_staff' in payload:
        try:
            min_staff = int(payload.get('min_staff')) if payload.get('min_staff') not in (None, '') else None
        except (TypeError, ValueError):
            return jsonify({'error': 'min_staff must be a whole number or blank.'}), 400
        updates.append('min_staff = %s')
        params.append(min_staff)

    if not updates:
        return jsonify({'error': 'No updates supplied.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    updates.append('updated_at = CURRENT_TIMESTAMP')
    set_clause = ', '.join(updates)
    cursor.execute(
        f"UPDATE service_pricing_tiers SET {set_clause} WHERE id = %s",
        (*params, tier_id)
    )
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Pricing tier not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Pricing tier updated.'})


@app.route('/admin/api/pricing/tiers/<int:tier_id>', methods=['DELETE'])
@admin_login_required
def delete_pricing_tier(tier_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM service_pricing_tiers WHERE id = %s", (tier_id,))
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Pricing tier not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Pricing tier deleted.'})


# Tenancy rates (end of tenancy)
@app.route('/admin/api/services/<int:service_id>/pricing/tenancy', methods=['POST'])
@admin_login_required
def add_tenancy_rate(service_id):
    payload = request.get_json(silent=True) or {}
    label = sanitize_text(payload.get('label'), 255)
    blocker_msg = sanitize_text(payload.get('blocker_msg'), 500)
    is_blocker = str_to_bool(payload.get('is_blocker', '0'))

    try:
        standard_price = parse_price_input(payload.get('standard_price'))
        deep_clean_price = parse_price_input(payload.get('deep_clean_price'))
    except ValueError:
        return jsonify({'error': 'Invalid price supplied.'}), 400

    if not label:
        return jsonify({'error': 'Label is required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM services WHERE id = %s", (service_id,))
    if not cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({'error': 'Service not found.'}), 404

    cursor.close()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO service_tenancy_rates (service_id, label, standard_price, deep_clean_price, is_blocker, blocker_msg)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (service_id, label, standard_price, deep_clean_price, 1 if is_blocker else 0, blocker_msg)
    )
    conn.commit()
    rate_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return jsonify({'message': 'Tenancy rate added.', 'id': rate_id}), 201


@app.route('/admin/api/pricing/tenancy/<int:rate_id>', methods=['PUT'])
@admin_login_required
def update_tenancy_rate(rate_id):
    payload = request.get_json(silent=True) or {}
    updates = []
    params = []

    label = sanitize_text(payload.get('label'), 255)
    if label:
        updates.append('label = %s')
        params.append(label)

    if 'standard_price' in payload:
        try:
            standard_price = parse_price_input(payload.get('standard_price'))
        except ValueError:
            return jsonify({'error': 'Invalid price supplied for standard_price.'}), 400
        updates.append('standard_price = %s')
        params.append(standard_price)

    if 'deep_clean_price' in payload:
        try:
            deep_clean_price = parse_price_input(payload.get('deep_clean_price'))
        except ValueError:
            return jsonify({'error': 'Invalid price supplied for deep_clean_price.'}), 400
        updates.append('deep_clean_price = %s')
        params.append(deep_clean_price)

    if 'is_blocker' in payload:
        updates.append('is_blocker = %s')
        params.append(1 if str_to_bool(payload.get('is_blocker')) else 0)

    if 'blocker_msg' in payload:
        blocker_msg = sanitize_text(payload.get('blocker_msg'), 500)
        updates.append('blocker_msg = %s')
        params.append(blocker_msg)

    if not updates:
        return jsonify({'error': 'No updates supplied.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    set_clause = ', '.join(updates)
    cursor.execute(
        f"UPDATE service_tenancy_rates SET {set_clause} WHERE id = %s",
        (*params, rate_id)
    )
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Tenancy rate not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Tenancy rate updated.'})


@app.route('/admin/api/pricing/tenancy/<int:rate_id>', methods=['DELETE'])
@admin_login_required
def delete_tenancy_rate(rate_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM service_tenancy_rates WHERE id = %s", (rate_id,))
    if cursor.rowcount == 0:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Tenancy rate not found.'}), 404

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Tenancy rate deleted.'})


@app.route('/api/job-positions/add', methods=['POST'])
def add_job_position():
    try:
        title = request.form['title']
        description = request.form['description']
        try:
            image_path = upload_job_image()
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO job_positions (title, description, image_path) VALUES (%s, %s, %s)",
            (title, description, image_path)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Job position added!'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/job-positions/edit/<int:id>', methods=['PUT'])
def edit_job_position(id):
    try:
        title = request.form['title']
        description = request.form['description']
        existing_image = request.form.get('existing_image', '')
        try:
            image_path = upload_job_image(existing_image)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

        # If image changed, delete the old one from Cloudinary / disk
        if image_path and existing_image and image_path != existing_image:
            delete_uploaded_file(existing_image)

        conn = get_db_connection()
        cursor = conn.cursor()
        if image_path:
            cursor.execute(
                "UPDATE job_positions SET title=%s, description=%s, image_path=%s WHERE id=%s",
                (title, description, image_path, id)
            )
        else:
            cursor.execute(
                "UPDATE job_positions SET title=%s, description=%s WHERE id=%s",
                (title, description, id)
            )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Job position updated!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/job-positions/delete/<int:id>', methods=['DELETE'])
def delete_job_position(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT image_path FROM job_positions WHERE id=%s", (id,))
        row = cursor.fetchone()
        old_image = row.get('image_path', '') if row else ''
        cursor.close()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM job_positions WHERE id=%s", (id,))
        conn.commit()
        cursor.close()
        conn.close()
        if old_image:
            delete_uploaded_file(old_image)
        return jsonify({'message': 'Job position deleted!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/testimonials', methods=['GET'])
def get_testimonials():
    try:
        # Admin can pass ?include_all=1 to get pending testimonials too
        include_all = request.args.get('include_all', '0') == '1'
        testimonials = fetch_testimonials_from_db(include_pending=include_all)
        return jsonify(testimonials)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/faqs', methods=['GET'])
def get_public_faqs():
    """Public endpoint to get active FAQs."""
    try:
        ensure_faq_table()
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        if engine == 'postgres':
            cursor.execute("SELECT id, question, answer, category FROM faqs WHERE is_active = TRUE ORDER BY sort_order ASC, id ASC")
        else:
            cursor.execute("SELECT id, question, answer, category FROM faqs WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
        faqs = cursor.fetchall()
        cursor.close()
        conn.close()
        return jsonify(faqs)
    except Exception as e:
        app.logger.exception('Error fetching FAQs')
        return jsonify({'error': str(e)}), 500


# ==================== PUBLIC AI CHAT WIDGET ====================

def detect_contact_details(message):
    """Detect if user is providing contact details in their message.
    Only returns contact info when there's actual email/phone AND an explicit name introduction.
    """
    import re

    original_message = message
    message = message.lower().strip()
    
    # Common greetings and short phrases to ignore - these are NOT contact details
    ignore_phrases = [
        'hi', 'hello', 'hey', 'hiya', 'howdy', 'sup', 'yo', 'greetings',
        'good morning', 'good afternoon', 'good evening', 'good day',
        'thanks', 'thank you', 'ok', 'okay', 'yes', 'no', 'sure', 'bye',
        'goodbye', 'see you', 'later', 'help', 'please', 'what', 'how',
        'when', 'where', 'why', 'who', 'can you', 'could you', 'would you'
    ]
    
    # If message is just a greeting or common phrase, skip detection
    if message in ignore_phrases or len(message) < 4:
        return None
    
    # Check if message starts with common greeting patterns
    for phrase in ignore_phrases:
        if message == phrase or message.startswith(phrase + ' ') or message.startswith(phrase + ','):
            # Only continue if there's substantial content after the greeting
            if len(message) < 15:
                return None

    # Look for contact patterns FIRST (email or phone) - these are required
    contact = None

    # Email pattern - must have valid email format
    email_match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', original_message)
    if email_match:
        contact = email_match.group(0)
    else:
        # Phone pattern - must be a valid phone number format
        phone_match = re.search(r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b', message)
        if phone_match:
            contact = phone_match.group(0)

    # Only look for name if we found actual contact info (email or phone)
    name = None
    if contact:
        # Look for explicit name patterns only
        name_patterns = [
            r'(?:my name is|i\'m|i am|this is|call me)\s+([a-zA-Z][a-zA-Z\s]{1,25}?)(?:\s*[,.]|\s+and|\s+my|\s+email|\s+phone|\s+at|\s*$)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                potential_name = match.group(1).strip()
                # Validate it's a reasonable name (not a greeting or common word)
                if potential_name.lower() not in ignore_phrases and len(potential_name) > 1:
                    name = potential_name.title()
                    break

    # Only return if we found actual contact information (email or phone)
    if contact:
        return {'name': name, 'contact': contact}
    return None


@app.route('/api/chat/init', methods=['POST'])
def init_chat_session():
    """Initialize a new chat session and return persona info."""
    try:
        ensure_chat_tables()
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Get AI persona settings
        cursor.execute("SELECT * FROM ai_persona WHERE id = 1")
        persona = cursor.fetchone()
        
        # Check if AI is enabled
        cursor.execute("SELECT is_enabled, api_key FROM ai_settings WHERE id = 1")
        ai_settings = cursor.fetchone()
        
        if not ai_settings or not ai_settings.get('is_enabled') or not ai_settings.get('api_key'):
            cursor.close()
            conn.close()
            return jsonify({'error': 'Chat is currently unavailable'}), 503
        
        # Create new session
        session_id = str(uuid4())
        visitor_ip = request.remote_addr
        user_agent = request.headers.get('User-Agent', '')[:500]
        
        cursor.execute("""
            INSERT INTO chat_sessions (session_id, visitor_ip, user_agent)
            VALUES (%s, %s, %s)
        """, (session_id, visitor_ip, user_agent))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'session_id': session_id,
            'persona': {
                'name': persona.get('persona_name', 'Assistant') if persona else 'Assistant',
                'greeting': persona.get('greeting_message', 'Hello! How can I help you today?') if persona else 'Hello! How can I help you today?',
                'avatar': persona.get('avatar_url') if persona else None
            }
        })
        
    except Exception as e:
        app.logger.exception('Error initializing chat')
        return jsonify({'error': 'Failed to start chat'}), 500


@app.route('/api/chat/message', methods=['POST'])
def send_chat_message():
    """Send a message and get AI response."""
    conn = None
    cursor = None
    try:
        ensure_chat_tables()
        data = request.get_json() or {}
        session_id = data.get('session_id', '').strip()
        user_message = data.get('message', '').strip()

        if not session_id or not user_message:
            return jsonify({'error': 'Missing session_id or message'}), 400

        if len(user_message) > 2000:
            return jsonify({'error': 'Message too long (max 2000 characters)'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Verify session exists
        cursor.execute("SELECT id FROM chat_sessions WHERE session_id = %s", (session_id,))
        session_row = cursor.fetchone()
        if not session_row:
            return jsonify({'error': 'Invalid session'}), 400

        # Get AI settings
        cursor.execute("SELECT * FROM ai_settings WHERE id = 1")
        ai_settings = cursor.fetchone()

        if not ai_settings or not ai_settings.get('is_enabled') or not ai_settings.get('api_key'):
            return jsonify({'error': 'Chat is currently unavailable'}), 503

        # Get persona
        cursor.execute("SELECT * FROM ai_persona WHERE id = 1")
        persona = cursor.fetchone() or {}

        # Save user message
        cursor.execute("""
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (%s, %s, %s)
        """, (session_id, 'user', user_message))

        # Update session
        cursor.execute("""
            UPDATE chat_sessions
            SET message_count = message_count + 1, last_message_at = NOW()
            WHERE session_id = %s
        """, (session_id,))

        # Build knowledge context (use separate connection to avoid transaction issues)
        knowledge_context = ""
        try:
            knowledge_context = build_chat_knowledge_context_separate()
        except Exception as e:
            app.logger.warning(f"Failed to build knowledge context: {e}")
            knowledge_context = "Knowledge base temporarily unavailable."

        # Get conversation history (last 10 messages for context)
        cursor.execute("""
            SELECT role, content FROM chat_messages
            WHERE session_id = %s
            ORDER BY created_at DESC LIMIT 10
        """, (session_id,))
        history = cursor.fetchall()[::-1]  # Reverse to get chronological order

        # Generate AI response (this can fail, so we handle it separately)
        ai_response = generate_chat_response(
            user_message,
            history,
            knowledge_context,
            persona,
            ai_settings
        )

        # Check if user is providing contact details (only triggers when actual email/phone is detected)
        user_providing_details = detect_contact_details(user_message)
        if user_providing_details and user_providing_details.get('contact'):
            name = user_providing_details.get('name')
            contact = user_providing_details.get('contact')
            if name or contact:
                # Update session with user details
                update_fields = []
                update_values = []
                if name:
                    update_fields.append("visitor_name = %s")
                    update_values.append(name)
                if contact:
                    # Determine if it's email or phone
                    if '@' in contact:
                        update_fields.append("visitor_email = %s")
                        update_values.append(contact)
                    else:
                        # For now, store in visitor_email field (we can distinguish later)
                        update_fields.append("visitor_email = %s")
                        update_values.append(contact)
                
                if update_fields:
                    update_values.append(session_id)
                    cursor.execute(f"""
                        UPDATE chat_sessions 
                        SET {', '.join(update_fields)}
                        WHERE session_id = %s
                    """, update_values)
                    
                    ai_response = f"Thank you for providing your information{f' {name}' if name else ''}! A representative will contact you within 24 hours. You can also reach us directly:\n\n📧 Email: {persona.get('contact_email', 'support@sparkleclean.com')}\n📱 WhatsApp: {persona.get('whatsapp_number', '+1-800-SPLK-CLEAN')}\n📞 Phone: {persona.get('contact_phone', '1-800-SPLK-CLEAN')}"

        # Check if user seems unsatisfied and we should collect details
        user_wants_followup = any(keyword in user_message.lower() for keyword in [
            'contact', 'call me', 'email me', 'reach out', 'representative', 'speak to someone',
            'not helpful', 'not satisfied', 'frustrated', 'disappointed', 'need help',
            'talk to person', 'human', 'manager', 'supervisor'
        ])

        if user_wants_followup and len(history) > 2:  # Only after some conversation
            # Check if we already have their details
            cursor.execute("SELECT visitor_name, visitor_email FROM chat_sessions WHERE session_id = %s", (session_id,))
            session_data = cursor.fetchone()
            if not session_data or not session_data.get('visitor_name'):
                # Collect user details
                ai_response += "\n\nI'd be happy to have one of our representatives follow up with you personally. May I get your name and contact information?"

        # Save AI response
        cursor.execute("""
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (%s, %s, %s)
        """, (session_id, 'assistant', ai_response))

        cursor.execute("""
            UPDATE chat_sessions
            SET message_count = message_count + 1, last_message_at = NOW()
            WHERE session_id = %s
        """, (session_id,))

        # Commit all changes
        conn.commit()

        return jsonify({
            'response': ai_response,
            'persona_name': persona.get('persona_name', 'Assistant')
        })

    except Exception as e:
        # Rollback on any error
        if conn:
            try:
                conn.rollback()
            except:
                pass
        app.logger.exception('Error processing chat message')
        return jsonify({'error': 'Failed to process message'}), 500
    finally:
        # Always close cursor and connection
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass


def build_chat_knowledge_context_separate():
    """Build knowledge context from FAQs, services, and knowledge base using separate connection."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        if engine == 'postgres':
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cursor = conn.cursor(dictionary=True)
        return build_chat_knowledge_context(cursor)
    finally:
        if cursor:
            try:
                cursor.close()
            except:
                pass
        if conn:
            try:
                conn.close()
            except:
                pass


def build_chat_knowledge_context(cursor):
    """Build knowledge context from FAQs, services, and knowledge base."""
    context_parts = []
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    # Get FAQs
    try:
        if engine == 'postgres':
            cursor.execute("SELECT question, answer, category FROM faqs WHERE is_active = TRUE ORDER BY sort_order LIMIT 20")
        else:
            cursor.execute("SELECT question, answer, category FROM faqs WHERE is_active = 1 ORDER BY sort_order LIMIT 20")
        faqs = cursor.fetchall()
        if faqs:
            faq_text = "\n\nFREQUENTLY ASKED QUESTIONS:\n"
            for faq in faqs:
                faq_text += f"Q: {faq['question']}\nA: {faq['answer']}\n\n"
            context_parts.append(faq_text)
    except Exception as e:
        app.logger.warning(f"Failed to load FAQs for chat: {e}")
    
    # Get services
    try:
        if engine == 'postgres':
            # services.is_active is smallint in this db, so use = 1
            cursor.execute("SELECT name, title, description, short_description, price FROM services WHERE is_active = 1 LIMIT 20")
        else:
            cursor.execute("SELECT name, title, description, short_description, price FROM services WHERE is_active = 1 LIMIT 20")
        services = cursor.fetchall()
        if services:
            svc_text = "\n\nOUR CLEANING SERVICES:\n"
            for svc in services:
                svc_name = svc.get('name') or svc.get('title') or 'Service'
                svc_desc = svc.get('short_description') or svc.get('description') or 'Professional cleaning service'
                svc_price = svc.get('price')
                price_str = f"Starting at ${svc_price}" if svc_price else "Contact for pricing"
                svc_text += f"- {svc_name}: {svc_desc} ({price_str})\n"
            context_parts.append(svc_text)
    except Exception as e:
        app.logger.warning(f"Failed to load services for chat: {e}")
    
    # Get knowledge base entries - use correct boolean for postgres
    try:
        if engine == 'postgres':
            cursor.execute("SELECT title, content FROM ai_knowledge_base WHERE is_active = TRUE LIMIT 10")
        else:
            cursor.execute("SELECT title, content FROM ai_knowledge_base WHERE is_active = 1 LIMIT 10")
        kb_entries = cursor.fetchall()
        if kb_entries:
            kb_text = "\n\nADDITIONAL INFORMATION:\n"
            for entry in kb_entries:
                kb_text += f"{entry['title']}: {entry['content']}\n\n"
            context_parts.append(kb_text)
    except Exception as e:
        app.logger.warning(f"Failed to load knowledge base for chat: {e}")
    
    # Get company and contact info
    company_name = 'Our Company'
    phone = 'Contact us'
    email = 'Contact us'
    location = ''
    whatsapp = None

    # Company name from latest site settings entry
    try:
        cursor.execute("SELECT company_name FROM site_settings ORDER BY id DESC LIMIT 1")
        settings = cursor.fetchone()
        if settings:
            company_name = settings.get('company_name') or company_name
    except Exception as e:
        app.logger.warning(f"Failed to load site settings for chat: {e}")

    # Contact info from footer_info admin settings
    try:
        cursor.execute("SELECT phone, email, location FROM footer_info WHERE id = 1")
        footer = cursor.fetchone()
        if footer:
            phone = footer.get('phone') or phone
            email = footer.get('email') or email
            location = footer.get('location') or location
    except Exception as e:
        app.logger.warning(f"Failed to load footer contact info for chat: {e}")

    whatsapp = whatsapp or phone or 'Contact us'

    info_text = f"""

COMPANY CONTACT INFORMATION:
Company Name: {company_name}
Phone: {phone}
Email: {email}
WhatsApp: {whatsapp}
Location: {location}"""
    context_parts.append(info_text)
    
    return "\n".join(context_parts)


def generate_chat_response(user_message, history, knowledge_context, persona, ai_settings):
    """Generate AI response using the configured provider."""
    try:
        provider = ai_settings.get('ai_provider', 'groq')
        api_key = ai_settings.get('api_key', '')
        model = ai_settings.get('model', 'llama-3.3-70b-versatile')
        
        persona_name = persona.get('persona_name', 'Assistant')
        persona_desc = persona.get('persona_description', 'A helpful assistant')
        personality = persona.get('personality_traits', 'Friendly, Professional')
        response_style = persona.get('response_style', 'friendly')
        
        # Build system prompt
        system_prompt = f"""You are {persona_name}, {persona_desc}

Your personality traits: {personality}
Response style: {response_style}

IMPORTANT GUIDELINES:
1. ALWAYS use the KNOWLEDGE BASE information below to answer questions - this contains your real services, pricing, FAQs, and company details
2. When asked about services, list the ACTUAL services from the knowledge base with their real prices
3. When asked for contact info, use the COMPANY CONTACT INFORMATION from the knowledge base - NOT placeholder data
4. If a user asks about something that is not covered by the knowledge base or unrelated to the cleaning business, politely explain that you can only discuss the services and information shown on the site
5. Be helpful, friendly, and professional
6. Keep responses concise but informative (2-4 sentences usually)
7. If someone wants to book a service, guide them to the booking form on the website or provide the contact details
8. Never make up services or prices - only mention what's in the knowledge base
9. If the user seems unsatisfied or needs more help, offer to collect their contact details for a representative to follow up
10. When collecting details, ask for: name, email/phone, and their specific question or request
11. After collecting details, provide the REAL company contact information from the knowledge base
12. Be conversational and warm, not robotic

FORMATTING RULES (VERY IMPORTANT):
- NEVER use markdown - no tables, no **bold**, no *italics*, no headers
- Use PLAIN TEXT only - the chat widget does not render markdown
- For service lists, use simple bullet points: "• Service Name - $XX"
- Put descriptions on a new line under the service name if needed
- Keep formatting simple and chat-friendly
- Use line breaks between items for readability
- Emojis are okay sparingly (✨, 🏠, 📞)

KNOWLEDGE BASE (USE THIS DATA FOR ALL ANSWERS):
{knowledge_context}
"""
        
        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history
        for msg in history[:-1]:  # Exclude the current message
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })
        
        # Add current user message
        messages.append({"role": "user", "content": user_message})
        
        # Call appropriate API
        if provider == 'groq':
            response = call_groq_api(api_key, model, messages)
        elif provider == 'openai':
            response = call_openai_api(api_key, model, messages)
        elif provider == 'anthropic':
            response = call_anthropic_api(api_key, model, messages, system_prompt)
        else:
            response = "I'm sorry, I'm having trouble connecting right now. Please try again later or contact us directly."
        
        return response
        
    except Exception as e:
        app.logger.exception(f"Error generating chat response: {e}")
        return "I apologize, but I'm having some technical difficulties. Please try again in a moment, or feel free to contact us directly using the information on our website."


def call_groq_api(api_key, model, messages):
    """Call Groq API for chat completion."""
    try:
        response = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': model,
                'messages': messages,
                'max_tokens': 500,
                'temperature': 0.7
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        app.logger.error(f"Groq API error: {e}")
        raise


def call_openai_api(api_key, model, messages):
    """Call OpenAI API for chat completion."""
    try:
        response = requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': model,
                'messages': messages,
                'max_tokens': 500,
                'temperature': 0.7
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        app.logger.error(f"OpenAI API error: {e}")
        raise


def call_anthropic_api(api_key, model, messages, system_prompt):
    """Call Anthropic API for chat completion."""
    try:
        # Convert messages for Anthropic format (no system in messages)
        anthropic_messages = [m for m in messages if m['role'] != 'system']
        
        response = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers={
                'x-api-key': api_key,
                'Content-Type': 'application/json',
                'anthropic-version': '2023-06-01'
            },
            json={
                'model': model,
                'system': system_prompt,
                'messages': anthropic_messages,
                'max_tokens': 500
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()['content'][0]['text']
    except Exception as e:
        app.logger.error(f"Anthropic API error: {e}")
        raise


@app.route('/api/testimonials/submit', methods=['POST'])
def submit_testimonial():
    """Public endpoint for customers to submit testimonials.
    Auto-approves if name matches an existing customer in requests table.
    """
    data = request.json
    name = (data.get('name') or '').strip()
    message = (data.get('message') or '').strip()
    rating = data.get('rating', 5)
    email = (data.get('email') or '').strip() or None
    
    # Validate required fields
    if not name:
        return jsonify({'error': 'Please enter your name.'}), 400
    if not message:
        return jsonify({'error': 'Please write a review.'}), 400
    if len(message) < 10:
        return jsonify({'error': 'Please write a longer review (at least 10 characters).'}), 400
    
    # Validate rating
    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            rating = 5
    except (ValueError, TypeError):
        rating = 5
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # Check if this name exists in our requests table (existing customer)
        # Use case-insensitive comparison and look for exact or partial match
        cursor.execute("""
            SELECT DISTINCT name 
            FROM requests 
            WHERE LOWER(TRIM(name)) = LOWER(%s)
            OR LOWER(TRIM(name)) LIKE LOWER(%s)
            LIMIT 1
        """, (name, f'%{name}%'))
        
        existing_customer = cursor.fetchone()
        is_verified = existing_customer is not None
        status = 'approved' if is_verified else 'pending'
        
        # Insert the testimonial
        cursor.execute("""
            INSERT INTO testimonials (name, message, rating, email, status, is_verified_customer, image_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (name, message, rating, email, status, is_verified, ''))
        
        testimonial_id = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()
        
        if is_verified:
            return jsonify({
                'message': 'Thank you! Your review has been published.',
                'status': 'approved',
                'is_verified': True,
                'id': testimonial_id
            }), 201
        else:
            return jsonify({
                'message': 'Thank you! Your review will be published after verification.',
                'status': 'pending',
                'is_verified': False,
                'id': testimonial_id
            }), 201
            
    except Exception as e:
        app.logger.error(f'Error submitting testimonial: {e}')
        return jsonify({'error': 'Unable to submit review. Please try again.'}), 500


@app.route('/api/testimonials/add', methods=['POST'])
def add_testimonial():
    data = request.json
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = "INSERT INTO testimonials (name, message, image_url) VALUES (%s, %s, %s)"
        cursor.execute(query, (data['name'], data['message'], data.get('image_url', '')))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Testimonial added!'}), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/testimonials/edit/<int:id>', methods=['PUT'])
def edit_testimonial(id):
    data = request.json
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = "UPDATE testimonials SET name=%s, message=%s, image_url=%s WHERE id=%s"
        cursor.execute(query, (data['name'], data['message'], data.get('image_url', ''), id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Testimonial updated!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/testimonials/delete/<int:id>', methods=['DELETE'])
def delete_testimonial(id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM testimonials WHERE id=%s", (id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Testimonial deleted!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/testimonials/approve/<int:id>', methods=['POST'])
def approve_testimonial(id):
    """Approve a pending testimonial."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE testimonials SET status = 'approved' WHERE id = %s", (id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Testimonial approved!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/testimonials/reject/<int:id>', methods=['POST'])
def reject_testimonial(id):
    """Reject a pending testimonial (deletes it)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM testimonials WHERE id = %s", (id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Testimonial rejected and removed.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analytics/event', methods=['POST'])
def create_analytics_event():
    data = request.get_json(silent=True) or {}
    event_type = (data.get('event_type') or '').strip()
    if not event_type:
        return jsonify({'error': 'event_type is required'}), 400
    event_payload = data.get('event_data')
    log_analytics_event(event_type, event_payload)
    return jsonify({'message': 'Event recorded'}), 201


@app.route('/admin/api/dashboard/live-stats', methods=['GET'])
@admin_login_required
def admin_dashboard_live_stats():
    """Lightweight endpoint for live dashboard polling - returns status counts and recent requests"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        
        # Get status counts
        cursor.execute("""
            SELECT 
                status,
                COUNT(*) as count
            FROM requests
            GROUP BY status
        """)
        status_rows = cursor.fetchall()
        counts = {row['status']: row['count'] for row in status_rows}
        
        # Ensure all statuses are present
        for status in ['pending', 'in_progress', 'completed', 'cancelled', 'survey_needed']:
            if status not in counts:
                counts[status] = 0
        
        # Get total
        counts['total'] = sum(counts.values())
        
        # Get recent requests (top 15)
        cursor.execute("""
            SELECT 
                r.id,
                r.ref_id,
                r.name,
                r.email,
                r.request_type,
                r.service_name,
                r.job_position,
                r.status,
                r.created_at,
                r.updated_at,
                sr.total_price
            FROM requests r
            LEFT JOIN service_requests sr ON r.id = sr.legacy_request_id
            ORDER BY r.created_at DESC
            LIMIT 15
        """)
        recent_rows = cursor.fetchall()
        
        recent_requests = []
        for row in recent_rows:
            recent_requests.append({
                'id': row['id'],
                'ref_id': row['ref_id'],
                'name': row['name'],
                'email': row['email'],
                'request_type': row['request_type'],
                'service_name': row['service_name'] or row['job_position'] or 'General',
                'status': row['status'],
                'price': float(row['total_price']) if row['total_price'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None
            })
        
        # Visitor count (analytics events in last 10 minutes)
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COUNT(*) as visitors
                FROM analytics
                WHERE created_at >= NOW() - INTERVAL '10 minutes'
                """
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as visitors
                FROM analytics
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL 10 MINUTE)
                """
            )
        visitor_row = cursor.fetchone()
        visitor_count = visitor_row['visitors'] if visitor_row else 0
        
        # Today's new requests count
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COUNT(*) as today_count
                FROM requests
                WHERE DATE(created_at) = CURRENT_DATE
                """
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as today_count
                FROM requests
                WHERE DATE(created_at) = CURDATE()
                """
            )
        today_row = cursor.fetchone()
        today_count = today_row['today_count'] if today_row else 0
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'counts': counts,
            'recent_requests': recent_requests,
            'visitor_count': visitor_count,
            'today_count': today_count,
            'last_updated': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/analytics/live', methods=['GET'])
@admin_login_required
def admin_analytics_live():
    """Lightweight endpoint for live analytics polling"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        
        # Get period from query param (default 30 days)
        days = request.args.get('days', 30, type=int)
        
        # Count analytics events
        def count_event(event_type, period_days=None):
            if period_days:
                if engine == 'postgres':
                    cursor.execute(
                        "SELECT COUNT(*) as cnt FROM analytics WHERE event_type = %s AND created_at >= NOW() - (%s * INTERVAL '1 day')",
                        (event_type, period_days)
                    )
                else:
                    cursor.execute(
                        "SELECT COUNT(*) as cnt FROM analytics WHERE event_type = %s AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)",
                        (event_type, period_days)
                    )
            else:
                if engine == 'postgres':
                    cursor.execute(
                        "SELECT COUNT(*) as cnt FROM analytics WHERE event_type = %s AND DATE(created_at) = CURRENT_DATE",
                        (event_type,)
                    )
                else:
                    cursor.execute(
                        "SELECT COUNT(*) as cnt FROM analytics WHERE event_type = %s AND DATE(created_at) = CURDATE()",
                        (event_type,)
                    )
            return cursor.fetchone()['cnt']
        
        # Period totals from analytics
        visits = count_event('homepage_visit', days)
        service_views = count_event('service_view', days)
        
        # Bookings/jobs/contacts from requests table (matching original endpoint)
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM requests
                WHERE request_type = 'service'
                AND created_at >= NOW() - (%s * INTERVAL '1 day')
                """,
                (days,)
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM requests 
                WHERE request_type = 'service' 
                AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                """,
                (days,)
            )
        bookings = cursor.fetchone()['cnt']
        
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM requests
                WHERE request_type = 'job'
                AND created_at >= NOW() - (%s * INTERVAL '1 day')
                """,
                (days,)
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM requests 
                WHERE request_type = 'job' 
                AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                """,
                (days,)
            )
        job_applications = cursor.fetchone()['cnt']
        
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM requests
                WHERE request_type = 'general'
                AND created_at >= NOW() - (%s * INTERVAL '1 day')
                """,
                (days,)
            )
        else:
            cursor.execute(
                """
                SELECT COUNT(*) as cnt FROM requests 
                WHERE request_type = 'general' 
                AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                """,
                (days,)
            )
        contact_submissions = cursor.fetchone()['cnt']
        
        # Today totals
        today_visits = count_event('homepage_visit')
        today_service_views = count_event('service_view')
        
        if engine == 'postgres':
            cursor.execute("SELECT COUNT(*) as cnt FROM requests WHERE request_type = 'service' AND DATE(created_at) = CURRENT_DATE")
        else:
            cursor.execute("SELECT COUNT(*) as cnt FROM requests WHERE request_type = 'service' AND DATE(created_at) = CURDATE()")
        today_bookings = cursor.fetchone()['cnt']
        
        # Conversion rate (visits to bookings)
        conversion_rate = round((bookings / visits * 100), 1) if visits > 0 else 0
        
        # Revenue data
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                    AND status = 'completed'
                """,
                (days,)
            )
        else:
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    AND status = 'completed'
                """,
                (days,)
            )
        period_revenue = float(cursor.fetchone()['total'] or 0)
        
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE DATE(created_at) = CURRENT_DATE
                    AND status = 'completed'
                """
            )
        else:
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE DATE(created_at) = CURDATE()
                    AND status = 'completed'
                """
            )
        today_revenue = float(cursor.fetchone()['total'] or 0)
        
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE created_at >= NOW() - (7 * INTERVAL '1 day')
                    AND status = 'completed'
                """
            )
        else:
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                    AND status = 'completed'
                """
            )
        week_revenue = float(cursor.fetchone()['total'] or 0)
        
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE created_at >= date_trunc('month', NOW())
                    AND created_at < date_trunc('month', NOW()) + INTERVAL '1 month'
                    AND status = 'completed'
                """
            )
        else:
            cursor.execute(
                """
                SELECT COALESCE(SUM(total_price), 0) as total
                FROM service_requests
                WHERE MONTH(created_at) = MONTH(CURDATE()) AND YEAR(created_at) = YEAR(CURDATE())
                    AND status = 'completed'
                """
            )
        month_revenue = float(cursor.fetchone()['total'] or 0)
        
        # Request status counts for funnel
        if engine == 'postgres':
            cursor.execute(
                """
                SELECT status, COUNT(*) as count
                FROM requests
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                GROUP BY status
                """,
                (days,)
            )
        else:
            cursor.execute(
                """
                SELECT status, COUNT(*) as count
                FROM requests
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                GROUP BY status
                """,
                (days,)
            )
        status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'period': {
                'visits': visits,
                'service_views': service_views,
                'bookings': bookings,
                'job_applications': job_applications,
                'contact_submissions': contact_submissions,
                'conversion_rate': conversion_rate
            },
            'today': {
                'visits': today_visits,
                'service_views': today_service_views,
                'bookings': today_bookings
            },
            'revenue': {
                'period': period_revenue,
                'today': today_revenue,
                'week': week_revenue,
                'month': month_revenue
            },
            'funnel': {
                'visits': visits,
                'views': service_views,
                'requests': bookings + job_applications + contact_submissions,
                'confirmed': status_counts.get('in_progress', 0) + status_counts.get('completed', 0),
                'completed': status_counts.get('completed', 0)
            },
            'last_updated': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/analytics/kpis', methods=['GET'])
@admin_login_required
def admin_analytics_kpis():
    """Business KPIs: active contracts, all-time revenue, avg booking, pending quotes, due this week."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

        # Active contracts
        cursor.execute("SELECT COUNT(*) AS cnt FROM contracts WHERE status = 'active'")
        active_contracts = (cursor.fetchone() or {}).get('cnt') or 0

        # All-time paid revenue
        cursor.execute("SELECT COALESCE(SUM(total_price), 0) AS total FROM service_requests WHERE is_paid = TRUE")
        total_revenue = float((cursor.fetchone() or {}).get('total') or 0)

        # Average booking value (paid, non-zero)
        cursor.execute("SELECT COALESCE(AVG(total_price), 0) AS avg_val FROM service_requests WHERE is_paid = TRUE AND total_price > 0")
        avg_booking = float((cursor.fetchone() or {}).get('avg_val') or 0)

        # Pending quotes
        cursor.execute("SELECT COUNT(*) AS cnt FROM service_requests WHERE status IN ('pending', 'quote_ready')")
        pending_quotes = (cursor.fetchone() or {}).get('cnt') or 0

        # Contracts due this week
        if engine == 'postgres':
            cursor.execute("SELECT COUNT(*) AS cnt FROM contracts WHERE next_service_date BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '7 days'")
        else:
            cursor.execute("SELECT COUNT(*) AS cnt FROM contracts WHERE next_service_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)")
        due_week = (cursor.fetchone() or {}).get('cnt') or 0

        cursor.close()
        conn.close()

        return jsonify({
            'active_contracts': int(active_contracts),
            'total_revenue_alltime': round(total_revenue, 2),
            'avg_booking_value': round(avg_booking, 2),
            'pending_quotes': int(pending_quotes),
            'contracts_due_week': int(due_week),
        })
    except Exception as e:
        app.logger.exception('Error in admin_analytics_kpis')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/analytics/ai-summary', methods=['POST'])
@admin_login_required
def admin_analytics_ai_summary():
    """Generate a plain-English AI summary of current analytics data."""
    try:
        days = 30
        if request.is_json:
            days = int(request.json.get('days', 30))
        days = max(1, min(90, days))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

        # AI settings
        cursor.execute("SELECT * FROM ai_settings WHERE id = 1")
        ai_settings = cursor.fetchone() or {}
        api_key = (ai_settings.get('api_key') or '').strip()
        model = (ai_settings.get('model') or 'openai/gpt-oss-20b').strip()

        if not api_key:
            cursor.close()
            conn.close()
            return jsonify({'error': 'AI not configured. Add an API key in AI settings.'}), 503

        # Gather snapshot data
        if engine == 'postgres':
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM analytics WHERE event_type = 'homepage_visit' AND created_at >= NOW() - INTERVAL '{days} days'")
        else:
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM analytics WHERE event_type = 'homepage_visit' AND created_at >= DATE_SUB(NOW(), INTERVAL {days} DAY)")
        visits = (cursor.fetchone() or {}).get('cnt') or 0

        if engine == 'postgres':
            cursor.execute(f"SELECT COUNT(*) AS cnt, COALESCE(SUM(total_price),0) AS rev FROM service_requests WHERE created_at >= NOW() - INTERVAL '{days} days'")
        else:
            cursor.execute(f"SELECT COUNT(*) AS cnt, COALESCE(SUM(total_price),0) AS rev FROM service_requests WHERE created_at >= DATE_SUB(NOW(), INTERVAL {days} DAY)")
        row = cursor.fetchone() or {}
        bookings = int(row.get('cnt') or 0)
        period_revenue = float(row.get('rev') or 0)

        cursor.execute("SELECT COALESCE(SUM(total_price), 0) AS total FROM service_requests WHERE is_paid = TRUE")
        alltime_revenue = float((cursor.fetchone() or {}).get('total') or 0)

        cursor.execute("SELECT COUNT(*) AS cnt FROM contracts WHERE status = 'active'")
        active_contracts = int((cursor.fetchone() or {}).get('cnt') or 0)

        cursor.execute("SELECT COUNT(*) AS cnt FROM service_requests WHERE status IN ('pending', 'quote_ready')")
        pending_quotes = int((cursor.fetchone() or {}).get('cnt') or 0)

        cursor.execute("SELECT service_name, COUNT(*) AS cnt FROM requests WHERE service_name IS NOT NULL GROUP BY service_name ORDER BY cnt DESC LIMIT 1")
        top_row = cursor.fetchone()
        top_service = (top_row.get('service_name') or 'Unknown') if top_row else 'Unknown'

        conversion_rate = round((bookings / visits * 100), 1) if visits > 0 else 0

        cursor.close()
        conn.close()

        snapshot = {
            'period_days': days,
            'visits': int(visits),
            'bookings': bookings,
            'period_revenue_gbp': round(period_revenue, 2),
            'alltime_revenue_gbp': round(alltime_revenue, 2),
            'active_contracts': active_contracts,
            'pending_quotes': pending_quotes,
            'top_service': top_service,
            'conversion_rate_pct': conversion_rate,
        }

        prompt = (
            f"You are a business analyst for Done-Well Cleaning Limited, a UK cleaning company. "
            f"Write a 3-4 sentence plain-English summary for the business owner based on this {days}-day analytics snapshot. "
            f"Be specific with numbers. Mention what's going well and any areas of concern. "
            f"Data: {json.dumps(snapshot)}\n\nReply with the summary only, no headings or bullet points."
        )

        summary = call_groq_api(api_key, model, [{"role": "user", "content": prompt}])

        return jsonify({
            'summary': summary,
            'snapshot': snapshot,
            'generated_at': datetime.utcnow().isoformat() + 'Z',
        })

    except Exception as e:
        app.logger.exception('Error in admin_analytics_ai_summary')
        return jsonify({'error': str(e)}), 500


@app.route('/admin/api/analytics/summary', methods=['GET'])
@admin_login_required
def analytics_summary():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    def count_analytics_event(event_type):
        cursor.execute("SELECT COUNT(*) AS total FROM analytics WHERE event_type = %s", (event_type,))
        return cursor.fetchone()['total']

    def count_analytics_event_today(event_type):
        if engine == 'postgres':
            cursor.execute(
                "SELECT COUNT(*) AS total FROM analytics WHERE event_type = %s AND DATE(created_at) = CURRENT_DATE",
                (event_type,)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM analytics WHERE event_type = %s AND DATE(created_at) = CURDATE()",
                (event_type,)
            )
        return cursor.fetchone()['total']

    visits = count_analytics_event('homepage_visit')
    service_views = count_analytics_event('service_view')

    cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'job'")
    job_applications_count = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'service'")
    bookings_count = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'general'")
    contact_submissions = cursor.fetchone()['total']

    cursor.execute(
        """
        SELECT service_name AS label, COUNT(*) AS total
        FROM requests
        WHERE request_type = 'service' AND service_name IS NOT NULL AND service_name <> ''
        GROUP BY service_name
        ORDER BY total DESC
        LIMIT 1
        """
    )
    most_viewed_row = cursor.fetchone()
    most_viewed_service = most_viewed_row['label'] if most_viewed_row else None

    if engine == 'postgres':
        cursor.execute(
            """
            SELECT EXTRACT(HOUR FROM created_at) AS hour, COUNT(*) AS total
            FROM analytics
            GROUP BY EXTRACT(HOUR FROM created_at)
            ORDER BY total DESC
            LIMIT 1
            """
        )
    else:
        cursor.execute(
            """
            SELECT HOUR(created_at) AS hour, COUNT(*) AS total
            FROM analytics
            GROUP BY HOUR(created_at)
            ORDER BY total DESC
            LIMIT 1
            """
        )
    peak_row = cursor.fetchone()
    peak_hour = peak_row['hour'] if peak_row else None
    peak_traffic_time = f"{int(peak_hour):02d}:00" if peak_hour is not None else None

    today = {
        'visits': count_analytics_event_today('homepage_visit'),
        'contact_submissions': count_analytics_event_today('contact_form'),
        'service_views': count_analytics_event_today('service_view'),
    }

    if engine == 'postgres':
        cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'job' AND DATE(created_at) = CURRENT_DATE")
    else:
        cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'job' AND DATE(created_at) = CURDATE()")
    today['job_applications'] = cursor.fetchone()['total']

    if engine == 'postgres':
        cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'service' AND DATE(created_at) = CURRENT_DATE")
    else:
        cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'service' AND DATE(created_at) = CURDATE()")
    today['bookings'] = cursor.fetchone()['total']

    if engine == 'postgres':
        cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'general' AND DATE(created_at) = CURRENT_DATE")
    else:
        cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE request_type = 'general' AND DATE(created_at) = CURDATE()")
    today['contact_submissions'] = cursor.fetchone()['total']

    cursor.execute(
        "SELECT event_type, created_at FROM analytics ORDER BY created_at DESC LIMIT 5"
    )
    recent_events = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({
        'visits': visits,
        'service_views': service_views,
        'job_applications': job_applications_count,
        'bookings': bookings_count,
        'contact_submissions': contact_submissions,
        'most_viewed_service': most_viewed_service,
        'peak_traffic_time': peak_traffic_time,
        'today': today,
        'recent_events': recent_events
    })


@app.route('/admin/api/requests', methods=['GET'])
@admin_login_required
def admin_list_requests():
    ensure_travel_tables()
    ensure_faq_table()
    move_stale_stripe_pending_requests_to_draft()
    status_filter = (request.args.get('status') or '').strip().lower()
    request_type_filter = (request.args.get('request_type') or '').strip().lower()
    limit_param = request.args.get('limit', '100')

    try:
        limit = max(1, min(int(limit_param), 250))
    except (TypeError, ValueError):
        limit = 100

    filters = []
    params = []
    if status_filter and status_filter in REQUEST_STATUSES:
        filters.append('r.status = %s')
        params.append(status_filter)
    if request_type_filter in {'service', 'job', 'general'}:
        filters.append('r.request_type = %s')
        params.append(request_type_filter)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ''

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        f"""
           SELECT r.id, r.request_type, r.name, r.email, r.phone, r.subject, r.service_name, r.job_position,
               r.ref_id, r.status, r.source, r.created_at, r.email_sent_admin, r.email_sent_user,
               sr.total_price AS amount_due, sr.payment_type, sr.is_paid,
               CASE WHEN c.id IS NOT NULL THEN 1 ELSE 0 END AS is_contract,
               c.frequency AS contract_frequency
           FROM requests r
           LEFT JOIN service_requests sr ON sr.legacy_request_id = r.id
           LEFT JOIN contracts c ON c.request_id = r.id
        {where_clause}
           ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (*params, limit)
    )
    results = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in results:
        created_at = row.get('created_at')
        if isinstance(created_at, datetime):
            row['created_at'] = created_at.isoformat()
        row['email_sent_admin'] = bool(row.get('email_sent_admin'))
        row['email_sent_user'] = bool(row.get('email_sent_user'))
        row['is_contract'] = bool(row.get('is_contract'))
        if (row.get('request_type') or '').lower() == 'service':
            row['amount_due'] = normalize_price_value(row.get('amount_due'))
            source = (row.get('source') or '').lower()
            if row.get('payment_type') is None and 'stripe' in source:
                row['payment_type'] = 'stripe'
                row['is_paid'] = source == 'stripe_checkout_paid'
            else:
                row['payment_type'] = normalize_stored_payment_type(row.get('payment_type'))
                row['is_paid'] = bool(row.get('is_paid'))
            row['payment_status_label'] = resolve_payment_status_label(row.get('payment_type'), row.get('is_paid'))
            row['payment_badge'] = resolve_payment_badge(row.get('payment_type'), row.get('is_paid'))
        else:
            row['amount_due'] = None
            row['payment_type'] = ''
            row['is_paid'] = False
            row['payment_status_label'] = '—'
            row['payment_badge'] = 'na'

    return jsonify(results)


@app.route('/admin/api/requests/grouped', methods=['GET'])
@admin_login_required
def admin_list_requests_grouped():
    ensure_travel_tables()
    move_stale_stripe_pending_requests_to_draft()
    request_type_filter = (request.args.get('request_type') or '').strip().lower()
    limit_param = request.args.get('limit', '200')

    try:
        limit = max(1, min(int(limit_param), 500))
    except (TypeError, ValueError):
        limit = 200

    filters = []
    params = []

    if request_type_filter in {'service', 'job', 'general'}:
        filters.append('r.request_type = %s')
        params.append(request_type_filter)

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ''

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        f"""
         SELECT r.id, r.request_type, r.name, r.email, r.phone, r.subject, r.service_name, r.job_position,
             r.ref_id, r.status, r.source, r.created_at, r.email_sent_admin, r.email_sent_user,
             sr.total_price AS amount_due, sr.payment_type, sr.is_paid,
             CASE WHEN c.id IS NOT NULL THEN 1 ELSE 0 END AS is_contract,
             c.frequency AS contract_frequency
         FROM requests r
         LEFT JOIN service_requests sr ON sr.legacy_request_id = r.id
         LEFT JOIN contracts c ON c.request_id = r.id
        {where_clause}
         ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (*params, limit)
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    grouped = {
        'draft': [],
        'pending': [],
        'in_progress': [],
        'survey_needed': [],
        'completed_by_month': [],
        'cancelled_by_month': []
    }

    monthly_maps = {
        'completed': OrderedDict(),
        'cancelled': OrderedDict()
    }

    for row in rows:
        item = dict(row)
        created_at_value = item.get('created_at')
        dt_value = created_at_value if isinstance(created_at_value, datetime) else None
        if dt_value is None and created_at_value:
            try:
                dt_value = datetime.fromisoformat(str(created_at_value))
            except (ValueError, TypeError):
                dt_value = None

        if isinstance(created_at_value, datetime):
            item['created_at'] = created_at_value.isoformat()
        else:
            item['created_at'] = str(created_at_value) if created_at_value is not None else None

        item['email_sent_admin'] = bool(item.get('email_sent_admin'))
        item['email_sent_user'] = bool(item.get('email_sent_user'))
        item['is_contract'] = bool(item.get('is_contract'))
        if (item.get('request_type') or '').lower() == 'service':
            item['amount_due'] = normalize_price_value(item.get('amount_due'))
            # If no service_requests row yet but source shows stripe paid, reflect that
            source = (item.get('source') or '').lower()
            if item.get('payment_type') is None and 'stripe' in source:
                item['payment_type'] = 'stripe'
                item['is_paid'] = source == 'stripe_checkout_paid'
            else:
                item['payment_type'] = normalize_stored_payment_type(item.get('payment_type'))
                item['is_paid'] = bool(item.get('is_paid'))
            item['payment_status_label'] = resolve_payment_status_label(item.get('payment_type'), item.get('is_paid'))
            item['payment_badge'] = resolve_payment_badge(item.get('payment_type'), item.get('is_paid'))
        else:
            item['amount_due'] = None
            item['payment_type'] = ''
            item['is_paid'] = False
            item['payment_status_label'] = '—'
            item['payment_badge'] = 'na'

        status = (item.get('status') or 'pending').strip().lower()
        if status not in REQUEST_STATUSES:
            status = 'pending'

        if status in {'completed', 'cancelled'}:
            month_key = 'unknown'
            month_label = 'Unknown'
            if dt_value:
                month_key = dt_value.strftime('%Y-%m')
                month_label = f"{calendar.month_name[dt_value.month]} {dt_value.year}"

            monthly_map = monthly_maps['completed' if status == 'completed' else 'cancelled']
            if month_key not in monthly_map:
                monthly_map[month_key] = {
                    'key': month_key,
                    'label': month_label,
                    'items': []
                }
            monthly_map[month_key]['items'].append(item)
        else:
            grouped[status].append(item)

    grouped['completed_by_month'] = list(monthly_maps['completed'].values())
    grouped['cancelled_by_month'] = list(monthly_maps['cancelled'].values())

    summary_counts = {
        'draft': len(grouped['draft']),
        'pending': len(grouped['pending']),
        'in_progress': len(grouped['in_progress']),
        'survey_needed': len(grouped['survey_needed']),
        'completed': sum(len(group['items']) for group in grouped['completed_by_month']),
        'cancelled': sum(len(group['items']) for group in grouped['cancelled_by_month'])
    }

    response_payload = {
        'draft': grouped['draft'],
        'pending': grouped['pending'],
        'in_progress': grouped['in_progress'],
        'survey_needed': grouped['survey_needed'],
        'completed_by_month': grouped['completed_by_month'],
        'cancelled_by_month': grouped['cancelled_by_month'],
        'summary_counts': summary_counts
    }

    if request_type_filter in {'service', 'job', 'general'}:
        response_payload['filters'] = {'request_type': request_type_filter}

    return jsonify(response_payload)


@app.route('/admin/api/requests/<int:request_id>', methods=['GET', 'PATCH'])
@admin_login_required
def admin_request_detail(request_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM requests WHERE id = %s", (request_id,))
    request_row = cursor.fetchone()

    if not request_row:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Request not found.'}), 404

    original_status = request_row.get('status')
    previous_status = original_status
    status_changed = False
    total_cost_updated = False
    new_total_cost = None

    if request.method == 'PATCH':
        data = request.get_json(silent=True) or {}
        updates = []
        params = []

        if 'status' in data:
            new_status = (data.get('status') or '').strip().lower()
            if new_status not in REQUEST_STATUSES:
                cursor.close()
                conn.close()
                return jsonify({'error': 'Invalid status provided.'}), 400
            updates.append('status = %s')
            params.append(new_status)
            if new_status != original_status:
                status_changed = True
                previous_status = original_status

        if 'admin_notes' in data:
            notes = sanitize_text(data.get('admin_notes'))
            updates.append('admin_notes = %s')
            params.append(notes or None)

        # Handle total_cost update in service_requests table
        if 'total_cost' in data:
            raw_cost = data.get('total_cost')
            if raw_cost is not None:
                try:
                    new_total_cost = float(raw_cost)
                    if new_total_cost < 0:
                        new_total_cost = None
                except (ValueError, TypeError):
                    new_total_cost = None
            # Update service_requests table
            cursor.execute(
                "UPDATE service_requests SET total_price = %s WHERE legacy_request_id = %s",
                (new_total_cost, request_id)
            )
            if cursor.rowcount > 0:
                total_cost_updated = True

        if updates:
            updates.append('updated_at = CURRENT_TIMESTAMP')
            set_clause = ', '.join(updates)
            cursor.execute(f"UPDATE requests SET {set_clause} WHERE id = %s", (*params, request_id))
        conn.commit()

        cursor.execute("SELECT * FROM requests WHERE id = %s", (request_id,))
        request_row = cursor.fetchone()
        if not status_changed:
            # If status didn't change (e.g., same value was submitted) keep previous reference consistent
            previous_status = request_row.get('status') if request_row else previous_status

    cursor.execute(
        "SELECT id, original_filename, stored_path, mime_type, file_size_bytes, created_at FROM request_files WHERE request_id = %s",
        (request_id,)
    )
    attachments = cursor.fetchall()
    for attachment in attachments:
        created = attachment.get('created_at')
        if isinstance(created, datetime):
            attachment['created_at'] = created.isoformat()
        stored_path = (attachment.get('stored_path') or '').strip()
        if stored_path.startswith(('http://', 'https://')):
            attachment['remote_url'] = stored_path
            attachment['absolute_path'] = None
        else:
            normalized_path = os.path.normpath(stored_path).replace('\\', '/').lstrip('/')
            if normalized_path and not normalized_path.startswith('..'):
                absolute_path = safe_join(app.static_folder, normalized_path)
            else:
                absolute_path = None
            if absolute_path and os.path.isfile(absolute_path):
                attachment['absolute_path'] = absolute_path
            else:
                attachment['absolute_path'] = None
            attachment['remote_url'] = ''
    cursor.close()
    conn.close()

    status_notification = None
    quote_ready_notification = None
    if request.method == 'PATCH' and status_changed and request_row:
        # Check if transitioning from survey_needed to pending with price > 0
        new_status = request_row.get('status', '').lower()
        is_survey_to_pending = (
            previous_status == 'survey_needed' and
            new_status in ('pending', 'in_progress') and
            new_total_cost is not None and
            new_total_cost > 0
        )
        if is_survey_to_pending:
            try:
                quote_ready_notification = send_quote_ready_notification(request_row, new_total_cost)
            except Exception:
                app.logger.exception('Failed to send quote ready notification for request %s', request_id)
                quote_ready_notification = {'user_sent': False}
        else:
            try:
                status_notification = send_status_update_notifications(request_row, previous_status)
            except Exception:
                app.logger.exception('Failed to dispatch status update notifications for request %s', request_id)
                status_notification = {'admin_sent': False, 'user_sent': False}
        try:
            sync_service_request_status(request_id, request_row.get('status'))
        except Exception:
            app.logger.exception('Failed to sync service request status for %s', request_id)

    detail = generate_request_context(request_row)
    detail['admin_notes'] = request_row.get('admin_notes')
    detail['attachments'] = attachments
    detail['metadata'] = detail.get('metadata') or {}
    detail['service_flow'] = detail.get('service_flow') or detail['metadata'].get('service_flow')
    detail['service_request'] = fetch_service_request_detail(request_id)

    if detail['service_request']:
        detail['payment_type'] = detail['service_request'].get('payment_type')
        detail['is_paid'] = bool(detail['service_request'].get('is_paid'))
        detail['payment_status_label'] = detail['service_request'].get('payment_status_label')
        detail['payment_badge'] = detail['service_request'].get('payment_badge')
        detail['amount_due'] = detail['service_request'].get('total_price')
    else:
        detail['payment_type'] = ''
        detail['is_paid'] = False
        detail['payment_status_label'] = '—'
        detail['payment_badge'] = 'na'
        detail['amount_due'] = None

    travel_summary = {}
    if detail['service_flow'] and isinstance(detail['service_flow'], dict):
        travel_summary = detail['service_flow'].get('travel') or {}
    if detail['service_request']:
        travel_summary = travel_summary or {}
        travel_summary.setdefault('travel_fee', detail['service_request'].get('travel_fee'))
        travel_summary.setdefault('distance_miles', detail['service_request'].get('distance_miles'))
        travel_summary.setdefault('travel_time_minutes', detail['service_request'].get('travel_time_minutes'))
        travel_summary.setdefault('pricing_method', detail['service_request'].get('pricing_method'))
        travel_summary.setdefault('base_id', detail['service_request'].get('assigned_base_id'))
        travel_summary.setdefault('base_name', detail['service_request'].get('assigned_base_name'))
    detail['travel'] = travel_summary

    detail['status_label'] = get_status_label(detail.get('status'))
    detail['previous_status'] = previous_status
    detail['previous_status_label'] = get_status_label(previous_status)
    detail['status_changed'] = status_changed
    detail['status_notification'] = status_notification
    detail['quote_ready_notification'] = quote_ready_notification
    detail['total_cost_updated'] = total_cost_updated
    return jsonify(detail)


@app.route('/admin/api/contracts', methods=['GET'])
@app.route('/api/contracts', methods=['GET'])
@admin_login_required
def admin_contracts_api():
    try:
        limit = request.args.get('limit', 250)
        contracts = fetch_contract_records(limit=limit)
        return jsonify(contracts)
    except Exception:
        app.logger.exception('Failed to fetch contracts list')
        return jsonify({'error': 'Unable to fetch contracts right now.'}), 500


@app.route('/admin/api/contracts/reminders/run', methods=['POST'])
@admin_login_required
def admin_run_contract_reminders_api():
    try:
        result = process_due_contract_reminders(limit=100)
        return jsonify({'message': 'Reminder run completed.', **(result or {})})
    except Exception:
        app.logger.exception('Failed to run contract reminders manually')
        return jsonify({'error': 'Unable to process reminders right now.'}), 500


@app.route('/admin/api/contracts/<int:contract_id>', methods=['PATCH'])
@admin_login_required
def admin_update_contract_api(contract_id):
    """Update contract fields: status, reminder_enabled, next_service_date, next_reminder_at, preferred_time, frequency."""
    try:
        payload = request.get_json(silent=True) or {}
        allowed = ('status', 'reminder_enabled', 'next_service_date', 'next_reminder_at', 'preferred_time', 'frequency')
        updates = {k: v for k, v in payload.items() if k in allowed}
        if not updates:
            return jsonify({'error': 'No valid fields to update.'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Fetch current contract so we can detect schedule changes and get contact info
        cursor.execute("SELECT * FROM contracts WHERE id = %s LIMIT 1", (contract_id,))
        existing = cursor.fetchone() or {}

        set_parts = [f"{k} = %s" for k in updates]
        values = list(updates.values())
        values.append(contract_id)
        cursor.execute(f"UPDATE contracts SET {', '.join(set_parts)} WHERE id = %s", values)
        conn.commit()
        cursor.close()
        conn.close()

        # Send reschedule email if next_service_date changed
        rescheduled = (
            'next_service_date' in updates
            and str(updates['next_service_date']) != str(existing.get('next_service_date') or '')
        )
        if rescheduled:
            try:
                recipient = sanitize_email(existing.get('customer_email'))
                email_settings = fetch_email_settings()
                if recipient and email_settings and int(email_settings.get('is_active') or 0):
                    new_date = updates['next_service_date']
                    new_time = updates.get('preferred_time') or existing.get('preferred_time') or '09:00'
                    service_name = existing.get('service_name') or 'Cleaning service'
                    cust_name = existing.get('customer_name') or 'there'
                    frequency_label = format_contract_frequency_label(existing.get('frequency')) or 'Recurring'
                    subject = f"Your {service_name} has been rescheduled"
                    html_body = (
                        f"<p>Hello {cust_name},</p>"
                        f"<p>Your {frequency_label.lower()} contract service <strong>{service_name}</strong> has been rescheduled.</p>"
                        f"<p><strong>New date:</strong> {new_date} at {new_time}</p>"
                        f"<p>If you have any questions or need to make further changes, please reply to this email.</p>"
                        f"<p>Thank you,<br>Done-Well Cleaning Team</p>"
                    )
                    text_body = (
                        f"Hello {cust_name},\n\n"
                        f"Your {frequency_label.lower()} contract service ({service_name}) has been rescheduled.\n"
                        f"New date: {new_date} at {new_time}\n\n"
                        "Questions? Reply to this email.\n\nDone-Well Cleaning Team"
                    )
                    send_email_via_settings(
                        subject=subject,
                        html_body=html_body,
                        text_body=text_body,
                        recipients=[recipient],
                        settings=email_settings,
                        reply_to=email_settings.get('reply_to') or email_settings.get('sender_email'),
                        error_context='contract_reschedule_notification'
                    )
            except Exception:
                app.logger.exception('Failed to send contract reschedule email for contract %s', contract_id)

        return jsonify({'message': 'Contract updated.'})
    except Exception:
        app.logger.exception('Failed to update contract %s', contract_id)
        return jsonify({'error': 'Unable to update contract.'}), 500


@app.route('/admin/api/contracts/<int:contract_id>/remind', methods=['POST'])
@admin_login_required
def admin_send_contract_reminder_api(contract_id):
    """Send an immediate reminder email for a specific contract."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM contracts WHERE id = %s LIMIT 1", (contract_id,))
        contract = cursor.fetchone()
        cursor.close()
        conn.close()

        if not contract:
            return jsonify({'error': 'Contract not found.'}), 404

        recipient = sanitize_email(contract.get('customer_email'))
        if not recipient:
            return jsonify({'error': 'No email address on this contract.'}), 400

        settings = fetch_email_settings()
        if not settings or not int(settings.get('is_active') or 0):
            return jsonify({'error': 'Email is not configured/active.'}), 400

        frequency_label = format_contract_frequency_label(contract.get('frequency')) or 'Recurring'
        service_date = contract.get('next_service_date') or 'TBC'
        service_time = contract.get('preferred_time') or '09:00'
        subject = f"Reminder: {contract.get('service_name') or 'Cleaning service'} — {service_date}"
        html_body = (
            f"<p>Hello {contract.get('customer_name') or 'there'},</p>"
            f"<p>This is a reminder for your {frequency_label.lower()} contract service <strong>{contract.get('service_name') or 'Cleaning service'}</strong>.</p>"
            f"<p><strong>Scheduled for:</strong> {service_date} at {service_time}</p>"
            f"<p>If you need to reschedule or have any questions, please reply to this email.</p>"
            f"<p>Thank you,<br>Done-Well Cleaning Team</p>"
        )
        text_body = (
            f"Hello {contract.get('customer_name') or 'there'},\n\n"
            f"Reminder for your {frequency_label.lower()} contract service ({contract.get('service_name') or 'Cleaning service'})\n"
            f"Scheduled for: {service_date} at {service_time}\n\n"
            "Need to reschedule? Reply to this email.\n\nDone-Well Cleaning Team"
        )
        sent = send_email_via_settings(
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            recipients=[recipient],
            settings=settings,
            reply_to=settings.get('reply_to') or settings.get('sender_email'),
            error_context='manual_contract_reminder'
        )

        if sent:
            # Update last_reminder_sent_at
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE contracts SET last_reminder_sent_at = CURRENT_TIMESTAMP WHERE id = %s", (contract_id,))
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'message': f'Reminder sent to {recipient}.'})
        else:
            return jsonify({'error': 'Email send failed. Check email settings.'}), 500
    except Exception:
        app.logger.exception('Failed to send contract reminder for %s', contract_id)
        return jsonify({'error': 'Unable to send reminder.'}), 500


@app.route('/admin/api/requests/<int:request_id>/files/<int:file_id>', methods=['GET'])
@app.route('/admin/api/requests/<int:request_id>/files/<int:file_id>/download', methods=['GET'])
@admin_login_required
def admin_download_request_file(request_id, file_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT original_filename, stored_path FROM request_files WHERE id = %s AND request_id = %s",
        (file_id, request_id)
    )
    file_row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not file_row:
        return jsonify({'error': 'File not found.'}), 404

    stored_path = (file_row.get('stored_path') or '').strip()
    if not stored_path:
        return jsonify({'error': 'File path invalid.'}), 400

    is_download_route = request.path.endswith('/download')
    download_param = request.args.get('download')
    as_attachment = is_download_route if download_param is None else str_to_bool(download_param)
    download_name = file_row.get('original_filename') or 'attachment'

    if stored_path.startswith(('http://', 'https://')):
        try:
            remote_resp = requests.get(stored_path, timeout=30)
            remote_resp.raise_for_status()
        except RequestException:
            return jsonify({'error': 'Unable to retrieve the attachment from remote storage.'}), 502

        mimetype = file_row.get('mime_type') or remote_resp.headers.get('Content-Type') or 'application/octet-stream'
        content = remote_resp.content or b''
        file_stream = BytesIO(content)
        file_stream.seek(0)
        response = send_file(
            file_stream,
            mimetype=mimetype,
            as_attachment=as_attachment,
            download_name=download_name if as_attachment else None
        )
        if not as_attachment:
            safe_name = secure_filename(download_name) or download_name
            response.headers['Content-Disposition'] = f'inline; filename="{safe_name}"'
        return response

    normalized_path = os.path.normpath(stored_path).replace('\\', '/')
    normalized_path = normalized_path.lstrip('/')
    if normalized_path.startswith('..'):
        return jsonify({'error': 'File path invalid.'}), 400
    absolute_path = safe_join(app.static_folder, normalized_path)
    if not absolute_path or not os.path.isfile(absolute_path):
        return jsonify({'error': 'File is missing from the server.'}), 410

    directory, filename = os.path.split(absolute_path)

    guessed_type = file_row.get('mime_type') or mimetypes.guess_type(download_name)[0] or mimetypes.guess_type(filename)[0]
    mimetype = guessed_type or 'application/octet-stream'

    response = send_file(
        absolute_path,
        mimetype=mimetype,
        as_attachment=as_attachment,
        download_name=(download_name or os.path.basename(filename)) if as_attachment else None
    )
    if not as_attachment:
        safe_name = secure_filename(download_name or os.path.basename(filename)) or (download_name or os.path.basename(filename))
        response.headers['Content-Disposition'] = f'inline; filename="{safe_name}"'
    return response


@app.route('/admin/api/email-settings', methods=['GET', 'POST'])
@admin_login_required
def admin_email_settings():
    if request.method == 'GET':
        settings = fetch_email_settings()
        settings['smtp_password'] = ''
        settings['resend_api_key'] = ''
        settings.pop('smtp_password_encrypted', None)
        settings.pop('resend_api_key_encrypted', None)
        return jsonify(settings)

    payload = request.get_json(silent=True) or {}
    try:
        updated = update_email_settings(payload)
        return jsonify({'message': 'Email settings updated.', 'settings': updated})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to update email settings.')
        return jsonify({'error': 'Unable to update email settings at this time.'}), 500


@app.route('/admin/api/payment-settings', methods=['GET', 'POST'])
@admin_login_required
def admin_payment_settings_api():
    if request.method == 'GET':
        return jsonify(fetch_payment_settings())

    payload = request.get_json(silent=True) or {}
    try:
        updated = upsert_payment_settings(payload)
        return jsonify({'message': 'Payment settings saved.', 'settings': updated})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to update payment settings.')
        return jsonify({'error': 'Unable to save payment settings right now.'}), 500


@app.route('/admin/api/payment-transactions', methods=['GET'])
@admin_login_required
def admin_payment_transactions_api():
    try:
        limit = request.args.get('limit', 200)
        rows = fetch_payment_transactions(limit=limit)
        return jsonify(rows)
    except Exception:
        app.logger.exception('Failed to load payment transactions.')
        return jsonify({'error': 'Unable to load transactions right now.'}), 500


@app.route('/admin/api/payment-transactions/<int:tx_id>/retry', methods=['POST'])
@admin_login_required
def admin_retry_payment_transaction(tx_id):
    """Re-run finalization for a Stripe transaction. For checkout_created, checks Stripe first."""
    try:
        tx = fetch_payment_transaction_by_id(tx_id)
        if not tx:
            return jsonify({'error': 'Transaction not found.'}), 404

        tx_status = tx.get('status') or ''
        force = request.args.get('force') == '1' or (request.get_json(silent=True) or {}).get('force')

        # For checkout_created/stale_pending: check Stripe to see if payment actually completed
        if tx_status in ('checkout_created', 'stale_pending') and not force:
            if not stripe_ready():
                return jsonify({'error': 'Stripe is not configured.'}), 500
            stripe.api_key = stripe_secret_key()

            is_paid_on_stripe = False
            resolved_payment_intent_id = tx.get('payment_intent_id') or ''

            # Try checkout session first — access attributes directly (avoid stripe_object_to_dict issues)
            session_id = tx.get('checkout_session_id')
            if session_id:
                try:
                    session_obj = stripe.checkout.Session.retrieve(
                        session_id, expand=['payment_intent']
                    )
                    # Read payment_status directly from the object
                    payment_status = ''
                    try:
                        payment_status = (getattr(session_obj, 'payment_status', None) or session_obj.get('payment_status') or '').lower()
                    except Exception:
                        pass
                    if payment_status == 'paid':
                        is_paid_on_stripe = True

                    # Get payment_intent — may be a StripeObject, a plain dict, or a string ID
                    pi_field = None
                    try:
                        pi_field = getattr(session_obj, 'payment_intent', None)
                        if pi_field is None:
                            pi_field = session_obj.get('payment_intent')
                    except Exception:
                        pass

                    if pi_field is not None:
                        if isinstance(pi_field, str) and pi_field.startswith('pi_'):
                            resolved_payment_intent_id = pi_field
                        elif hasattr(pi_field, 'id'):
                            resolved_payment_intent_id = pi_field.id or resolved_payment_intent_id
                            try:
                                if (getattr(pi_field, 'status', None) or '').lower() == 'succeeded':
                                    is_paid_on_stripe = True
                            except Exception:
                                pass
                        elif isinstance(pi_field, dict):
                            resolved_payment_intent_id = pi_field.get('id') or resolved_payment_intent_id
                            if (pi_field.get('status') or '').lower() == 'succeeded':
                                is_paid_on_stripe = True

                    app.logger.info('Session %s: payment_status=%s pi=%s is_paid=%s', session_id, payment_status, resolved_payment_intent_id, is_paid_on_stripe)
                except Exception as exc:
                    app.logger.warning('Could not retrieve Stripe session %s: %s', session_id, exc)

            # If session didn't confirm, try PaymentIntent directly (covers expired sessions that still have a PI)
            if not is_paid_on_stripe and resolved_payment_intent_id:
                try:
                    pi = stripe.PaymentIntent.retrieve(resolved_payment_intent_id)
                    pi_status = ''
                    try:
                        pi_status = (getattr(pi, 'status', None) or pi.get('status') or '').lower()
                    except Exception:
                        pass
                    if pi_status == 'succeeded':
                        is_paid_on_stripe = True
                    app.logger.info('PI %s status=%s is_paid=%s for tx %s', resolved_payment_intent_id, pi_status, is_paid_on_stripe, tx_id)
                except Exception as exc:
                    app.logger.warning('Could not retrieve Stripe PaymentIntent %s: %s', resolved_payment_intent_id, exc)

            # Last resort: search charges by metadata transaction_id
            if not is_paid_on_stripe:
                try:
                    searched = stripe.Charge.search(query=f'metadata["transaction_id"]:"{tx_id}"')
                    charges = []
                    try:
                        charges = getattr(searched, 'data', None) or searched.get('data') or []
                    except Exception:
                        pass
                    for ch in charges:
                        try:
                            ch_paid = getattr(ch, 'paid', None)
                            ch_status = (getattr(ch, 'status', None) or '').lower()
                            ch_pi = getattr(ch, 'payment_intent', None)
                            if ch_paid and ch_status == 'succeeded':
                                is_paid_on_stripe = True
                                if ch_pi and isinstance(ch_pi, str):
                                    resolved_payment_intent_id = ch_pi
                                break
                        except Exception:
                            pass
                except Exception as exc:
                    app.logger.warning('Charge search fallback failed for tx %s: %s', tx_id, exc)

            if not is_paid_on_stripe:
                return jsonify({'error': 'Payment was not completed on Stripe for this transaction.'}), 400

            # Payment confirmed — update local record and proceed to finalize
            update_payment_transaction(tx_id, status='paid', payment_intent_id=resolved_payment_intent_id or None)
            tx['status'] = 'paid'
            tx['payment_intent_id'] = resolved_payment_intent_id or tx.get('payment_intent_id')
            tx_status = 'paid'

        if tx_status not in ('paid', 'processing_failed'):
            return jsonify({'error': f"Cannot finalize transaction with status '{tx_status}'."}), 400

        prepared_payload = tx.get('prepared_payload') or '{}'
        prepared = json.loads(prepared_payload) if prepared_payload else {}
        if not prepared.get('payment_type_for_db'):
            prepared['payment_type_for_db'] = 'stripe'

        service_metadata = prepared.get('service_metadata') if isinstance(prepared.get('service_metadata'), dict) else {}
        payment_meta = service_metadata.get('payment') if isinstance(service_metadata.get('payment'), dict) else {}
        payment_meta.update({
            'option': PAYMENT_OPTION_PREBOOK,
            'payment_type': 'stripe',
            'is_paid': True,
            'transaction_id': tx.get('id'),
            'stripe_checkout_session_id': tx.get('checkout_session_id'),
            'stripe_payment_intent_id': tx.get('payment_intent_id'),
        })
        service_metadata['payment'] = payment_meta
        prepared['service_metadata'] = service_metadata

        if tx.get('request_id'):
            prepared['existing_request_id'] = tx.get('request_id')

        submission = finalize_prepared_service_booking(prepared, remote_addr='admin-retry', mark_paid=True)
        update_payment_transaction(
            tx_id,
            status='processed',
            request_id=submission.get('request_id'),
            service_request_id=submission.get('service_request_id')
        )
        return jsonify({'message': 'Transaction finalized successfully.', 'request_id': submission.get('request_id'), 'service_request_id': submission.get('service_request_id')})
    except Exception:
        app.logger.exception('Admin retry failed for transaction %s', tx_id)
        return jsonify({'error': 'Retry failed. Check server logs for details.'}), 500


@app.route('/admin/api/payment-transactions/<int:tx_id>/refund', methods=['POST'])
@admin_login_required
def admin_refund_payment_transaction(tx_id):
    """Issue a full or partial refund for a processed Stripe transaction."""
    try:
        tx = fetch_payment_transaction_by_id(tx_id)
        if not tx:
            return jsonify({'error': 'Transaction not found.'}), 404

        if tx.get('refund_status') in ('succeeded', 'pending'):
            return jsonify({'error': 'This transaction has already been refunded.'}), 400

        pi_id = tx.get('payment_intent_id')
        if not pi_id:
            return jsonify({'error': 'No PaymentIntent ID on record — cannot issue refund automatically.'}), 400

        if not stripe_ready():
            return jsonify({'error': 'Stripe is not configured.'}), 500

        stripe.api_key = stripe_secret_key()

        payload = request.get_json(silent=True) or {}
        amount_pence = None
        if payload.get('amount'):
            try:
                amount_pence = int(round(float(payload['amount']) * 100))
            except (TypeError, ValueError):
                return jsonify({'error': 'Invalid refund amount.'}), 400

        refund_kwargs = {'payment_intent': pi_id, 'reason': 'requested_by_customer'}
        if amount_pence:
            refund_kwargs['amount'] = amount_pence

        refund_obj = stripe.Refund.create(**refund_kwargs)
        refund_status = ''
        refund_id = ''
        try:
            refund_status = getattr(refund_obj, 'status', None) or refund_obj.get('status') or ''
            refund_id = getattr(refund_obj, 'id', None) or refund_obj.get('id') or ''
        except Exception:
            pass

        update_payment_transaction(
            tx_id,
            refund_id=refund_id,
            refund_status=refund_status,
            refunded_at=datetime.now(timezone.utc),
            status='refunded'
        )

        return jsonify({'message': f'Refund issued. Status: {refund_status}', 'refund_id': refund_id, 'refund_status': refund_status})
    except stripe.error.StripeError as exc:
        msg = getattr(exc, 'user_message', None) or str(exc)
        return jsonify({'error': f'Stripe error: {msg}'}), 400
    except Exception:
        app.logger.exception('Refund failed for tx %s', tx_id)
        return jsonify({'error': 'Refund failed. Check server logs.'}), 500


@app.route('/admin/api/travel-settings', methods=['GET', 'POST'])
@admin_login_required
def admin_travel_settings_api():
    if request.method == 'GET':
        settings = serialize_travel_settings(fetch_travel_settings())
        return jsonify(settings)

    payload = request.get_json(silent=True) or {}
    try:
        updated = serialize_travel_settings(upsert_travel_settings(payload))
        return jsonify({'message': 'Travel settings saved.', 'settings': updated})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to update travel settings.')
        return jsonify({'error': 'Unable to update travel settings right now.'}), 500


@app.route('/admin/api/site-content', methods=['GET', 'POST'])
@admin_login_required
def admin_site_content_api():
    """GET: Fetch all site content sections. POST: Update specific section(s)."""
    if request.method == 'GET':
        content = fetch_site_content()
        return jsonify(content)

    payload = request.get_json(silent=True) or {}
    if not payload:
        return jsonify({'error': 'No data provided.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    updated_keys = []
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    active_value = 1

    if 'postgres' in engine:
        try:
            cursor.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'site_content'
                  AND column_name = 'is_active'
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            data_type = (row[0] or '').strip().lower() if row and len(row) > 0 else ''
            active_value = True if data_type == 'boolean' else 1
        except Exception:
            # Fallback for mixed/legacy schemas; integer is valid for smallint-backed flags.
            active_value = 1

    try:
        for section_key, value in payload.items():
            section_key = sanitize_text(section_key, 50)
            if not section_key:
                continue

            # Determine if it's JSON or text content
            if isinstance(value, (dict, list)):
                content_text = None
                content_json = json.dumps(value)
            else:
                content_text = sanitize_text(str(value), 10000) if value else ''
                content_json = None

            cursor.execute(
                """
                UPDATE site_content
                SET content_text=%s,
                    content_json=%s,
                    is_active=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE section_key=%s
                """,
                (content_text, content_json, active_value, section_key)
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO site_content (section_key, content_text, content_json, is_active)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (section_key, content_text, content_json, active_value)
                )
            updated_keys.append(section_key)

        conn.commit()
        cursor.close()
        conn.close()

        updated_content = fetch_site_content()
        return jsonify({
            'message': f'Updated {len(updated_keys)} section(s).',
            'updated': updated_keys,
            'content': updated_content
        })
    except Exception:
        app.logger.exception('Failed to update site content.')
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        return jsonify({'error': 'Unable to update site content right now.'}), 500


@app.route('/admin/api/team-photo', methods=['POST', 'DELETE'])
@admin_login_required
def admin_team_photo_api():
    """Upload or delete team photo for About section."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Get current team photo path
        cursor.execute("SELECT content_text FROM site_content WHERE section_key = 'team_photo'")
        row = cursor.fetchone()
        existing_path = row['content_text'] if row else ''
        
        if request.method == 'DELETE':
            if existing_path:
                delete_uploaded_file(existing_path)
            cursor.execute(
                "DELETE FROM site_content WHERE section_key = 'team_photo'"
            )
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'message': 'Team photo removed.', 'team_photo': ''})
        
        # POST - upload new photo
        new_path = upload_team_photo(existing_path)
        if new_path and new_path != existing_path:
            # Delete old file if different
            if existing_path and existing_path != new_path:
                delete_uploaded_file(existing_path)
            
            cursor.execute(
                """
                UPDATE site_content
                SET content_text=%s,
                    updated_at=CURRENT_TIMESTAMP
                WHERE section_key='team_photo'
                """,
                (new_path,)
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    """
                    INSERT INTO site_content (section_key, content_text, is_active)
                    VALUES ('team_photo', %s, TRUE)
                    """,
                    (new_path,)
                )
            conn.commit()
            cursor.close()
            conn.close()
            return jsonify({'message': 'Team photo uploaded.', 'team_photo': new_path})
        
        cursor.close()
        conn.close()
        return jsonify({'message': 'No file uploaded.', 'team_photo': existing_path})
    except Exception:
        app.logger.exception('Failed to update team photo.')
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        return jsonify({'error': 'Unable to update team photo.'}), 500


@app.route('/admin/site-content')
@admin_login_required
def admin_site_content_page():
    """Render the Site Content admin page."""
    site_settings = fetch_site_settings()
    site_content = fetch_site_content()
    return render_template('admin/site_content.html', site_settings=site_settings, site_content=site_content)


# ─────────────────────────────────────────────────────────────────────────────
# FAQ Management Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/faqs')
@admin_login_required
def admin_faqs_page():
    """Render the FAQ management admin page."""
    ensure_faq_table()
    site_settings = fetch_site_settings()
    return render_template('admin/faqs.html', site_settings=site_settings)


@app.route('/admin/api/faqs', methods=['GET'])
@admin_login_required
def admin_get_faqs():
    """Get all FAQs for admin."""
    ensure_faq_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM faqs ORDER BY sort_order ASC, id ASC")
    faqs = cursor.fetchall()
    cursor.close()
    conn.close()
    # Convert boolean fields for JSON
    for faq in faqs:
        faq['is_active'] = bool(faq.get('is_active'))
    return jsonify(faqs)


@app.route('/admin/api/faqs', methods=['POST'])
@admin_login_required
def admin_create_faq():
    """Create a new FAQ."""
    ensure_faq_table()
    payload = request.get_json(silent=True) or {}
    question = sanitize_text(payload.get('question', ''), 1000)
    answer = sanitize_text(payload.get('answer', ''), 5000)
    category = sanitize_text(payload.get('category', 'General'), 100)
    sort_order = int(payload.get('sort_order', 0))
    is_active = bool(payload.get('is_active', True))

    if not question or not answer:
        return jsonify({'error': 'Question and answer are required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute(
            "INSERT INTO faqs (question, answer, category, sort_order, is_active) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (question, answer, category, sort_order, is_active)
        )
        faq_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            "INSERT INTO faqs (question, answer, category, sort_order, is_active) VALUES (%s, %s, %s, %s, %s)",
            (question, answer, category, sort_order, 1 if is_active else 0)
        )
        faq_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'FAQ created.', 'id': faq_id})


@app.route('/admin/api/faqs/<int:faq_id>', methods=['PUT', 'PATCH'])
@admin_login_required
def admin_update_faq(faq_id):
    """Update an existing FAQ."""
    ensure_faq_table()
    payload = request.get_json(silent=True) or {}

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM faqs WHERE id = %s", (faq_id,))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        conn.close()
        return jsonify({'error': 'FAQ not found.'}), 404

    question = sanitize_text(payload.get('question', existing['question']), 1000)
    answer = sanitize_text(payload.get('answer', existing['answer']), 5000)
    category = sanitize_text(payload.get('category', existing.get('category', 'General')), 100)
    sort_order = int(payload.get('sort_order', existing.get('sort_order', 0)))
    is_active = payload.get('is_active', existing.get('is_active', True))
    if isinstance(is_active, str):
        is_active = is_active.lower() in ('true', '1', 'yes')
    else:
        is_active = bool(is_active)

    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    cursor.execute(
        "UPDATE faqs SET question=%s, answer=%s, category=%s, sort_order=%s, is_active=%s WHERE id=%s",
        (question, answer, category, sort_order, is_active if engine == 'postgres' else (1 if is_active else 0), faq_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'FAQ updated.'})


@app.route('/admin/api/faqs/<int:faq_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_faq(faq_id):
    """Delete an FAQ."""
    ensure_faq_table()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM faqs WHERE id = %s", (faq_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'FAQ deleted.'})


@app.route('/admin/api/faqs/reorder', methods=['POST'])
@admin_login_required
def admin_reorder_faqs():
    """Reorder FAQs by updating sort_order."""
    ensure_faq_table()
    payload = request.get_json(silent=True) or {}
    order = payload.get('order', [])  # List of faq IDs in new order

    if not order:
        return jsonify({'error': 'No order provided.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    for idx, faq_id in enumerate(order):
        cursor.execute("UPDATE faqs SET sort_order = %s WHERE id = %s", (idx, faq_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'FAQs reordered.'})


# ─────────────────────────────────────────────────────────────────────────────
# Policy Management Routes (Admin)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/policies')
@admin_login_required
def admin_policies_page():
    """Render the Policy management admin page."""
    ensure_policy_table()
    site_settings = fetch_site_settings()
    return render_template('admin/policies.html', site_settings=site_settings)


@app.route('/admin/api/policies', methods=['GET'])
@admin_login_required
def admin_get_policies():
    """Get all policies for admin."""
    policies = fetch_policies_from_db(include_inactive=True)
    return jsonify(policies)


@app.route('/admin/api/policies', methods=['POST'])
@admin_login_required
def admin_create_policy():
    """Create a new policy."""
    ensure_policy_table()
    payload = request.get_json(silent=True) or {}
    title = sanitize_text(payload.get('title', ''), 255)
    description = sanitize_text(payload.get('description', ''), 5000)
    icon = sanitize_text(payload.get('icon', 'shield'), 100)
    sort_order = int(payload.get('sort_order', 0))
    is_active = bool(payload.get('is_active', True))

    if not title or not description:
        return jsonify({'error': 'Title and description are required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if engine == 'postgres':
        cursor.execute(
            "INSERT INTO policies (title, description, icon, sort_order, is_active) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (title, description, icon, sort_order, is_active)
        )
        policy_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            "INSERT INTO policies (title, description, icon, sort_order, is_active) VALUES (%s, %s, %s, %s, %s)",
            (title, description, icon, sort_order, 1 if is_active else 0)
        )
        policy_id = cursor.lastrowid

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Policy created.', 'id': policy_id})


@app.route('/admin/api/policies/<int:policy_id>', methods=['PUT', 'PATCH'])
@admin_login_required
def admin_update_policy(policy_id):
    """Update an existing policy."""
    ensure_policy_table()
    payload = request.get_json(silent=True) or {}

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM policies WHERE id = %s", (policy_id,))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Policy not found.'}), 404

    title = sanitize_text(payload.get('title', existing['title']), 255)
    description = sanitize_text(payload.get('description', existing['description']), 5000)
    icon = sanitize_text(payload.get('icon', existing.get('icon', 'shield')), 100)
    sort_order = int(payload.get('sort_order', existing.get('sort_order', 0)))
    is_active = payload.get('is_active', existing.get('is_active', True))
    if isinstance(is_active, str):
        is_active = is_active.lower() in ('true', '1', 'yes')
    else:
        is_active = bool(is_active)

    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    cursor.execute(
        "UPDATE policies SET title=%s, description=%s, icon=%s, sort_order=%s, is_active=%s WHERE id=%s",
        (title, description, icon, sort_order, is_active if engine == 'postgres' else (1 if is_active else 0), policy_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Policy updated.'})


@app.route('/admin/api/policies/<int:policy_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_policy(policy_id):
    """Delete a policy."""
    ensure_policy_table()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM policies WHERE id = %s", (policy_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Policy deleted.'})


@app.route('/admin/api/policies/reorder', methods=['POST'])
@admin_login_required
def admin_reorder_policies():
    """Reorder policies by updating sort_order."""
    ensure_policy_table()
    payload = request.get_json(silent=True) or {}
    order = payload.get('order', [])

    if not order:
        return jsonify({'error': 'No order provided.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    for idx, policy_id in enumerate(order):
        cursor.execute("UPDATE policies SET sort_order = %s WHERE id = %s", (idx, policy_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Policies reordered.'})


@app.route('/admin/api/homepage-sections', methods=['GET'])
@admin_login_required
def admin_get_homepage_sections():
    """Return homepage sections in current render order."""
    try:
        return jsonify(fetch_home_page_sections())
    except Exception:
        app.logger.exception('Failed to load homepage sections.')
        return jsonify({'error': 'Unable to load homepage sections right now.'}), 500


@app.route('/admin/api/homepage-sections/reorder', methods=['POST'])
@admin_login_required
def admin_reorder_homepage_sections():
    """Persist homepage section order immediately after drag-and-drop."""
    payload = request.get_json(silent=True) or {}
    section_keys = payload.get('section_keys')

    try:
        updated_sections = save_home_page_section_order(section_keys)
        return jsonify({'message': 'Homepage section order updated.', 'sections': updated_sections})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to reorder homepage sections.')
        return jsonify({'error': 'Unable to save homepage order right now.'}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Service Room Cards CRUD (Admin) — replaces domestic_cleaning_cards routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/domestic-cleaning')
@admin_login_required
def admin_domestic_cleaning_page():
    """Domestic cleaning editor backed by unified services data."""
    site_settings = fetch_site_settings()
    return render_template('admin/domestic_cleaning.html', site_settings=site_settings)


@app.route('/admin/api/domestic-cleaning', methods=['GET', 'POST'])
@admin_login_required
def admin_domestic_cleaning_content_api():
    service = get_domestic_service_record(create_if_missing=True)
    if not service:
        return jsonify({'error': 'Domestic Cleaning service not found.'}), 404

    if request.method == 'GET':
        return jsonify(
            {
                'service_id': service.get('id'),
                'section_title': service.get('contract_section_title') or service.get('title') or 'Domestic Cleaning',
                'section_subtitle': service.get('contract_section_subtitle') or '',
                'intro_title': service.get('contract_intro_title') or '',
                'intro_body': service.get('contract_intro_body') or '',
                'trust_body': service.get('contract_trust_body') or '',
                'continuity_body': service.get('contract_continuity_body') or '',
                'is_active': bool(service.get('is_active'))
            }
        )

    payload = request.get_json(silent=True) or {}
    section_title = sanitize_text(payload.get('section_title'), 255) or 'Domestic Cleaning'
    section_subtitle = sanitize_text(payload.get('section_subtitle')) or ''
    intro_title = sanitize_text(payload.get('intro_title'), 255) or ''
    intro_body = sanitize_text(payload.get('intro_body')) or ''
    trust_body = sanitize_text(payload.get('trust_body')) or ''
    continuity_body = sanitize_text(payload.get('continuity_body')) or ''
    is_active = str_to_bool(payload.get('is_active', True))
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    active_value = is_active if engine == 'postgres' else (1 if is_active else 0)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE services
        SET title=%s,
            name=%s,
            short_description=%s,
            contract_section_title=%s,
            contract_section_subtitle=%s,
            contract_intro_title=%s,
            contract_intro_body=%s,
            contract_trust_body=%s,
            contract_continuity_body=%s,
            service_category=%s,
            is_contract=%s,
            is_active=%s
        WHERE id=%s
        """,
        (
            section_title,
            section_title,
            section_subtitle[:150],
            section_title,
            section_subtitle,
            intro_title,
            intro_body,
            trust_body,
            continuity_body,
            'contract',
            True if engine == 'postgres' else 1,
            active_value,
            service.get('id')
        )
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Domestic cleaning section saved.'})


@app.route('/admin/api/domestic-cleaning/cards', methods=['GET', 'POST'])
@admin_login_required
def admin_domestic_cleaning_cards_api():
    service = get_domestic_service_record(create_if_missing=True)
    if not service:
        return jsonify({'error': 'Domestic Cleaning service not found.'}), 404

    service_id = int(service.get('id'))

    if request.method == 'GET':
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM service_room_cards WHERE service_id=%s ORDER BY sort_order ASC, id ASC",
            (service_id,)
        )
        cards = cursor.fetchall()
        cursor.close()
        conn.close()
        for card in cards:
            card['is_active'] = bool(card.get('is_active'))
        return jsonify(cards)

    payload = request.form or request.get_json(silent=True) or {}
    room_name = sanitize_text(payload.get('room_name'), 120)
    card_key = sanitize_text(payload.get('card_key'), 80).lower().replace(' ', '_')
    lifestyle_copy = sanitize_text(payload.get('lifestyle_copy'))
    sort_order = int(payload.get('sort_order') or 0)
    is_active = str_to_bool(payload.get('is_active', True))
    image_path = upload_domestic_card_image('')
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    active_value = is_active if engine == 'postgres' else (1 if is_active else 0)

    if not room_name or not card_key or not lifestyle_copy:
        return jsonify({'error': 'room_name, card_key and lifestyle_copy are required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if engine == 'postgres':
            cursor.execute(
                """
                INSERT INTO service_room_cards (service_id, card_key, room_name, lifestyle_copy, image_path, sort_order, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (service_id, card_key, room_name, lifestyle_copy, image_path or None, sort_order, active_value)
            )
            card_id = cursor.fetchone()[0]
        else:
            cursor.execute(
                """
                INSERT INTO service_room_cards (service_id, card_key, room_name, lifestyle_copy, image_path, sort_order, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (service_id, card_key, room_name, lifestyle_copy, image_path or None, sort_order, active_value)
            )
            card_id = cursor.lastrowid
    except Exception:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Card key must be unique per service.'}), 400

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Room card created.', 'id': card_id})


@app.route('/admin/api/domestic-cleaning/cards/<int:card_id>', methods=['PUT', 'DELETE'])
@admin_login_required
def admin_domestic_cleaning_card_detail_api(card_id):
    service = get_domestic_service_record(create_if_missing=True)
    if not service:
        return jsonify({'error': 'Domestic Cleaning service not found.'}), 404

    service_id = int(service.get('id'))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM service_room_cards WHERE id=%s AND service_id=%s", (card_id, service_id))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Card not found.'}), 404

    if request.method == 'DELETE':
        cursor.close()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM service_room_cards WHERE id=%s AND service_id=%s", (card_id, service_id))
        conn.commit()
        cursor.close()
        conn.close()
        if existing.get('image_path'):
            delete_uploaded_file(existing.get('image_path'))
        return jsonify({'message': 'Card deleted.'})

    payload = request.form or request.get_json(silent=True) or {}
    room_name = sanitize_text(payload.get('room_name', existing.get('room_name')), 120)
    card_key = sanitize_text(payload.get('card_key', existing.get('card_key')), 80).lower().replace(' ', '_')
    lifestyle_copy = sanitize_text(payload.get('lifestyle_copy', existing.get('lifestyle_copy')))
    sort_order = int(payload.get('sort_order', existing.get('sort_order') or 0))
    is_active = str_to_bool(payload.get('is_active', existing.get('is_active', True)))
    old_image = existing.get('image_path') or ''
    image_path = upload_domestic_card_image(old_image)
    if image_path and old_image and image_path != old_image:
        delete_uploaded_file(old_image)

    if not room_name or not card_key or not lifestyle_copy:
        cursor.close()
        conn.close()
        return jsonify({'error': 'room_name, card_key and lifestyle_copy are required.'}), 400

    cursor.close()
    cursor = conn.cursor()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    active_value = is_active if engine == 'postgres' else (1 if is_active else 0)
    try:
        cursor.execute(
            """
            UPDATE service_room_cards
            SET card_key=%s, room_name=%s, lifestyle_copy=%s, image_path=%s, sort_order=%s, is_active=%s
            WHERE id=%s AND service_id=%s
            """,
            (card_key, room_name, lifestyle_copy, image_path or None, sort_order, active_value, card_id, service_id)
        )
    except Exception:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Card key must be unique per service.'}), 400

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Card updated.'})


@app.route('/admin/api/domestic-cleaning/pricing', methods=['GET', 'POST'])
@admin_login_required
def admin_domestic_cleaning_pricing_api():
    service = get_domestic_service_record(create_if_missing=True)
    if not service:
        return jsonify({'error': 'Domestic Cleaning service not found.'}), 404

    plans = _load_domestic_pricing_from_service(service.get('contract_pricing_plans'))

    if request.method == 'GET':
        return jsonify(plans)

    payload = request.get_json(silent=True) or {}
    candidate = _normalize_domestic_plan_row(payload, (max([int(p.get('id') or 0) for p in plans] or [0]) + 1))
    if not candidate:
        return jsonify({'error': 'plan_key is required.'}), 400

    if any((p.get('plan_key') or '').lower() == candidate.get('plan_key') for p in plans):
        return jsonify({'error': 'Plan key already exists.'}), 400

    plans.append(candidate)
    plans.sort(key=lambda row: (int(row.get('sort_order') or 0), int(row.get('id') or 0)))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE services SET contract_pricing_plans=%s WHERE id=%s",
        (_serialize_domestic_pricing_for_service(plans), service.get('id'))
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'message': 'Pricing plan created.'})


@app.route('/admin/api/domestic-cleaning/pricing/<int:plan_id>', methods=['PUT', 'DELETE'])
@admin_login_required
def admin_domestic_cleaning_pricing_detail_api(plan_id):
    service = get_domestic_service_record(create_if_missing=True)
    if not service:
        return jsonify({'error': 'Domestic Cleaning service not found.'}), 404

    plans = _load_domestic_pricing_from_service(service.get('contract_pricing_plans'))
    idx = next((i for i, row in enumerate(plans) if int(row.get('id') or 0) == int(plan_id)), -1)
    if idx < 0:
        return jsonify({'error': 'Pricing plan not found.'}), 404

    if request.method == 'DELETE':
        plans.pop(idx)
    else:
        payload = request.get_json(silent=True) or {}
        updated = _normalize_domestic_plan_row(payload, plan_id)
        if not updated:
            return jsonify({'error': 'plan_key is required.'}), 400
        duplicate = next(
            (
                row for i, row in enumerate(plans)
                if i != idx and (row.get('plan_key') or '').lower() == updated.get('plan_key')
            ),
            None
        )
        if duplicate:
            return jsonify({'error': 'Plan key already exists.'}), 400
        updated['id'] = plan_id
        plans[idx] = updated

    plans.sort(key=lambda row: (int(row.get('sort_order') or 0), int(row.get('id') or 0)))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE services SET contract_pricing_plans=%s WHERE id=%s",
        (_serialize_domestic_pricing_for_service(plans), service.get('id'))
    )
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'message': 'Pricing plans updated.'})


@app.route('/admin/api/services/<int:service_id>/room-cards', methods=['GET', 'POST'])
@admin_login_required
def admin_service_room_cards_api(service_id):
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()

    if request.method == 'GET':
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM service_room_cards WHERE service_id=%s ORDER BY sort_order ASC, id ASC",
            (service_id,)
        )
        cards = cursor.fetchall()
        cursor.close()
        conn.close()
        for c in cards:
            c['is_active'] = bool(c.get('is_active'))
        return jsonify(cards)

    payload = request.form or request.get_json(silent=True) or {}
    room_name = sanitize_text(payload.get('room_name'), 120)
    card_key = sanitize_text(payload.get('card_key'), 80).lower().replace(' ', '_')
    lifestyle_copy = sanitize_text(payload.get('lifestyle_copy'))
    sort_order = int(payload.get('sort_order') or 0)
    is_active = str_to_bool(payload.get('is_active', True))
    image_path = upload_domestic_card_image('')

    if not room_name or not card_key or not lifestyle_copy:
        return jsonify({'error': 'room_name, card_key and lifestyle_copy are required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    active_value = is_active if engine == 'postgres' else (1 if is_active else 0)
    try:
        if engine == 'postgres':
            cursor.execute(
                """
                INSERT INTO service_room_cards (service_id, card_key, room_name, lifestyle_copy, image_path, sort_order, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (service_id, card_key, room_name, lifestyle_copy, image_path or None, sort_order, active_value)
            )
            card_id = cursor.fetchone()[0]
        else:
            cursor.execute(
                """
                INSERT INTO service_room_cards (service_id, card_key, room_name, lifestyle_copy, image_path, sort_order, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (service_id, card_key, room_name, lifestyle_copy, image_path or None, sort_order, active_value)
            )
            card_id = cursor.lastrowid
    except Exception:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Card key must be unique per service.'}), 400

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Room card created.', 'id': card_id})


@app.route('/admin/api/services/room-cards/<int:card_id>', methods=['PUT', 'PATCH', 'DELETE'])
@admin_login_required
def admin_service_room_card_detail_api(card_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM service_room_cards WHERE id=%s", (card_id,))
    existing = cursor.fetchone()
    if not existing:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Card not found.'}), 404

    if request.method == 'DELETE':
        cursor.close()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM service_room_cards WHERE id=%s", (card_id,))
        conn.commit()
        cursor.close()
        conn.close()
        if existing.get('image_path'):
            delete_uploaded_file(existing.get('image_path'))
        return jsonify({'message': 'Card deleted.'})

    payload = request.form or request.get_json(silent=True) or {}
    room_name = sanitize_text(payload.get('room_name', existing.get('room_name')), 120)
    card_key = sanitize_text(payload.get('card_key', existing.get('card_key')), 80).lower().replace(' ', '_')
    lifestyle_copy = sanitize_text(payload.get('lifestyle_copy', existing.get('lifestyle_copy')))
    sort_order = int(payload.get('sort_order', existing.get('sort_order') or 0))
    is_active = str_to_bool(payload.get('is_active', existing.get('is_active', True)))
    old_image = existing.get('image_path') or ''
    remove_image = str_to_bool(payload.get('remove_image', 'false'))
    if remove_image:
        if old_image:
            delete_uploaded_file(old_image)
        image_path = None
    else:
        image_path = upload_domestic_card_image(old_image)
        if image_path and old_image and image_path != old_image:
            delete_uploaded_file(old_image)

    if not room_name or not card_key or not lifestyle_copy:
        cursor.close()
        conn.close()
        return jsonify({'error': 'room_name, card_key and lifestyle_copy are required.'}), 400

    cursor.close()
    cursor = conn.cursor()
    active_value = 1 if is_active else 0
    try:
        cursor.execute(
            """
            UPDATE service_room_cards
            SET card_key=%s, room_name=%s, lifestyle_copy=%s, image_path=%s, sort_order=%s, is_active=%s
            WHERE id=%s
            """,
            (card_key, room_name, lifestyle_copy, image_path if not remove_image else None, sort_order, active_value, card_id)
        )
    except Exception:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Card key must be unique per service.'}), 400

    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Card updated.'})


@app.route('/admin/api/services/<int:service_id>/room-cards/reorder', methods=['POST'])
@admin_login_required
def admin_service_room_cards_reorder_api(service_id):
    payload = request.get_json(silent=True) or {}
    order = payload.get('order') or []
    if not isinstance(order, list) or not order:
        return jsonify({'error': 'No order provided.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    for idx, card_id in enumerate(order):
        cursor.execute("UPDATE service_room_cards SET sort_order=%s WHERE id=%s AND service_id=%s", (idx, card_id, service_id))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'Cards reordered.'})


# ─────────────────────────────────────────────────────────────────────────────
# AI Chat Management Routes (Admin)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/chat-conversations')
@admin_login_required
def admin_chat_conversations_page():
    """Render the chat conversations admin page."""
    ensure_chat_tables()
    site_settings = fetch_site_settings()
    return render_template('admin/chat_conversations.html', site_settings=site_settings)


@app.route('/admin/api/chat/conversations', methods=['GET'])
@admin_login_required
def admin_get_conversations():
    """Get all chat conversations for admin review."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Get pagination params
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    offset = (page - 1) * per_page
    
    # Get total count
    cursor.execute("SELECT COUNT(*) as total FROM chat_sessions")
    total = cursor.fetchone()['total']
    
    # Get sessions with message preview
    cursor.execute("""
        SELECT cs.*, 
        (SELECT content FROM chat_messages WHERE session_id = cs.session_id AND role = 'user' ORDER BY created_at LIMIT 1) as first_message
        FROM chat_sessions cs
        ORDER BY cs.last_message_at DESC
        LIMIT %s OFFSET %s
    """, (per_page, offset))
    sessions = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Convert datetime and boolean fields
    for s in sessions:
        s['is_resolved'] = bool(s.get('is_resolved'))
        if s.get('started_at'):
            s['started_at'] = s['started_at'].isoformat() if hasattr(s['started_at'], 'isoformat') else str(s['started_at'])
        if s.get('last_message_at'):
            s['last_message_at'] = s['last_message_at'].isoformat() if hasattr(s['last_message_at'], 'isoformat') else str(s['last_message_at'])
    
    return jsonify({
        'sessions': sessions,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': math.ceil(total / per_page) if total > 0 else 1
    })


@app.route('/admin/api/chat/conversations/<session_id>', methods=['GET'])
@admin_login_required
def admin_get_conversation_messages(session_id):
    """Get all messages for a specific conversation."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Get session info
    cursor.execute("SELECT * FROM chat_sessions WHERE session_id = %s", (session_id,))
    session_info = cursor.fetchone()
    
    if not session_info:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Conversation not found'}), 404
    
    # Get all messages
    cursor.execute("""
        SELECT * FROM chat_messages 
        WHERE session_id = %s 
        ORDER BY created_at ASC
    """, (session_id,))
    messages = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Convert datetime fields
    for m in messages:
        if m.get('created_at'):
            m['created_at'] = m['created_at'].isoformat() if hasattr(m['created_at'], 'isoformat') else str(m['created_at'])
    
    session_info['is_resolved'] = bool(session_info.get('is_resolved'))
    if session_info.get('started_at'):
        session_info['started_at'] = session_info['started_at'].isoformat() if hasattr(session_info['started_at'], 'isoformat') else str(session_info['started_at'])
    if session_info.get('last_message_at'):
        session_info['last_message_at'] = session_info['last_message_at'].isoformat() if hasattr(session_info['last_message_at'], 'isoformat') else str(session_info['last_message_at'])
    
    return jsonify({
        'session': session_info,
        'messages': messages
    })


@app.route('/admin/api/chat/conversations/<session_id>/resolve', methods=['POST'])
@admin_login_required
def admin_resolve_conversation(session_id):
    """Mark a conversation as resolved."""
    ensure_chat_tables()
    data = request.get_json() or {}
    is_resolved = bool(data.get('is_resolved', True))
    admin_notes = (data.get('admin_notes') or '').strip()[:2000]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if engine == 'postgres':
        cursor.execute("""
            UPDATE chat_sessions 
            SET is_resolved = %s, admin_notes = %s 
            WHERE session_id = %s
        """, (is_resolved, admin_notes, session_id))
    else:
        cursor.execute("""
            UPDATE chat_sessions 
            SET is_resolved = %s, admin_notes = %s 
            WHERE session_id = %s
        """, (1 if is_resolved else 0, admin_notes, session_id))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Conversation updated'})


@app.route('/admin/api/chat/conversations/<session_id>/message', methods=['POST'])
@admin_login_required
def admin_send_conversation_message(session_id):
    """Allow an admin to send a manual reply within a conversation."""
    ensure_chat_tables()
    data = request.get_json() or {}
    message = (data.get('message') or '').strip()

    if not message:
        return jsonify({'error': 'Message is required'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT id FROM chat_sessions WHERE session_id = %s", (session_id,))
        exists = cursor.fetchone()
        if not exists:
            cursor.close()
            conn.close()
            return jsonify({'error': 'Conversation not found'}), 404

        cursor.execute(
            """
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (%s, %s, %s)
            """,
            (session_id, 'admin', message)
        )

        cursor.execute(
            """
            UPDATE chat_sessions
            SET message_count = message_count + 1, last_message_at = NOW()
            WHERE session_id = %s
            """,
            (session_id,)
        )

        conn.commit()
        return jsonify({'message': 'Reply sent'})
    except Exception:
        conn.rollback()
        app.logger.exception('Failed to save admin reply for chat session %s', session_id)
        return jsonify({'error': 'Unable to send reply'}), 500
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


@app.route('/admin/api/chat/conversations/<session_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_conversation(session_id):
    """Delete a conversation and its messages."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Delete messages first
    cursor.execute("DELETE FROM chat_messages WHERE session_id = %s", (session_id,))
    # Delete session
    cursor.execute("DELETE FROM chat_sessions WHERE session_id = %s", (session_id,))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Conversation deleted'})


@app.route('/admin/api/chat/persona', methods=['GET', 'POST'])
@admin_login_required
def admin_chat_persona():
    """Get or update AI persona settings."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if request.method == 'GET':
        cursor.execute("SELECT * FROM ai_persona WHERE id = 1")
        persona = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if persona:
            persona['is_enabled'] = bool(persona.get('is_enabled'))
            return jsonify(persona)
        return jsonify({
            'persona_name': 'Assistant',
            'greeting_message': 'Hello! How can I help you today?',
            'is_enabled': True
        })
    
    # POST - update persona
    data = request.get_json() or {}
    
    persona_name = (data.get('persona_name') or 'Assistant').strip()[:100]
    greeting_message = (data.get('greeting_message') or 'Hello! How can I help you today?').strip()[:500]
    persona_description = (data.get('persona_description') or '').strip()[:1000]
    personality_traits = (data.get('personality_traits') or '').strip()[:500]
    response_style = (data.get('response_style') or 'friendly').strip()[:50]
    avatar_url = (data.get('avatar_url') or '').strip()[:500]
    contact_email = (data.get('contact_email') or 'support@sparkleclean.com').strip()[:255]
    contact_phone = (data.get('contact_phone') or '1-800-SPLK-CLEAN').strip()[:50]
    whatsapp_number = (data.get('whatsapp_number') or '+1-800-SPLK-CLEAN').strip()[:50]
    is_enabled = bool(data.get('is_enabled', True))
    
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    # Check if exists
    cursor.execute("SELECT id FROM ai_persona WHERE id = 1")
    exists = cursor.fetchone()
    
    if exists:
        if engine == 'postgres':
            cursor.execute("""
                UPDATE ai_persona 
                SET persona_name = %s, greeting_message = %s, persona_description = %s,
                    personality_traits = %s, response_style = %s, avatar_url = %s,
                    contact_email = %s, contact_phone = %s, whatsapp_number = %s,
                    is_enabled = %s, updated_at = NOW()
                WHERE id = 1
            """, (persona_name, greeting_message, persona_description, personality_traits, 
                  response_style, avatar_url, contact_email, contact_phone, whatsapp_number, is_enabled))
        else:
            cursor.execute("""
                UPDATE ai_persona 
                SET persona_name = %s, greeting_message = %s, persona_description = %s,
                    personality_traits = %s, response_style = %s, avatar_url = %s,
                    contact_email = %s, contact_phone = %s, whatsapp_number = %s,
                    is_enabled = %s
                WHERE id = 1
            """, (persona_name, greeting_message, persona_description, personality_traits, 
                  response_style, avatar_url, contact_email, contact_phone, whatsapp_number, 1 if is_enabled else 0))
    else:
        if engine == 'postgres':
            cursor.execute("""
                INSERT INTO ai_persona (id, persona_name, greeting_message, persona_description, 
                    personality_traits, response_style, avatar_url, contact_email, contact_phone, 
                    whatsapp_number, is_enabled)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (persona_name, greeting_message, persona_description, personality_traits, 
                  response_style, avatar_url, contact_email, contact_phone, whatsapp_number, is_enabled))
        else:
            cursor.execute("""
                INSERT INTO ai_persona (id, persona_name, greeting_message, persona_description, 
                    personality_traits, response_style, avatar_url, contact_email, contact_phone, 
                    whatsapp_number, is_enabled)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (persona_name, greeting_message, persona_description, personality_traits, 
                  response_style, avatar_url, contact_email, contact_phone, whatsapp_number, 1 if is_enabled else 0))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Persona settings updated'})


@app.route('/admin/api/chat/knowledge', methods=['GET', 'POST'])
@admin_login_required
def admin_knowledge_base():
    """Manage AI knowledge base entries."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if request.method == 'GET':
        cursor.execute("SELECT * FROM ai_knowledge_base ORDER BY category, id")
        entries = cursor.fetchall()
        cursor.close()
        conn.close()
        for e in entries:
            e['is_active'] = bool(e.get('is_active'))
        return jsonify(entries)
    
    # POST - create new entry
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()[:255]
    content = (data.get('content') or '').strip()[:5000]
    category = (data.get('category') or 'General').strip()[:100]
    keywords = (data.get('keywords') or '').strip()[:500]
    is_active = bool(data.get('is_active', True))
    
    if not title or not content:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Title and content are required'}), 400
    
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    if engine == 'postgres':
        cursor.execute("""
            INSERT INTO ai_knowledge_base (title, content, category, keywords, is_active)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (title, content, category, keywords, is_active))
        entry_id = cursor.fetchone()['id']
    else:
        cursor.execute("""
            INSERT INTO ai_knowledge_base (title, content, category, keywords, is_active)
            VALUES (%s, %s, %s, %s, %s)
        """, (title, content, category, keywords, 1 if is_active else 0))
        entry_id = cursor.lastrowid
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Knowledge entry created', 'id': entry_id})


@app.route('/admin/api/chat/knowledge/<int:entry_id>', methods=['PUT', 'DELETE'])
@admin_login_required
def admin_knowledge_entry(entry_id):
    """Update or delete a knowledge base entry."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'DELETE':
        cursor.execute("DELETE FROM ai_knowledge_base WHERE id = %s", (entry_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'message': 'Entry deleted'})
    
    # PUT - update
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()[:255]
    content = (data.get('content') or '').strip()[:5000]
    category = (data.get('category') or 'General').strip()[:100]
    keywords = (data.get('keywords') or '').strip()[:500]
    is_active = bool(data.get('is_active', True))
    
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    if engine == 'postgres':
        cursor.execute("""
            UPDATE ai_knowledge_base 
            SET title = %s, content = %s, category = %s, keywords = %s, is_active = %s
            WHERE id = %s
        """, (title, content, category, keywords, is_active, entry_id))
    else:
        cursor.execute("""
            UPDATE ai_knowledge_base 
            SET title = %s, content = %s, category = %s, keywords = %s, is_active = %s
            WHERE id = %s
        """, (title, content, category, keywords, 1 if is_active else 0, entry_id))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'message': 'Entry updated'})


@app.route('/admin/api/chat/stats', methods=['GET'])
@admin_login_required
def admin_chat_stats():
    """Get chat statistics for admin dashboard."""
    ensure_chat_tables()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Total conversations
    cursor.execute("SELECT COUNT(*) as total FROM chat_sessions")
    total = cursor.fetchone()['total']
    
    # Conversations today
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if engine == 'postgres':
        cursor.execute("SELECT COUNT(*) as today FROM chat_sessions WHERE started_at >= CURRENT_DATE")
    else:
        cursor.execute("SELECT COUNT(*) as today FROM chat_sessions WHERE DATE(started_at) = CURDATE()")
    today = cursor.fetchone()['today']
    
    # Unresolved conversations
    if engine == 'postgres':
        cursor.execute("SELECT COUNT(*) as unresolved FROM chat_sessions WHERE is_resolved = FALSE")
    else:
        cursor.execute("SELECT COUNT(*) as unresolved FROM chat_sessions WHERE is_resolved = 0")
    unresolved = cursor.fetchone()['unresolved']
    
    # Total messages
    cursor.execute("SELECT COUNT(*) as messages FROM chat_messages")
    messages = cursor.fetchone()['messages']
    
    cursor.close()
    conn.close()
    
    return jsonify({
        'total_conversations': total,
        'conversations_today': today,
        'unresolved': unresolved,
        'total_messages': messages
    })


@app.route('/admin/api/operating-bases', methods=['GET', 'POST'])
@admin_login_required
def admin_operating_bases():
    if request.method == 'GET':
        bases = fetch_operating_bases(include_inactive=True)
        return jsonify(bases)

    payload = request.get_json(silent=True) or {}
    try:
        base_id = payload.get('id')
        saved_id = upsert_operating_base(payload, base_id=base_id)
        bases = fetch_operating_bases(include_inactive=True)
        return jsonify({'message': 'Operating base saved.', 'id': saved_id, 'bases': bases})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to save operating base.')
        return jsonify({'error': 'Unable to save operating base right now.'}), 500


@app.route('/admin/api/operating-bases/<int:base_id>', methods=['DELETE', 'PATCH'])
@admin_login_required
def admin_operating_base_item(base_id):
    if request.method == 'DELETE':
        try:
            delete_operating_base(base_id)
            bases = fetch_operating_bases(include_inactive=True)
            return jsonify({'message': 'Operating base deleted.', 'bases': bases})
        except Exception:
            app.logger.exception('Failed to delete operating base %s', base_id)
            return jsonify({'error': 'Unable to delete operating base right now.'}), 500

    payload = request.get_json(silent=True) or {}
    try:
        if 'is_active' in payload and len(payload.keys()) == 1:
            set_operating_base_active(base_id, bool(payload.get('is_active')))
        else:
            payload['id'] = base_id
            upsert_operating_base(payload, base_id=base_id)
        bases = fetch_operating_bases(include_inactive=True)
        return jsonify({'message': 'Operating base updated.', 'bases': bases})
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        app.logger.exception('Failed to update operating base %s', base_id)
        return jsonify({'error': 'Unable to update operating base right now.'}), 500


@app.route('/admin/api/hero', methods=['GET', 'POST'])
@admin_login_required
def admin_hero():
    if request.method == 'GET':
        hero = fetch_hero_content()
        return jsonify(hero)

    current = fetch_hero_content()

    title = sanitize_text(request.form.get('title') or current.get('title'), 255)
    subtitle = sanitize_text(request.form.get('subtitle') or current.get('subtitle'), 255)
    tagline = sanitize_text(request.form.get('tagline') or current.get('tagline'), 255)
    existing_background = request.form.get('existing_background', '').strip()

    try:
        background_image = upload_hero_background(existing_background)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if background_image and existing_background and background_image != existing_background:
        delete_uploaded_file(existing_background)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE hero_content SET title=%s, subtitle=%s, background_image=%s, tagline=%s WHERE id = 1",
        (title, subtitle, background_image, tagline)
    )
    conn.commit()
    cursor.close()
    conn.close()

    hero = fetch_hero_content()
    return jsonify({'message': 'Hero content updated.', 'hero': hero})


@app.route('/admin/api/hero/badges', methods=['GET', 'POST'])
@admin_login_required
def admin_hero_badges():
    if request.method == 'GET':
        badges = fetch_hero_badges(include_inactive=True)
        return jsonify(badges)

    try:
        image_path = upload_badge_image()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    if not image_path:
        return jsonify({'error': 'Badge image is required.'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO hero_badges (image_path) VALUES (%s)", (image_path,))
    badge_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'message': 'Badge uploaded.', 'badge': {'id': badge_id, 'image_path': image_path}}), 201


@app.route('/admin/api/hero/badges/<int:badge_id>', methods=['DELETE'])
@admin_login_required
def admin_delete_hero_badge(badge_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT image_path FROM hero_badges WHERE id = %s", (badge_id,))
    badge = cursor.fetchone()
    if not badge:
        cursor.close()
        conn.close()
        return jsonify({'error': 'Badge not found.'}), 404

    image_path = badge['image_path']
    cursor.close()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM hero_badges WHERE id = %s", (badge_id,))
    conn.commit()
    cursor.close()
    conn.close()

    delete_uploaded_file(image_path)

    return jsonify({'message': 'Badge deleted.'})


@app.route('/admin/hero-content', methods=['GET', 'POST'])
@admin_login_required
def admin_hero_content_page():
    message = request.args.get('message', '').strip()
    error = request.args.get('error', '').strip()

    if request.method == 'POST':
        form_id = request.form.get('form_id', 'hero').strip()
        redirect_params = {}
        try:
            if form_id == 'hero':
                ensure_hero_content_schema()
                title = sanitize_text(request.form.get('title'), 255)
                subtitle = sanitize_text(request.form.get('subtitle'), 255)
                tagline = sanitize_text(request.form.get('tagline'), 255)
                small_text_line1 = sanitize_text(request.form.get('small_text_line1'), 255)
                small_text_line2 = sanitize_text(request.form.get('small_text_line2'), 255)
                small_text_line3 = sanitize_text(request.form.get('small_text_line3'), 255)
                stat1_text = sanitize_text(request.form.get('stat1_text'), 255)
                stat2_text = sanitize_text(request.form.get('stat2_text'), 255)
                stat3_text = sanitize_text(request.form.get('stat3_text'), 255)
                content_offset_x = sanitize_int_range(request.form.get('content_offset_x'), 0, -420, 420)
                content_offset_y = sanitize_int_range(request.form.get('content_offset_y'), 0, -220, 220)
                tagline_offset_x = sanitize_int_range(request.form.get('tagline_offset_x'), 0, -320, 320)
                tagline_offset_y = sanitize_int_range(request.form.get('tagline_offset_y'), 0, -220, 220)
                title_offset_x = sanitize_int_range(request.form.get('title_offset_x'), 0, -320, 320)
                title_offset_y = sanitize_int_range(request.form.get('title_offset_y'), 0, -220, 220)
                subtitle_offset_x = sanitize_int_range(request.form.get('subtitle_offset_x'), 0, -320, 320)
                subtitle_offset_y = sanitize_int_range(request.form.get('subtitle_offset_y'), 0, -220, 220)
                meta_offset_x = sanitize_int_range(request.form.get('meta_offset_x'), 0, -320, 320)
                meta_offset_y = sanitize_int_range(request.form.get('meta_offset_y'), 0, -220, 220)
                card1_offset_x = sanitize_int_range(request.form.get('card1_offset_x'), 0, -220, 220)
                card1_offset_y = sanitize_int_range(request.form.get('card1_offset_y'), 0, -220, 220)
                card2_offset_x = sanitize_int_range(request.form.get('card2_offset_x'), 0, -220, 220)
                card2_offset_y = sanitize_int_range(request.form.get('card2_offset_y'), 0, -220, 220)
                card3_offset_x = sanitize_int_range(request.form.get('card3_offset_x'), 0, -220, 220)
                card3_offset_y = sanitize_int_range(request.form.get('card3_offset_y'), 0, -220, 220)
                tagline_bg_color = sanitize_css_color(request.form.get('tagline_bg_color'), '#16a34a')
                tagline_text_color = sanitize_css_color(request.form.get('tagline_text_color'), '#ffffff')
                title_color = sanitize_css_color(request.form.get('title_color'), '#2563eb')
                title_size_px = sanitize_int_range(request.form.get('title_size_px'), 72, 34, 84)
                title_weight = sanitize_int_range(request.form.get('title_weight'), 800, 400, 900)
                subtitle_color = sanitize_css_color(request.form.get('subtitle_color'), '#ffffff')
                subtitle_size_px = sanitize_int_range(request.form.get('subtitle_size_px'), 18, 14, 24)
                subtitle_weight = sanitize_int_range(request.form.get('subtitle_weight'), 600, 300, 900)
                content_bg_color = sanitize_css_color(request.form.get('content_bg_color'), '', allow_empty=True)
                meta_text_color = sanitize_css_color(request.form.get('meta_text_color'), '#ffffff')
                meta_bg_color = sanitize_css_color(request.form.get('meta_bg_color'), '#0f172a')
                existing_background = request.form.get('existing_background', '').strip()
                remove_background = str_to_bool(request.form.get('remove_background', 'false'))

                background_file = request.files.get('background_image')
                has_new_background = bool(background_file and background_file.filename)

                try:
                    background_image = upload_hero_background(existing_background)
                except ValueError as exc:
                    raise ValueError(str(exc))

                if remove_background and not has_new_background:
                    background_image = ''

                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE hero_content SET
                        title=%s,
                        subtitle=%s,
                        tagline=%s,
                        small_text_line1=%s,
                        small_text_line2=%s,
                        small_text_line3=%s,
                        stat1_text=%s,
                        stat2_text=%s,
                        stat3_text=%s,
                        hero_background_image=%s,
                        content_offset_x=%s,
                        content_offset_y=%s,
                        tagline_offset_x=%s,
                        tagline_offset_y=%s,
                        title_offset_x=%s,
                        title_offset_y=%s,
                        subtitle_offset_x=%s,
                        subtitle_offset_y=%s,
                        meta_offset_x=%s,
                        meta_offset_y=%s,
                        card1_offset_x=%s,
                        card1_offset_y=%s,
                        card2_offset_x=%s,
                        card2_offset_y=%s,
                        card3_offset_x=%s,
                        card3_offset_y=%s,
                        tagline_bg_color=%s,
                        tagline_text_color=%s,
                        title_color=%s,
                        title_size_px=%s,
                        title_weight=%s,
                        subtitle_color=%s,
                        subtitle_size_px=%s,
                        subtitle_weight=%s,
                        content_bg_color=%s,
                        meta_text_color=%s,
                        meta_bg_color=%s
                    WHERE id = 1
                    """,
                    (
                        title,
                        subtitle,
                        tagline,
                        small_text_line1,
                        small_text_line2,
                        small_text_line3,
                        stat1_text,
                        stat2_text,
                        stat3_text,
                        background_image or None,
                        content_offset_x,
                        content_offset_y,
                        tagline_offset_x,
                        tagline_offset_y,
                        title_offset_x,
                        title_offset_y,
                        subtitle_offset_x,
                        subtitle_offset_y,
                        meta_offset_x,
                        meta_offset_y,
                        card1_offset_x,
                        card1_offset_y,
                        card2_offset_x,
                        card2_offset_y,
                        card3_offset_x,
                        card3_offset_y,
                        tagline_bg_color,
                        tagline_text_color,
                        title_color,
                        title_size_px,
                        title_weight,
                        subtitle_color,
                        subtitle_size_px,
                        subtitle_weight,
                        content_bg_color or None,
                        meta_text_color,
                        meta_bg_color
                    )
                )
                conn.commit()
                cursor.close()
                conn.close()

                if has_new_background and existing_background and background_image and background_image != existing_background:
                    delete_uploaded_file(existing_background)
                elif remove_background and not has_new_background and existing_background:
                    delete_uploaded_file(existing_background)

                redirect_params['message'] = 'Hero content updated.'

            elif form_id == 'badge_upload':
                try:
                    image_path = upload_badge_image()
                except ValueError as exc:
                    raise ValueError(str(exc))

                if not image_path:
                    raise ValueError('Badge image is required.')

                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO hero_badges (image_path) VALUES (%s)", (image_path,))
                conn.commit()
                cursor.close()
                conn.close()

                redirect_params['message'] = 'Badge uploaded.'

            elif form_id == 'badge_delete':
                try:
                    badge_id = int(request.form.get('badge_id'))
                except (TypeError, ValueError):
                    raise ValueError('Invalid badge selection.')

                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT image_path FROM hero_badges WHERE id = %s", (badge_id,))
                badge = cursor.fetchone()
                if not badge:
                    cursor.close()
                    conn.close()
                    raise ValueError('Badge not found.')

                image_path = badge['image_path']
                cursor.close()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM hero_badges WHERE id = %s", (badge_id,))
                conn.commit()
                cursor.close()
                conn.close()
                delete_uploaded_file(image_path)
                redirect_params['message'] = 'Badge deleted.'

            else:
                raise ValueError('Unsupported action.')

        except ValueError as exc:
            app.logger.info('Hero content update validation error: %s', exc)
            redirect_params['error'] = normalize_message(exc)
        except Exception:
            app.logger.exception('Failed to update hero content')
            redirect_params['error'] = 'Unable to process that request right now.'

        return redirect(url_for('admin_hero_content_page', **redirect_params))

    hero = fetch_hero_content()
    badges = fetch_hero_badges(include_inactive=True)
    site_settings = fetch_site_settings()
    homepage_sections = fetch_home_page_sections()

    return render_template(
        'admin/hero_content.html',
        hero=hero,
        hero_badges=badges,
        homepage_sections=homepage_sections,
        site_settings=site_settings,
        message=message,
        error=error
    )




@app.route('/admin/quote-section', methods=['GET', 'POST'])
@admin_login_required
def admin_quote_section():
    message = request.args.get('message', '').strip()
    error = request.args.get('error', '').strip()

    if request.method == 'POST':
        redirect_params = {}
        try:
            headline = sanitize_text(request.form.get('headline'), 255)
            phone = sanitize_text(request.form.get('phone'), 50)
            email = sanitize_text(request.form.get('email'), 100)
            operating_hours = sanitize_text(request.form.get('operating_hours'))
            service_areas = sanitize_text(request.form.get('service_areas'))
            existing_background = request.form.get('existing_background', '').strip()
            remove_background = str_to_bool(request.form.get('remove_background', 'false'))

            if not headline or not phone or not email:
                raise ValueError('Headline, phone, and email are required.')

            background_file = request.files.get('quote_background')
            has_new_background = bool(background_file and background_file.filename)

            try:
                background_path = upload_quote_background(existing_background or '')
            except ValueError as exc:
                raise ValueError(str(exc))

            if remove_background and not has_new_background:
                background_path = ''

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE contact_info SET headline=%s, phone=%s, email=%s, operating_hours=%s, service_areas=%s, quote_background=%s WHERE id = 1",
                (
                    headline,
                    phone,
                    email,
                    operating_hours,
                    service_areas,
                    background_path or None
                )
            )
            conn.commit()
            cursor.close()
            conn.close()

            if has_new_background and existing_background and background_path and background_path != existing_background:
                delete_uploaded_file(existing_background)
            elif remove_background and not has_new_background and existing_background:
                delete_uploaded_file(existing_background)

            redirect_params['message'] = 'Contact information updated.'

        except ValueError as exc:
            app.logger.info('Quote section validation error: %s', exc)
            redirect_params['error'] = normalize_message(exc)
        except Exception:
            app.logger.exception('Failed to update quote section')
            redirect_params['error'] = 'Unable to process that request right now.'

        return redirect(url_for('admin_quote_section', **redirect_params))

    contact_info = fetch_contact_info()
    site_settings = fetch_site_settings()
    return render_template(
        'admin/quote_section.html',
        contact_info=contact_info,
        site_settings=site_settings,
        message=message,
        error=error
    )


@app.route('/admin/footer-settings', methods=['GET', 'POST'])
@admin_login_required
def admin_footer_settings():
    message = request.args.get('message', '').strip()
    error = request.args.get('error', '').strip()
    footer = fetch_footer_info()
    site_settings = fetch_site_settings()

    if request.method == 'POST':
        redirect_params = {}
        try:
            phone = sanitize_text(request.form.get('footer_phone'), 50)
            email = sanitize_text(request.form.get('footer_email'), 100)
            location = sanitize_text(request.form.get('footer_location'), 255)
            facebook = request.form.get('social_facebook', '').strip()
            instagram = request.form.get('social_instagram', '').strip()
            twitter = request.form.get('social_twitter', '').strip()

            if not phone or not email or not location:
                raise ValueError('Footer phone, email, and location are required.')

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE footer_info SET phone=%s, email=%s, location=%s, facebook=%s, instagram=%s, twitter=%s WHERE id = 1",
                (phone, email, location, facebook, instagram, twitter)
            )
            conn.commit()
            cursor.close()
            conn.close()

            redirect_params['message'] = 'Footer settings updated.'

        except ValueError as exc:
            app.logger.info('Footer settings validation error: %s', exc)
            redirect_params['error'] = normalize_message(exc)
        except Exception:
            app.logger.exception('Failed to update footer settings')
            redirect_params['error'] = 'Unable to process that request right now.'

        return redirect(url_for('admin_footer_settings', **redirect_params))

    return render_template(
        'admin/footer_settings.html',
        footer=footer,
        site_settings=site_settings,
        message=message,
        error=error
    )


@app.route('/admin/telegram', methods=['GET', 'POST'])
@admin_login_required
def admin_telegram_settings():
    site_settings = fetch_site_settings()
    telegram_settings = fetch_telegram_settings()
    success_message = None
    error_message = None

    if request.method == 'POST':
        form_values = request.form.to_dict()
        try:
            telegram_settings = update_telegram_settings(form_values)
            success_message = 'Telegram settings updated successfully.'
        except ValueError as exc:
            error_message = str(exc)
            telegram_settings = {
                'id': telegram_settings.get('id', 1),
                'bot_token': sanitize_text(form_values.get('bot_token'), 255),
                'chat_id': sanitize_text(form_values.get('chat_id'), 128),
                'is_active': str_to_bool(form_values.get('is_active')),
                'notify_email_success': str_to_bool(form_values.get('notify_email_success')),
                'notify_email_error': str_to_bool(form_values.get('notify_email_error')),
                'notify_admin_login': str_to_bool(form_values.get('notify_admin_login')),
                'notify_login_failure': str_to_bool(form_values.get('notify_login_failure')),
                'notify_error_logs': str_to_bool(form_values.get('notify_error_logs'))
            }
        except Exception:
            app.logger.exception('Failed to update Telegram settings.')
            error_message = 'An unexpected error occurred while updating Telegram settings.'
            telegram_settings = fetch_telegram_settings()

    return render_template(
        'admin/telegram_settings.html',
        site_settings=site_settings,
        telegram_settings=telegram_settings,
        success_message=success_message,
        error_message=error_message
    )


@app.route('/admin/branding', methods=['GET', 'POST'])
@admin_login_required
def admin_brand_settings():
    message = request.args.get('message', '').strip()
    error = request.args.get('error', '').strip()
    site_settings = fetch_site_settings()

    if request.method == 'POST':
        redirect_params = {}
        try:
            company_name = sanitize_text(request.form.get('company_name'), 255)
            existing_logo = (request.form.get('existing_logo') or site_settings.get('logo_path') or '').strip()
            remove_logo = str_to_bool(request.form.get('remove_logo', 'false'))
            logo_file = request.files.get('logo')
            has_new_logo = bool(logo_file and logo_file.filename)

            if not company_name:
                raise ValueError('Company name is required.')

            try:
                logo_path = upload_brand_logo(existing_logo)
            except ValueError as exc:
                raise ValueError(str(exc))

            if remove_logo and not has_new_logo:
                logo_path = ''

            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE site_settings SET company_name=%s, logo_path=%s WHERE id = 1",
                (
                    company_name,
                    logo_path or None
                )
            )
            conn.commit()
            cursor.close()
            conn.close()

            if has_new_logo and existing_logo and logo_path and logo_path != existing_logo:
                delete_uploaded_file(existing_logo)
            elif remove_logo and not has_new_logo and existing_logo:
                delete_uploaded_file(existing_logo)

            redirect_params['message'] = 'Brand settings updated.'

        except ValueError as exc:
            app.logger.info('Brand settings validation error: %s', exc)
            redirect_params['error'] = normalize_message(exc)
        except Exception:
            app.logger.exception('Failed to update brand settings')
            redirect_params['error'] = 'Unable to process that request right now.'

        return redirect(url_for('admin_brand_settings', **redirect_params))

    site_settings = fetch_site_settings()
    return render_template(
        'admin/branding.html',
        site_settings=site_settings,
        message=message,
        error=error
    )


@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))

    error = ''
    site_settings = fetch_site_settings()
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        remote_ip = request.remote_addr or 'unknown'
        user_agent = sanitize_text(request.headers.get('User-Agent'), 150) or 'unknown'
        sanitized_username = sanitize_text(username, 64) if username else ''
        failure_reason = None

        if not username or not password:
            error = 'Username and password are required.'
            failure_reason = 'missing_credentials'
        else:
            user = fetch_admin_user(username)
            if user and user.get('password_hash') and check_password_hash(user['password_hash'], password):
                session.clear()
                session['admin_logged_in'] = True
                session['admin_username'] = user.get('username')
                session.permanent = False
                send_telegram_notification(
                    'notify_admin_login',
                    [
                        '[Admin] Login success',
                        f'Username: {user.get("username")}',
                        f'IP: {remote_ip}',
                        f'Agent: {user_agent}'
                    ]
                )
                return redirect(url_for('admin_dashboard'))
            else:
                error = 'Invalid username or password.'
                failure_reason = 'invalid_credentials'

        if failure_reason:
            details = [
                '[Admin] Login failure',
                f'Username: {sanitized_username or "(empty)"}',
                f'IP: {remote_ip}',
                f'Agent: {user_agent}'
            ]
            if failure_reason == 'missing_credentials':
                details.append('Reason: Missing username or password.')
            send_telegram_notification('notify_login_failure', details)

    return render_template('admin_login.html', error=error, site_settings=site_settings)


@app.route('/admin/logout', methods=['POST', 'GET'])
def admin_logout():
    username = session.get('admin_username')
    was_logged_in = session.get('admin_logged_in')

    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    session.permanent = False

    if was_logged_in and username:
        send_telegram_notification(
            'notify_admin_login',
            [
                '[Admin] Logout',
                f'Username: {sanitize_text(username, 64)}',
                f'IP: {request.remote_addr or "unknown"}'
            ]
        )

    if request.method == 'POST':
        prefers_json = request.is_json
        if not prefers_json:
            best = request.accept_mimetypes.best
            prefers_json = (
                best == 'application/json'
                and request.accept_mimetypes[best] > request.accept_mimetypes['text/html']
            )
        if prefers_json:
            return jsonify({'message': 'Logged out.'})
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@admin_login_required
def admin_dashboard():
    site_settings = fetch_site_settings()
    return render_template('admin/dashboard.html', site_settings=site_settings)


@app.route('/dashboard')
def legacy_dashboard():
    return redirect(url_for('admin_dashboard'))


@app.route('/admin/bookings')
@admin_login_required
def admin_bookings():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM bookings ORDER BY created_at DESC")
    bookings = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(bookings)


@app.route('/admin/applications')
@admin_login_required
def admin_applications():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM job_applications ORDER BY created_at DESC")
    applications = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(applications)


@app.route('/privacy-policy')
def privacy_policy():
    site_settings = {}
    try:
        site_settings = fetch_site_settings() or {}
    except Exception:
        pass
    return render_template('privacy_policy.html', site_settings=site_settings)


@app.route('/terms-of-service')
def terms_of_service():
    site_settings = {}
    try:
        site_settings = fetch_site_settings() or {}
        content = fetch_site_content() or {}
        tos_data = content.get('terms_of_service')
        if isinstance(tos_data, dict):
            site_settings['_terms_content'] = tos_data.get('html', '')
        elif isinstance(tos_data, str):
            site_settings['_terms_content'] = tos_data
    except Exception:
        pass
    return render_template('terms_of_service.html', site_settings=site_settings)


@app.route('/cookie-policy')
def cookie_policy():
    site_settings = {}
    try:
        site_settings = fetch_site_settings() or {}
    except Exception:
        pass
    return render_template('cookie_policy.html', site_settings=site_settings)


# ============================================================
# BLOG
# ============================================================

_done_ensure_blog_table = False

def ensure_blog_table():
    global _done_ensure_blog_table
    if _done_ensure_blog_table:
        return
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    if engine == 'postgres':
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blog_posts (
                id BIGSERIAL PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                slug VARCHAR(255) NOT NULL UNIQUE,
                excerpt TEXT,
                content TEXT NOT NULL,
                image_path VARCHAR(512),
                image_alt VARCHAR(255),
                meta_description VARCHAR(320),
                tags VARCHAR(512),
                is_published BOOLEAN DEFAULT FALSE,
                published_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blog_posts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                title VARCHAR(255) NOT NULL,
                slug VARCHAR(255) NOT NULL UNIQUE,
                excerpt TEXT,
                content TEXT NOT NULL,
                image_path VARCHAR(512),
                image_alt VARCHAR(255),
                meta_description VARCHAR(320),
                tags VARCHAR(512),
                is_published TINYINT(1) DEFAULT 0,
                published_at DATETIME,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    cursor.close()
    conn.close()
    _done_ensure_blog_table = True


def _blog_slugify(text):
    import re as _re
    text = (text or '').lower().strip()
    text = _re.sub(r'[^\w\s-]', '', text)
    text = _re.sub(r'[\s_-]+', '-', text)
    return text[:120]


def _blog_unique_slug(base_slug, exclude_id=None):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    slug = base_slug
    n = 1
    while True:
        if exclude_id:
            cursor.execute("SELECT id FROM blog_posts WHERE slug = %s AND id != %s", (slug, exclude_id))
        else:
            cursor.execute("SELECT id FROM blog_posts WHERE slug = %s", (slug,))
        if not cursor.fetchone():
            break
        slug = f"{base_slug}-{n}"
        n += 1
    cursor.close()
    conn.close()
    return slug


@app.route('/blog')
def blog_index():
    ensure_blog_table()
    site_settings = fetch_site_settings() or {}
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if engine == 'postgres':
        cursor.execute("SELECT id,title,slug,excerpt,image_path,image_alt,tags,published_at FROM blog_posts WHERE is_published = TRUE ORDER BY published_at DESC, id DESC")
    else:
        cursor.execute("SELECT id,title,slug,excerpt,image_path,image_alt,tags,published_at FROM blog_posts WHERE is_published = 1 ORDER BY published_at DESC, id DESC")
    posts = cursor.fetchall() or []
    cursor.close()
    conn.close()
    return render_template('blog_index.html', posts=posts, site_settings=site_settings)


@app.route('/blog/<slug>')
def blog_post(slug):
    ensure_blog_table()
    site_settings = fetch_site_settings() or {}
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    if engine == 'postgres':
        cursor.execute("SELECT * FROM blog_posts WHERE slug = %s AND is_published = TRUE", (slug,))
    else:
        cursor.execute("SELECT * FROM blog_posts WHERE slug = %s AND is_published = 1", (slug,))
    post = cursor.fetchone()
    cursor.close()
    conn.close()
    if not post:
        abort(404)
    return render_template('blog_post.html', post=post, site_settings=site_settings)


# Admin blog routes
@app.route('/admin/blog')
@admin_login_required
def admin_blog_page():
    ensure_blog_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id,title,slug,is_published,published_at,created_at FROM blog_posts ORDER BY id DESC")
    posts = cursor.fetchall() or []
    cursor.close()
    conn.close()
    site_settings = fetch_site_settings() or {}
    return render_template('admin/blog.html', posts=posts, site_settings=site_settings)


@app.route('/admin/api/blog/posts', methods=['GET'])
@admin_login_required
def admin_api_blog_list():
    ensure_blog_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id,title,slug,is_published,tags,published_at,created_at FROM blog_posts ORDER BY id DESC")
    posts = cursor.fetchall() or []
    cursor.close()
    conn.close()
    for p in posts:
        p['is_published'] = bool(p.get('is_published'))
        for k in ('published_at', 'created_at'):
            if p.get(k):
                p[k] = p[k].isoformat() if hasattr(p[k], 'isoformat') else str(p[k])
    return jsonify({'posts': posts})


@app.route('/admin/api/blog/posts', methods=['POST'])
@admin_login_required
def admin_api_blog_create():
    ensure_blog_table()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    title = sanitize_text(request.form.get('title'), 255)
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    slug = _blog_unique_slug(_blog_slugify(request.form.get('slug') or title))
    excerpt = (request.form.get('excerpt') or '').strip()[:500]
    content = (request.form.get('content') or '').strip()
    image_alt = sanitize_text(request.form.get('image_alt'), 255) or title
    meta_description = (request.form.get('meta_description') or excerpt or '')[:320]
    tags = sanitize_text(request.form.get('tags'), 512)
    is_published = bool(str_to_bool(request.form.get('is_published', '0')))

    # Handle image upload
    image_path = None
    img_file = request.files.get('image')
    if img_file and img_file.filename:
        try:
            from werkzeug.utils import secure_filename as _sf
            import os as _os
            ext = _os.path.splitext(_sf(img_file.filename))[1].lower()
            fname = f"blog_{slug}{ext}"
            upload_dir = _os.path.join(app.root_path, 'static', 'uploads', 'blog')
            _os.makedirs(upload_dir, exist_ok=True)
            img_file.save(_os.path.join(upload_dir, fname))
            image_path = f"static/uploads/blog/{fname}"
        except Exception:
            app.logger.exception('Blog image upload failed')

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    now = datetime.utcnow()
    if engine == 'postgres':
        published_at = now if is_published else None
        cursor.execute(
            "INSERT INTO blog_posts (title,slug,excerpt,content,image_path,image_alt,meta_description,tags,is_published,published_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (title, slug, excerpt, content, image_path, image_alt, meta_description, tags, is_published, published_at)
        )
        row = cursor.fetchone()
        new_id = row['id'] if row else None
    else:
        published_at = now if is_published else None
        cursor.execute(
            "INSERT INTO blog_posts (title,slug,excerpt,content,image_path,image_alt,meta_description,tags,is_published,published_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (title, slug, excerpt, content, image_path, image_alt, meta_description, tags, int(is_published), published_at)
        )
        new_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'id': new_id, 'slug': slug})


@app.route('/admin/api/blog/posts/<int:post_id>', methods=['GET'])
@admin_login_required
def admin_api_blog_get(post_id):
    ensure_blog_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM blog_posts WHERE id = %s", (post_id,))
    post = cursor.fetchone()
    cursor.close()
    conn.close()
    if not post:
        return jsonify({'error': 'Not found'}), 404
    post['is_published'] = bool(post.get('is_published'))
    for k in ('published_at', 'created_at', 'updated_at'):
        if post.get(k):
            post[k] = post[k].isoformat() if hasattr(post[k], 'isoformat') else str(post[k])
    return jsonify(post)


@app.route('/admin/api/blog/posts/<int:post_id>', methods=['PUT'])
@admin_login_required
def admin_api_blog_update(post_id):
    ensure_blog_table()
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    title = sanitize_text(request.form.get('title'), 255)
    if not title:
        return jsonify({'error': 'Title is required'}), 400
    slug = _blog_unique_slug(_blog_slugify(request.form.get('slug') or title), exclude_id=post_id)
    excerpt = (request.form.get('excerpt') or '').strip()[:500]
    content = (request.form.get('content') or '').strip()
    image_alt = sanitize_text(request.form.get('image_alt'), 255) or title
    meta_description = (request.form.get('meta_description') or excerpt or '')[:320]
    tags = sanitize_text(request.form.get('tags'), 512)
    is_published = bool(str_to_bool(request.form.get('is_published', '0')))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Existing image handling
    cursor.execute("SELECT image_path, published_at FROM blog_posts WHERE id = %s", (post_id,))
    existing = cursor.fetchone() or {}
    image_path = existing.get('image_path')
    existing_published_at = existing.get('published_at')

    img_file = request.files.get('image')
    if img_file and img_file.filename:
        try:
            from werkzeug.utils import secure_filename as _sf
            import os as _os
            ext = _os.path.splitext(_sf(img_file.filename))[1].lower()
            fname = f"blog_{slug}{ext}"
            upload_dir = _os.path.join(app.root_path, 'static', 'uploads', 'blog')
            _os.makedirs(upload_dir, exist_ok=True)
            img_file.save(_os.path.join(upload_dir, fname))
            image_path = f"static/uploads/blog/{fname}"
        except Exception:
            app.logger.exception('Blog image upload failed')

    now = datetime.utcnow()
    published_at = existing_published_at if existing_published_at else (now if is_published else None)
    if is_published and not existing_published_at:
        published_at = now

    if engine == 'postgres':
        cursor.execute(
            "UPDATE blog_posts SET title=%s,slug=%s,excerpt=%s,content=%s,image_path=%s,image_alt=%s,meta_description=%s,tags=%s,is_published=%s,published_at=%s,updated_at=NOW() WHERE id=%s",
            (title, slug, excerpt, content, image_path, image_alt, meta_description, tags, is_published, published_at, post_id)
        )
    else:
        cursor.execute(
            "UPDATE blog_posts SET title=%s,slug=%s,excerpt=%s,content=%s,image_path=%s,image_alt=%s,meta_description=%s,tags=%s,is_published=%s,published_at=%s,updated_at=NOW() WHERE id=%s",
            (title, slug, excerpt, content, image_path, image_alt, meta_description, tags, int(is_published), published_at, post_id)
        )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True, 'slug': slug})


@app.route('/admin/api/blog/posts/<int:post_id>', methods=['DELETE'])
@admin_login_required
def admin_api_blog_delete(post_id):
    ensure_blog_table()
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("DELETE FROM blog_posts WHERE id = %s", (post_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/')
def index():
    services = []
    one_time_services = []
    contract_services = []
    job_positions = []
    testimonials = []
    hero_content = {}
    hero_badges = []
    contact_info = {}
    footer_info = {}
    site_settings = {}
    travel_settings = {}
    operating_bases = []
    site_content = {}
    home_section_order = [item['section_key'] for item in DEFAULT_HOME_PAGE_SECTIONS]

    try:
        maybe_process_due_contract_reminders()
    except Exception:
        app.logger.exception('Failed background contract reminder pass on homepage')

    # Run one-time migration of domestic cleaning data into services table
    try:
        migrate_domestic_to_services()
    except Exception:
        app.logger.exception('Error during domestic-to-services migration')

    try:
        services = fetch_services_from_db()
        one_time_services = [svc for svc in services if not svc.get('is_contract') or svc.get('service_category') == 'hybrid']
        contract_services = [svc for svc in services if svc.get('is_contract') and svc.get('service_category') != 'hybrid']
    except Exception:
        app.logger.exception('Error fetching services for index page')

    try:
        job_positions = fetch_job_positions_from_db()
    except Exception:
        app.logger.exception('Error fetching job positions for index page')

    try:
        testimonials = fetch_testimonials_from_db(shuffle=True)
    except Exception:
        app.logger.exception('Error fetching testimonials for index page')

    try:
        hero_content = fetch_hero_content()
    except Exception:
        app.logger.exception('Error fetching hero content for index page')

    try:
        hero_badges = fetch_hero_badges()
    except Exception:
        app.logger.exception('Error fetching hero badges for index page')

    try:
        contact_info = fetch_contact_info()
    except Exception:
        app.logger.exception('Error fetching contact info for index page')

    try:
        footer_info = fetch_footer_info()
    except Exception:
        app.logger.exception('Error fetching footer info for index page')

    try:
        site_settings = fetch_site_settings()
    except Exception:
        app.logger.exception('Error fetching site settings for index page')

    try:
        travel_settings = fetch_travel_settings()
    except Exception:
        app.logger.exception('Error fetching travel settings for index page')

    try:
        operating_bases = fetch_operating_bases(include_inactive=False)
        # If any bases are missing coordinates, geocode them in the background
        # so this request isn't blocked. On subsequent loads the cached coords are used.
        needs_geocode = any(b.get('latitude') is None or b.get('longitude') is None for b in operating_bases)
        if needs_geocode:
            import threading
            threading.Thread(target=geocode_bases_if_needed, args=(list(operating_bases),), daemon=True).start()
    except Exception:
        app.logger.exception('Error fetching operating bases for index page')

    try:
        site_content = fetch_site_content()
    except Exception:
        app.logger.exception('Error fetching site content for index page')

    try:
        ordered_sections = fetch_home_page_sections()
        if ordered_sections:
            home_section_order = [row.get('section_key') for row in ordered_sections if row.get('section_key')]
    except Exception:
        app.logger.exception('Error fetching homepage section order for index page')

    # Fetch active FAQs
    try:
        ensure_faq_table()
    except Exception:
        app.logger.exception('Error ensuring FAQ table for index page')
    faqs = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        if engine == 'postgres':
            cursor.execute("SELECT id, question, answer, category FROM faqs WHERE is_active = TRUE ORDER BY sort_order ASC, id ASC")
        else:
            cursor.execute("SELECT id, question, answer, category FROM faqs WHERE is_active = 1 ORDER BY sort_order ASC, id ASC")
        faqs = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception:
        app.logger.exception('Error fetching FAQs for index page')

    # Fetch active policies
    policies = []
    try:
        policies = fetch_policies_from_db(include_inactive=False)
    except Exception:
        app.logger.exception('Error fetching policies for index page')

    hero_small_texts = [
        text for text in (
            hero_content.get('small_text_line1'),
            hero_content.get('small_text_line2'),
            hero_content.get('small_text_line3')
        ) if text
    ]

    hero_stat_cards = []
    for raw_stat in (
        hero_content.get('stat1_text'),
        hero_content.get('stat2_text'),
        hero_content.get('stat3_text')
    ):
        if not raw_stat:
            continue
        parts = [segment.strip() for segment in raw_stat.split('·') if segment and segment.strip()]
        if not parts:
            continue
        hero_stat_cards.append({
            'primary': parts[0],
            'secondary': parts[1] if len(parts) > 1 else '',
            'tertiary': parts[2] if len(parts) > 2 else '',
            'raw': raw_stat.strip()
        })

    try:
        log_analytics_event('homepage_visit', {
            'ip': request.remote_addr
        })
    except Exception:
        app.logger.exception('Error logging homepage analytics event')

    payment_settings = fetch_payment_settings()
    prebook_discount_enabled = bool(payment_settings.get('prebook_discount_enabled', True))
    try:
        prebook_discount_percent = float(payment_settings.get('prebook_discount_percent', PREBOOK_DISCOUNT_PERCENT))
    except (TypeError, ValueError):
        prebook_discount_percent = float(PREBOOK_DISCOUNT_PERCENT)
    prebook_discount_percent = max(0.0, min(100.0, prebook_discount_percent))

    return render_template(
        'index.html',
        services=services,
        one_time_services=one_time_services,
        contract_services=contract_services,
        job_positions=job_positions,
        testimonials=testimonials,
        hero=hero_content,
        hero_badges=hero_badges,
        hero_small_texts=hero_small_texts,
        hero_stat_cards=hero_stat_cards,
        contact_info=contact_info,
        footer_info=footer_info,
        site_settings=site_settings,
        travel_settings=travel_settings,
        operating_bases=operating_bases,
        site_content=site_content,
        faqs=faqs,
        policies=policies,
        home_section_order=home_section_order,
        prebook_discount_enabled=prebook_discount_enabled,
        prebook_discount_percent=prebook_discount_percent
    )


@app.route('/services')
def services_page():
    services = []
    one_time_services = []
    contract_services = []
    site_settings = {}

    try:
        maybe_process_due_contract_reminders()
    except Exception:
        app.logger.exception('Failed background contract reminder pass on services page')

    try:
        services = fetch_services_from_db(include_inactive=False)
        one_time_services = [svc for svc in services if not svc.get('is_contract') or svc.get('service_category') == 'hybrid']
        contract_services = [svc for svc in services if svc.get('is_contract') and svc.get('service_category') != 'hybrid']
    except Exception:
        app.logger.exception('Error fetching services for services page')

    try:
        site_settings = fetch_site_settings()
    except Exception:
        app.logger.exception('Error fetching site settings for services page')

    footer_info = {}
    try:
        footer_info = fetch_footer_info() or {}
    except Exception:
        app.logger.exception('Error fetching footer info for services page')

    return render_template(
        'services.html',
        services=services,
        one_time_services=one_time_services,
        contract_services=contract_services,
        site_settings=site_settings,
        footer_info=footer_info
    )


@app.route('/services/<int:service_id>')
def service_detail_page(service_id):
    site_settings = {}
    service = None
    domestic_cleaning = {}

    try:
        services = fetch_services_from_db(include_inactive=False)
        service = next((svc for svc in services if int(svc.get('id')) == int(service_id)), None)
    except Exception:
        app.logger.exception('Error fetching service detail for %s', service_id)

    if not service:
        return redirect(url_for('services_page'))

    try:
        site_settings = fetch_site_settings()
    except Exception:
        app.logger.exception('Error fetching site settings for service detail page')

    try:
        domestic_cleaning = fetch_domestic_cleaning_data(include_inactive=False)
    except Exception:
        app.logger.exception('Error fetching domestic flow data for service detail page')

    footer_info = {}
    try:
        footer_info = fetch_footer_info() or {}
    except Exception:
        app.logger.exception('Error fetching footer info for service detail page')

    return render_template(
        'service_detail.html',
        service=service,
        site_settings=site_settings,
        domestic_cleaning=domestic_cleaning,
        footer_info=footer_info
    )


@app.route('/company-info')
def company_info():
    return redirect(url_for('index') + '#who-we-are')


def _public_base_url():
    configured = (app.config.get('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    if configured:
        return configured

    forwarded_proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip()
    forwarded_host = (request.headers.get('X-Forwarded-Host') or '').split(',')[0].strip()
    host = forwarded_host or request.host
    scheme = forwarded_proto or request.scheme or 'https'

    if scheme not in ('http', 'https'):
        scheme = 'https'

    return f"{scheme}://{host}".rstrip('/')


def _public_url(path):
    base = _public_base_url()
    normalized_path = '/' + str(path or '').lstrip('/')
    return f"{base}{normalized_path}"


@app.route('/sitemap.xml', methods=['GET'])
def sitemap_xml():
    home_url = _public_url(url_for('index'))
    services_url = _public_url(url_for('services_page'))
    base_urls = [
        {'loc': home_url, 'lastmod': None, 'changefreq': 'daily', 'priority': '1.0'},
        {'loc': services_url, 'lastmod': None, 'changefreq': 'daily', 'priority': '0.9'},
    ]

    service_urls = []
    try:
        services = fetch_services_from_db(include_inactive=False)
        for service in services or []:
            service_id = service.get('id')
            if not service_id:
                continue
            updated_value = service.get('updated_at') or service.get('created_at')
            lastmod = None
            if isinstance(updated_value, datetime):
                lastmod = updated_value.date().isoformat()
            elif isinstance(updated_value, str):
                lastmod = updated_value.split('T')[0].split(' ')[0]
            service_urls.append(
                {
                    'loc': _public_url(url_for('service_detail_page', service_id=int(service_id))),
                    'lastmod': lastmod,
                    'changefreq': 'weekly',
                    'priority': '0.8'
                }
            )
    except Exception:
        app.logger.exception('Failed to build dynamic service URLs for sitemap.xml')

    # Add blog posts to sitemap
    blog_urls = []
    try:
        ensure_blog_table()
        _engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
        _conn = get_db_connection()
        _cur = _conn.cursor(dictionary=True)
        if _engine == 'postgres':
            _cur.execute("SELECT slug, updated_at FROM blog_posts WHERE is_published = TRUE ORDER BY id DESC")
        else:
            _cur.execute("SELECT slug, updated_at FROM blog_posts WHERE is_published = 1 ORDER BY id DESC")
        _posts = _cur.fetchall() or []
        _cur.close()
        _conn.close()
        blog_index_url = _public_url(url_for('blog_index'))
        blog_urls.append({'loc': blog_index_url, 'lastmod': None, 'changefreq': 'daily', 'priority': '0.8'})
        for _p in _posts:
            _dt = _p.get('updated_at')
            _lastmod = _dt.date().isoformat() if isinstance(_dt, datetime) else (str(_dt).split('T')[0].split(' ')[0] if _dt else None)
            blog_urls.append({
                'loc': _public_url(url_for('blog_post', slug=_p['slug'])),
                'lastmod': _lastmod,
                'changefreq': 'monthly',
                'priority': '0.7'
            })
    except Exception:
        app.logger.exception('Failed to build blog URLs for sitemap.xml')

    all_urls = base_urls + service_urls + blog_urls
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for item in all_urls:
        lines.append('  <url>')
        lines.append(f"    <loc>{xml_escape(item['loc'])}</loc>")
        if item.get('lastmod'):
            lines.append(f"    <lastmod>{xml_escape(str(item['lastmod']))}</lastmod>")
        if item.get('changefreq'):
            lines.append(f"    <changefreq>{xml_escape(item['changefreq'])}</changefreq>")
        if item.get('priority'):
            lines.append(f"    <priority>{xml_escape(item['priority'])}</priority>")
        lines.append('  </url>')
    lines.append('</urlset>')

    payload = '\n'.join(lines)
    return Response(payload, mimetype='application/xml')


@app.route('/robots.txt', methods=['GET'])
def robots_txt():
    sitemap_url = _public_url(url_for('sitemap_xml'))
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        "Disallow: /payment/\n"
        "\n"
        f"Sitemap: {sitemap_url}\n"
    )
    return Response(body, mimetype='text/plain')


# =============================================================================
# AI ASSISTANT ENDPOINTS
# =============================================================================

def get_ai_assistant():
    """Get AI assistant instance"""
    try:
        from ai_assistant import init_assistant, get_assistant
        assistant = get_assistant()
        if not assistant:
            engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
            if engine == 'postgres':
                db_config = {
                    'engine': 'postgres',
                    'postgres_url': (app.config.get('POSTGRES_URL') or '').strip()
                }
            else:
                db_config = {
                    'engine': 'mysql',
                    'host': Config.MYSQL_HOST,
                    'user': Config.MYSQL_USER,
                    'password': Config.MYSQL_PASSWORD,
                    'database': Config.MYSQL_DB
                }
            assistant = init_assistant(db_config)
        return assistant
    except Exception as e:
        print(f"AI Assistant init error: {e}")
        return None


@app.route('/admin/api/ai/query', methods=['POST'])
@admin_login_required
def ai_query():
    """Process AI query from admin"""
    assistant = get_ai_assistant()
    if not assistant:
        return jsonify({'error': 'AI Assistant not available'}), 503
    
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    chat_id = data.get('chat_id', 'admin_web')
    
    if not message:
        return jsonify({'error': 'Message required'}), 400
    
    result = assistant.process_message(message, chat_id)
    return jsonify(result)


@app.route('/admin/api/ai/settings', methods=['GET', 'POST'])
@admin_login_required
def ai_settings():
    """Get or update AI settings"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    if request.method == 'GET':
        cursor.execute("SELECT * FROM ai_settings WHERE id = 1")
        settings = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if settings:
            # Mask API key for security
            if settings.get('api_key'):
                key = settings['api_key']
                settings['api_key_masked'] = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else '****'
                settings['api_key'] = ''  # Don't send actual key
            return jsonify(settings)
        return jsonify({
            'ai_provider': 'groq',
            'model': 'openai/gpt-oss-20b',
            'reasoning_effort': 'medium',
            'is_enabled': False,
            'telegram_ai_enabled': False
        })
    
    # POST - update settings
    data = request.get_json() or {}
    
    ai_provider = data.get('ai_provider', 'groq')
    api_key = data.get('api_key', '').strip()
    model = data.get('model', 'openai/gpt-oss-20b')
    reasoning_effort = data.get('reasoning_effort', 'medium')
    is_enabled = 1 if data.get('is_enabled') else 0  # Use int for Postgres smallint compatibility
    telegram_ai_enabled = 1 if data.get('telegram_ai_enabled') else 0  # Use int for Postgres smallint compatibility
    allowed_chat_ids = data.get('allowed_chat_ids', '')
    daily_limit = int(data.get('daily_limit', 100))
    
    # Update then insert fallback (works on both MySQL and Postgres)
    if api_key:
        cursor.execute(
            """
            UPDATE ai_settings
            SET ai_provider=%s,
                api_key=%s,
                model=%s,
                reasoning_effort=%s,
                is_enabled=%s,
                telegram_ai_enabled=%s,
                allowed_chat_ids=%s,
                daily_limit=%s,
                updated_at=NOW()
            WHERE id = 1
            """,
            (ai_provider, api_key, model, reasoning_effort, is_enabled, telegram_ai_enabled, allowed_chat_ids, daily_limit)
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO ai_settings (id, ai_provider, api_key, model, reasoning_effort, is_enabled, telegram_ai_enabled, allowed_chat_ids, daily_limit)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (ai_provider, api_key, model, reasoning_effort, is_enabled, telegram_ai_enabled, allowed_chat_ids, daily_limit)
            )
    else:
        cursor.execute(
            """
            UPDATE ai_settings
            SET ai_provider=%s,
                model=%s,
                reasoning_effort=%s,
                is_enabled=%s,
                telegram_ai_enabled=%s,
                allowed_chat_ids=%s,
                daily_limit=%s,
                updated_at=NOW()
            WHERE id = 1
            """,
            (ai_provider, model, reasoning_effort, is_enabled, telegram_ai_enabled, allowed_chat_ids, daily_limit)
        )
        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO ai_settings (id, ai_provider, model, reasoning_effort, is_enabled, telegram_ai_enabled, allowed_chat_ids, daily_limit)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s)
                """,
                (ai_provider, model, reasoning_effort, is_enabled, telegram_ai_enabled, allowed_chat_ids, daily_limit)
            )
    
    conn.commit()
    cursor.close()
    conn.close()
    
    # Reload assistant settings
    assistant = get_ai_assistant()
    if assistant:
        assistant.reload_settings()
    
    return jsonify({'success': True, 'message': 'AI settings saved'})


@app.route('/admin/api/ai/stats', methods=['GET'])
@admin_login_required
def ai_stats():
    """Get AI usage stats"""
    assistant = get_ai_assistant()
    if assistant:
        stats = assistant.get_quick_stats()
        return jsonify(stats)
    return jsonify({})


@app.route('/admin/api/ai/test', methods=['POST'])
@admin_login_required
def ai_test():
    """Test AI connection"""
    assistant = get_ai_assistant()
    if not assistant:
        return jsonify({'success': False, 'message': 'AI Assistant not initialized'})
    
    ready, message = assistant.is_ready()
    if not ready:
        return jsonify({'success': False, 'message': message})
    
    # Try a simple test query
    result = assistant.process_message("Hello, please confirm you're working by saying 'AI Assistant Ready'", 'test')
    return jsonify({
        'success': result.get('success', False),
        'message': result.get('message', 'Test failed')[:500]
    })


# =============================================================================
# TELEGRAM BOT WEBHOOK & AI CHAT
# =============================================================================

@app.route('/telegram/webhook/<token>', methods=['POST'])
def telegram_webhook(token):
    """Receive Telegram webhook updates"""
    # Verify token matches our bot
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT bot_token, is_active FROM telegram_settings WHERE id = 1")
    tg_settings = cursor.fetchone()
    
    cursor.execute("SELECT telegram_ai_enabled, allowed_chat_ids FROM ai_settings WHERE id = 1")
    ai_settings = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not tg_settings or tg_settings.get('bot_token') != token:
        return jsonify({'error': 'Invalid token'}), 403
    
    if not tg_settings.get('is_active'):
        return jsonify({'error': 'Bot not active'}), 403
    
    # Get the update
    update = request.get_json() or {}
    message = update.get('message', {})
    
    if not message:
        return jsonify({'ok': True})
    
    chat_id = str(message.get('chat', {}).get('id', ''))
    text = message.get('text', '').strip()
    username = message.get('from', {}).get('username', 'Unknown')
    first_name = message.get('from', {}).get('first_name', '')
    
    if not text or not chat_id:
        return jsonify({'ok': True})
    
    # Check allowed chat IDs
    allowed_ids = (ai_settings.get('allowed_chat_ids') or '').split(',')
    allowed_ids = [cid.strip() for cid in allowed_ids if cid.strip()]
    
    if allowed_ids and chat_id not in allowed_ids:
        send_telegram_message(tg_settings['bot_token'], chat_id, 
            "⛔ You are not authorized to use this bot.\n\n"
            f"Your Chat ID: <code>{chat_id}</code>\n"
            "Share this ID with the admin to get access."
        )
        return jsonify({'ok': True})
    
    # Handle commands
    if text.startswith('/'):
        response = handle_telegram_command(text, chat_id, username, first_name, tg_settings, ai_settings)
    elif ai_settings.get('telegram_ai_enabled'):
        # AI mode - process as AI query
        response = handle_telegram_ai_query(text, chat_id)
    else:
        response = "💡 Use /help to see available commands.\n\nTo chat naturally, ask the admin to enable AI mode."
    
    # Send response
    if response:
        send_telegram_message(tg_settings['bot_token'], chat_id, response)
    
    return jsonify({'ok': True})


def send_telegram_message(bot_token: str, chat_id: str, text: str, parse_mode: str = 'HTML') -> bool:
    """Send a message via Telegram"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        # Split long messages
        max_len = 4000
        messages = [text[i:i+max_len] for i in range(0, len(text), max_len)]
        
        for msg in messages:
            payload = {
                'chat_id': chat_id,
                'text': msg,
                'parse_mode': parse_mode
            }
            requests.post(url, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram send error: {e}")
        return False


def handle_telegram_command(text: str, chat_id: str, username: str, first_name: str, tg_settings: dict, ai_settings: dict) -> str:
    """Handle Telegram bot commands"""
    parts = text[1:].split(maxsplit=1)
    command = parts[0].lower().split('@')[0]
    args = parts[1] if len(parts) > 1 else ''
    
    if command == 'start':
        return (
            f"👋 Hello {first_name or username}!\n\n"
            "🧹 <b>Done-Well Cleaners AI Assistant</b>\n\n"
            "I can help you manage requests, check stats, and more.\n\n"
            f"Your Chat ID: <code>{chat_id}</code>\n\n"
            "Use /help to see available commands."
        )
    
    elif command == 'help':
        ai_mode = "✅ Enabled" if ai_settings.get('telegram_ai_enabled') else "❌ Disabled"
        return (
            "📋 <b>Available Commands:</b>\n\n"
            "/start - Start the bot\n"
            "/help - Show this help\n"
            "/status - System status\n"
            "/today - Today's summary\n"
            "/pending - Pending requests\n"
            "/search [query] - Search requests\n"
            "/request [REF-ID] - Get request details\n"
            "/update [REF-ID] [status] - Update status\n"
            "/ai [question] - Ask AI assistant\n\n"
            f"🤖 <b>AI Chat Mode:</b> {ai_mode}\n"
            "When enabled, you can chat naturally without commands!"
        )
    
    elif command == 'status':
        return get_system_status_for_telegram()
    
    elif command == 'today':
        return get_today_summary_for_telegram()
    
    elif command == 'pending':
        return get_pending_requests_for_telegram()
    
    elif command == 'search':
        if not args:
            return "❓ Usage: /search [name or email or ref_id]"
        return search_requests_for_telegram(args)
    
    elif command == 'request':
        if not args:
            return "❓ Usage: /request [REF-ID]\nExample: /request REQ-ABC123"
        return get_request_details_for_telegram(args.strip().upper())
    
    elif command == 'update':
        if not args or len(args.split()) < 2:
            return (
                "❓ Usage: /update [REF-ID] [status]\n\n"
                "Valid statuses:\n"
                "• pending\n"
                "• in_progress\n"
                "• completed\n"
                "• cancelled\n"
                "• survey_needed\n\n"
                "Example: /update REQ-ABC123 completed"
            )
        parts = args.split(maxsplit=1)
        return update_request_for_telegram(parts[0].strip().upper(), parts[1].strip().lower())
    
    elif command == 'ai':
        if not args:
            return "❓ Usage: /ai [your question]\nExample: /ai How many pending requests today?"
        return handle_telegram_ai_query(args, chat_id)
    
    elif command == 'chatid' or command == 'id':
        return f"🆔 Your Chat ID: <code>{chat_id}</code>"
    
    else:
        return f"❓ Unknown command: /{command}\n\nUse /help to see available commands."


def handle_telegram_ai_query(query: str, chat_id: str) -> str:
    """Process AI query via Telegram"""
    assistant = get_ai_assistant()
    
    if not assistant:
        return "❌ AI Assistant not configured. Please set up in Admin > AI Settings."
    
    ready, error = assistant.is_ready()
    if not ready:
        return f"❌ {error}"
    
    try:
        result = assistant.process_message(query, f"telegram_{chat_id}")
        
        if result.get('success'):
            return result.get('message', 'No response from AI')
        else:
            return f"❌ {result.get('message', 'AI error occurred')}"
    except Exception as e:
        return f"❌ AI Error: {str(e)}"


def get_system_status_for_telegram() -> str:
    """Get system status for Telegram"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    cursor.execute("SELECT status, COUNT(*) as count FROM requests GROUP BY status")
    status_counts = {row['status']: row['count'] for row in cursor.fetchall()}
    
    condition, params = build_active_true_condition('is_active', engine)
    cursor.execute(
        f"SELECT COUNT(*) as total FROM services WHERE {condition}",
        params
    )
    active_services = cursor.fetchone()['total']
    
    cursor.close()
    conn.close()
    
    total = sum(status_counts.values())
    
    return (
        "📊 <b>System Status</b>\n\n"
        f"📝 Total Requests: {total}\n"
        f"⏳ Pending: {status_counts.get('pending', 0)}\n"
        f"🔄 In Progress: {status_counts.get('in_progress', 0)}\n"
        f"✅ Completed: {status_counts.get('completed', 0)}\n"
        f"❌ Cancelled: {status_counts.get('cancelled', 0)}\n"
        f"📋 Survey Needed: {status_counts.get('survey_needed', 0)}\n\n"
        f"🧹 Active Services: {active_services}"
    )


def get_today_summary_for_telegram() -> str:
    """Get today's summary for Telegram"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN request_type = 'service' THEN 1 ELSE 0 END) as services,
                SUM(CASE WHEN request_type = 'job' THEN 1 ELSE 0 END) as jobs,
                SUM(CASE WHEN request_type = 'general' THEN 1 ELSE 0 END) as general
            FROM requests
            WHERE DATE(created_at) = CURRENT_DATE
            """
        )
    else:
        cursor.execute(
            """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN request_type = 'service' THEN 1 ELSE 0 END) as services,
                SUM(CASE WHEN request_type = 'job' THEN 1 ELSE 0 END) as jobs,
                SUM(CASE WHEN request_type = 'general' THEN 1 ELSE 0 END) as general
            FROM requests
            WHERE DATE(created_at) = CURDATE()
            """
        )
    today = cursor.fetchone()
    
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM requests 
            WHERE status = 'completed' AND DATE(updated_at) = CURRENT_DATE
            """
        )
    else:
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM requests 
            WHERE status = 'completed' AND DATE(updated_at) = CURDATE()
            """
        )
    completed_today = cursor.fetchone()['count']
    
    cursor.close()
    conn.close()
    
    return (
        "📅 <b>Today's Summary</b>\n\n"
        f"📥 New Requests: {today['total']}\n"
        f"  • Service: {today['services']}\n"
        f"  • Job Applications: {today['jobs']}\n"
        f"  • General: {today['general']}\n\n"
        f"✅ Completed Today: {completed_today}"
    )


def get_pending_requests_for_telegram() -> str:
    """Get pending requests for Telegram"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT ref_id, name, request_type, created_at
        FROM requests
        WHERE status = 'pending'
        ORDER BY created_at DESC
        LIMIT 10
    """)
    requests_list = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if not requests_list:
        return "✨ No pending requests!"
    
    lines = ["📋 <b>Pending Requests</b>\n"]
    for req in requests_list:
        created = req['created_at'].strftime('%d/%m %H:%M') if req['created_at'] else ''
        lines.append(f"• <code>{req['ref_id']}</code> - {req['name']} ({req['request_type']}) {created}")
    
    lines.append(f"\n📝 Showing {len(requests_list)} pending requests")
    return "\n".join(lines)


def search_requests_for_telegram(query: str) -> str:
    """Search requests for Telegram"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    search_term = f"%{query}%"
    cursor.execute("""
        SELECT ref_id, name, email, request_type, status, created_at
        FROM requests
        WHERE name LIKE %s OR email LIKE %s OR ref_id LIKE %s OR phone LIKE %s
        ORDER BY created_at DESC
        LIMIT 10
    """, (search_term, search_term, search_term, search_term))
    
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if not results:
        return f"🔍 No results found for: {query}"
    
    lines = [f"🔍 <b>Search Results for '{query}'</b>\n"]
    for req in results:
        status_emoji = {'draft': '🗂️', 'pending': '⏳', 'in_progress': '🔄', 'completed': '✅', 'cancelled': '❌'}.get(req['status'], '📝')
        lines.append(f"{status_emoji} <code>{req['ref_id']}</code> - {req['name']}\n   {req['request_type']} | {req['status']}")
    
    return "\n".join(lines)


def get_request_details_for_telegram(ref_id: str) -> str:
    """Get request details for Telegram"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM requests WHERE ref_id = %s", (ref_id,))
    req = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not req:
        return f"❌ Request not found: {ref_id}"
    
    status_emoji = {'draft': '🗂️', 'pending': '⏳', 'in_progress': '🔄', 'completed': '✅', 'cancelled': '❌', 'survey_needed': '📋'}.get(req['status'], '📝')
    created = req['created_at'].strftime('%d/%m/%Y %H:%M') if req['created_at'] else 'N/A'
    
    return (
        f"📄 <b>Request Details</b>\n\n"
        f"🔖 Ref: <code>{req['ref_id']}</code>\n"
        f"👤 Name: {req['name']}\n"
        f"📧 Email: {req['email']}\n"
        f"📱 Phone: {req.get('phone', 'N/A')}\n"
        f"📝 Type: {req['request_type']}\n"
        f"{status_emoji} Status: {req['status']}\n"
        f"🧹 Service: {req.get('service_name') or 'N/A'}\n"
        f"📅 Created: {created}\n"
        f"💬 Message: {(req.get('message') or 'No message')[:200]}"
    )


def update_request_for_telegram(ref_id: str, new_status: str) -> str:
    """Update request status via Telegram"""
    valid_statuses = {'draft', 'pending', 'in_progress', 'completed', 'cancelled', 'survey_needed'}
    
    if new_status not in valid_statuses:
        return f"❌ Invalid status: {new_status}\n\nValid: {', '.join(valid_statuses)}"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE requests SET status = %s, updated_at = NOW() WHERE ref_id = %s", (new_status, ref_id))
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    conn.close()
    
    if affected:
        status_emoji = {'draft': '🗂️', 'pending': '⏳', 'in_progress': '🔄', 'completed': '✅', 'cancelled': '❌', 'survey_needed': '📋'}.get(new_status, '📝')
        return f"✅ Updated!\n\n<code>{ref_id}</code> → {status_emoji} {new_status}"
    
    return f"❌ Request not found: {ref_id}"


@app.route('/admin/api/telegram/set-webhook', methods=['POST'])
@admin_login_required
def set_telegram_webhook():
    """Set Telegram webhook URL"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT bot_token FROM telegram_settings WHERE id = 1")
    settings = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not settings or not settings.get('bot_token'):
        return jsonify({'success': False, 'message': 'Bot token not configured'})
    
    bot_token = settings['bot_token']
    
    # Get base URL from request or use provided
    data = request.get_json() or {}
    base_url = data.get('base_url', request.host_url.rstrip('/'))
    
    webhook_url = f"{base_url}/telegram/webhook/{bot_token}"
    
    try:
        # Set webhook via Telegram API
        url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
        response = requests.post(url, json={'url': webhook_url}, timeout=10)
        result = response.json()
        
        if result.get('ok'):
            return jsonify({
                'success': True, 
                'message': 'Webhook set successfully!',
                'webhook_url': webhook_url
            })
        else:
            return jsonify({
                'success': False, 
                'message': result.get('description', 'Failed to set webhook')
            })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/admin/api/telegram/webhook-info', methods=['GET'])
@admin_login_required
def get_telegram_webhook_info():
    """Get current Telegram webhook info"""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT bot_token FROM telegram_settings WHERE id = 1")
    settings = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not settings or not settings.get('bot_token'):
        return jsonify({'success': False, 'message': 'Bot token not configured'})
    
    try:
        url = f"https://api.telegram.org/bot{settings['bot_token']}/getWebhookInfo"
        response = requests.get(url, timeout=10)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/admin/api/analytics/chart-data', methods=['GET'])
@admin_login_required
def analytics_chart_data():
    """Get analytics data for charts with date range support"""
    days = request.args.get('days', '30', type=str)
    try:
        days = min(int(days), 90)  # Max 90 days
    except:
        days = 30
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    # Get daily data for the period
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                DATE(created_at) as date,
                SUM(CASE WHEN event_type = 'homepage_visit' THEN 1 ELSE 0 END) as visits,
                SUM(CASE WHEN event_type = 'service_view' THEN 1 ELSE 0 END) as service_views
            FROM analytics
            WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                DATE(created_at) as date,
                SUM(CASE WHEN event_type = 'homepage_visit' THEN 1 ELSE 0 END) as visits,
                SUM(CASE WHEN event_type = 'service_view' THEN 1 ELSE 0 END) as service_views
            FROM analytics
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """,
            (days,)
        )
    analytics_data = cursor.fetchall()
    
    # Get requests data
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                DATE(created_at) as date,
                SUM(CASE WHEN request_type = 'service' THEN 1 ELSE 0 END) as service_requests,
                SUM(CASE WHEN request_type = 'job' THEN 1 ELSE 0 END) as job_applications,
                SUM(CASE WHEN request_type = 'general' THEN 1 ELSE 0 END) as contact_forms
            FROM requests
            WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                DATE(created_at) as date,
                SUM(CASE WHEN request_type = 'service' THEN 1 ELSE 0 END) as service_requests,
                SUM(CASE WHEN request_type = 'job' THEN 1 ELSE 0 END) as job_applications,
                SUM(CASE WHEN request_type = 'general' THEN 1 ELSE 0 END) as contact_forms
            FROM requests
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DATE(created_at)
            ORDER BY date ASC
            """,
            (days,)
        )
    requests_data = cursor.fetchall()
    
    # Get status breakdown
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT status, COUNT(*) as count
            FROM requests
            WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY status
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT status, COUNT(*) as count
            FROM requests
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY status
            """,
            (days,)
        )
    status_breakdown = {row['status']: row['count'] for row in cursor.fetchall()}
    
    # Get service popularity
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT service_name, COUNT(*) as count
            FROM requests
            WHERE request_type = 'service' 
                AND service_name IS NOT NULL 
                AND service_name != ''
                AND created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY service_name
            ORDER BY count DESC
            LIMIT 5
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT service_name, COUNT(*) as count
            FROM requests
            WHERE request_type = 'service' 
                AND service_name IS NOT NULL 
                AND service_name != ''
                AND created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY service_name
            ORDER BY count DESC
            LIMIT 5
            """,
            (days,)
        )
    top_services = cursor.fetchall()
    
    # Get hourly distribution
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT EXTRACT(HOUR FROM created_at) as hour, COUNT(*) as count
            FROM analytics
            WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY EXTRACT(HOUR FROM created_at)
            ORDER BY hour
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT HOUR(created_at) as hour, COUNT(*) as count
            FROM analytics
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY HOUR(created_at)
            ORDER BY hour
            """,
            (days,)
        )
    hourly_data = cursor.fetchall()
    
    # Calculate conversion rate
    if engine == 'postgres':
        cursor.execute(
            "SELECT COUNT(*) as c FROM requests WHERE status = 'completed' AND created_at >= NOW() - (%s * INTERVAL '1 day')",
            (days,)
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) as c FROM requests WHERE status = 'completed' AND created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)",
            (days,)
        )
    completed = cursor.fetchone()['c']
    if engine == 'postgres':
        cursor.execute(
            "SELECT COUNT(*) as c FROM requests WHERE created_at >= NOW() - (%s * INTERVAL '1 day')",
            (days,)
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) as c FROM requests WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)",
            (days,)
        )
    total_requests = cursor.fetchone()['c']
    conversion_rate = round((completed / total_requests * 100) if total_requests > 0 else 0, 1)
    
    cursor.close()
    conn.close()
    
    # Merge analytics and requests data by date
    date_map = {}
    for row in analytics_data:
        d = row['date'].strftime('%Y-%m-%d') if row['date'] else None
        if d:
            date_map[d] = {
                'date': d,
                'visits': row['visits'],
                'service_views': row['service_views'],
                'service_requests': 0,
                'job_applications': 0,
                'contact_forms': 0
            }
    
    for row in requests_data:
        d = row['date'].strftime('%Y-%m-%d') if row['date'] else None
        if d:
            if d not in date_map:
                date_map[d] = {'date': d, 'visits': 0, 'service_views': 0}
            date_map[d]['service_requests'] = row['service_requests']
            date_map[d]['job_applications'] = row['job_applications']
            date_map[d]['contact_forms'] = row['contact_forms']
    
    # Sort by date
    chart_data = sorted(date_map.values(), key=lambda x: x['date'])
    
    return jsonify({
        'chart_data': chart_data,
        'status_breakdown': status_breakdown,
        'top_services': top_services,
        'hourly_distribution': hourly_data,
        'conversion_rate': conversion_rate,
        'total_requests': total_requests,
        'completed': completed,
        'period_days': days
    })


@app.route('/admin/ai-settings')
@admin_login_required
def admin_ai_settings_page():
    """AI Settings admin page"""
    site_settings = fetch_site_settings()
    return render_template('admin/ai_settings.html', site_settings=site_settings)


@app.route('/admin/api/analytics/detailed')
@admin_login_required
def admin_api_analytics_detailed():
    """Advanced analytics with revenue trends, funnel, geographic data"""
    days = request.args.get('days', 30, type=int)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    engine = (app.config.get('DB_ENGINE') or 'mysql').strip().lower()
    
    # Revenue Trend - daily revenue from completed service_requests
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT DATE(updated_at) as date,
                     SUM(COALESCE(total_price, 0)) as revenue,
                     COUNT(*) as completed_count
            FROM service_requests
            WHERE status = 'completed'
                AND updated_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY DATE(updated_at)
            ORDER BY date ASC
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT DATE(updated_at) as date,
                     SUM(COALESCE(total_price, 0)) as revenue,
                     COUNT(*) as completed_count
            FROM service_requests
            WHERE status = 'completed'
                AND updated_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DATE(updated_at)
            ORDER BY date ASC
            """,
            (days,)
        )
    revenue_data = cursor.fetchall()
    
    # Convert to serializable format
    revenue_trend = []
    for row in revenue_data:
        revenue_trend.append({
            'date': row['date'].strftime('%Y-%m-%d') if row['date'] else None,
            'revenue': float(row['revenue'] or 0),
            'completed_count': row['completed_count']
        })
    
    # Total Revenue Stats from service_requests
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                SUM(CASE WHEN updated_at >= NOW() - (%s * INTERVAL '1 day') AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as period_revenue,
                SUM(CASE WHEN DATE(updated_at) = CURRENT_DATE AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as today_revenue,
                SUM(CASE WHEN updated_at >= NOW() - (7 * INTERVAL '1 day') AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as week_revenue,
                SUM(CASE WHEN date_trunc('month', updated_at) = date_trunc('month', CURRENT_DATE) AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as month_revenue
            FROM service_requests
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                SUM(CASE WHEN updated_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY) AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as period_revenue,
                SUM(CASE WHEN DATE(updated_at) = CURDATE() AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as today_revenue,
                SUM(CASE WHEN updated_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as week_revenue,
                SUM(CASE WHEN MONTH(updated_at) = MONTH(CURDATE()) AND YEAR(updated_at) = YEAR(CURDATE()) AND status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as month_revenue
            FROM service_requests
            """,
            (days,)
        )
    revenue_stats = cursor.fetchone()
    
    # Conversion Funnel - use service_requests for service-related metrics
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                (SELECT COUNT(*) FROM analytics WHERE event_type = 'visit' AND created_at >= NOW() - (%s * INTERVAL '1 day')) as visits,
                (SELECT COUNT(*) FROM analytics WHERE event_type = 'service_view' AND created_at >= NOW() - (%s * INTERVAL '1 day')) as service_views,
                (SELECT COUNT(*) FROM service_requests WHERE created_at >= NOW() - (%s * INTERVAL '1 day')) as quote_requests,
                (SELECT COUNT(*) FROM service_requests WHERE status IN ('in_progress', 'completed') AND created_at >= NOW() - (%s * INTERVAL '1 day')) as confirmed,
                (SELECT COUNT(*) FROM service_requests WHERE status = 'completed' AND created_at >= NOW() - (%s * INTERVAL '1 day')) as completed
            """,
            (days, days, days, days, days)
        )
    else:
        cursor.execute(
            """
            SELECT 
                (SELECT COUNT(*) FROM analytics WHERE event_type = 'visit' AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)) as visits,
                (SELECT COUNT(*) FROM analytics WHERE event_type = 'service_view' AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)) as service_views,
                (SELECT COUNT(*) FROM service_requests WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)) as quote_requests,
                (SELECT COUNT(*) FROM service_requests WHERE status IN ('in_progress', 'completed') AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)) as confirmed,
                (SELECT COUNT(*) FROM service_requests WHERE status = 'completed' AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)) as completed
            """,
            (days, days, days, days, days)
        )
    funnel_data = cursor.fetchone()
    
    conversion_funnel = {
        'visits': funnel_data['visits'] or 0,
        'service_views': funnel_data['service_views'] or 0,
        'quote_requests': funnel_data['quote_requests'] or 0,
        'confirmed': funnel_data['confirmed'] or 0,
        'completed': funnel_data['completed'] or 0
    }
    
    # Calculate conversion rates
    if conversion_funnel['visits'] > 0:
        conversion_funnel['view_rate'] = round((conversion_funnel['service_views'] / conversion_funnel['visits']) * 100, 1)
    else:
        conversion_funnel['view_rate'] = 0
    
    if conversion_funnel['service_views'] > 0:
        conversion_funnel['request_rate'] = round((conversion_funnel['quote_requests'] / conversion_funnel['service_views']) * 100, 1)
    else:
        conversion_funnel['request_rate'] = 0
    
    if conversion_funnel['quote_requests'] > 0:
        conversion_funnel['completion_rate'] = round((conversion_funnel['completed'] / conversion_funnel['quote_requests']) * 100, 1)
    else:
        conversion_funnel['completion_rate'] = 0
    
    # Day of Week Analysis - use service_requests for revenue, requests for general count
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                EXTRACT(ISODOW FROM created_at) as day_num,
                TO_CHAR(created_at, 'FMDay') as day_name,
                COUNT(*) as request_count
            FROM requests
            WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY EXTRACT(ISODOW FROM created_at), TO_CHAR(created_at, 'FMDay')
            ORDER BY day_num
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                DAYOFWEEK(created_at) as day_num,
                DAYNAME(created_at) as day_name,
                COUNT(*) as request_count
            FROM requests
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DAYOFWEEK(created_at), DAYNAME(created_at)
            ORDER BY day_num
            """,
            (days,)
        )
    day_data = cursor.fetchall()
    
    # Get revenue by day from service_requests
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                EXTRACT(ISODOW FROM updated_at) as day_num,
                SUM(CASE WHEN status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as revenue
            FROM service_requests
            WHERE updated_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY EXTRACT(ISODOW FROM updated_at)
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                DAYOFWEEK(updated_at) as day_num,
                SUM(CASE WHEN status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as revenue
            FROM service_requests
            WHERE updated_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY DAYOFWEEK(updated_at)
            """,
            (days,)
        )
    day_revenue = {row['day_num']: float(row['revenue'] or 0) for row in cursor.fetchall()}
    
    day_of_week = []
    for row in day_data:
        day_of_week.append({
            'day': row['day_name'],
            'requests': row['request_count'],
            'revenue': day_revenue.get(row['day_num'], 0)
        })
    
    # Service Performance - join service_requests with service_request_items and services
    if engine == 'postgres':
        cursor.execute(
            """
            SELECT 
                s.name as service_name,
                COUNT(DISTINCT sr.id) as total_requests,
                COUNT(DISTINCT CASE WHEN sr.status = 'completed' THEN sr.id END) as completed,
                SUM(CASE WHEN sr.status = 'completed' THEN COALESCE(sri.price, 0) ELSE 0 END) as revenue,
                AVG(CASE WHEN sr.status = 'completed' THEN COALESCE(sri.price, 0) ELSE NULL END) as avg_value
            FROM service_requests sr
            JOIN service_request_items sri ON sr.id = sri.service_request_id
            JOIN services s ON sri.service_id = s.id
            WHERE sr.created_at >= NOW() - (%s * INTERVAL '1 day')
            GROUP BY s.id, s.name
            ORDER BY revenue DESC
            LIMIT 10
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                s.name as service_name,
                COUNT(DISTINCT sr.id) as total_requests,
                COUNT(DISTINCT CASE WHEN sr.status = 'completed' THEN sr.id END) as completed,
                SUM(CASE WHEN sr.status = 'completed' THEN COALESCE(sri.price, 0) ELSE 0 END) as revenue,
                AVG(CASE WHEN sr.status = 'completed' THEN COALESCE(sri.price, 0) ELSE NULL END) as avg_value
            FROM service_requests sr
            JOIN service_request_items sri ON sr.id = sri.service_request_id
            JOIN services s ON sri.service_id = s.id
            WHERE sr.created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY s.id, s.name
            ORDER BY revenue DESC
            LIMIT 10
            """,
            (days,)
        )
    service_perf = cursor.fetchall()
    
    service_performance = []
    for row in service_perf:
        service_performance.append({
            'name': row['service_name'],
            'requests': row['total_requests'],
            'completed': row['completed'],
            'revenue': float(row['revenue'] or 0),
            'avg_value': float(row['avg_value'] or 0)
        })
    
    # Geographic Distribution - from service_requests which has address
    if engine == 'postgres':
        cursor.execute(
            """
            WITH _sr AS (
                SELECT
                    address,
                    status,
                    total_price,
                    string_to_array(address, ',') AS parts
                FROM service_requests
                WHERE created_at >= NOW() - (%s * INTERVAL '1 day')
                  AND address IS NOT NULL AND address != ''
            )
            SELECT
                COALESCE(
                    NULLIF(
                        btrim(array_to_string(
                            parts[GREATEST(array_upper(parts, 1) - 1, 1):array_upper(parts, 1)],
                            ','
                        )),
                        ''
                    ),
                    'Unknown'
                ) AS area,
                COUNT(*) as request_count,
                SUM(CASE WHEN status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as revenue
            FROM _sr
            GROUP BY area
            ORDER BY request_count DESC
            LIMIT 10
            """,
            (days,)
        )
    else:
        cursor.execute(
            """
            SELECT 
                COALESCE(SUBSTRING_INDEX(address, ',', -2), 'Unknown') as area,
                COUNT(*) as request_count,
                SUM(CASE WHEN status = 'completed' THEN COALESCE(total_price, 0) ELSE 0 END) as revenue
            FROM service_requests
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              AND address IS NOT NULL AND address != ''
            GROUP BY area
            ORDER BY request_count DESC
            LIMIT 10
            """,
            (days,)
        )
    geo_data = cursor.fetchall()
    
    geographic = []
    for row in geo_data:
        geographic.append({
            'area': row['area'].strip() if row['area'] else 'Unknown',
            'requests': row['request_count'],
            'revenue': float(row['revenue'] or 0)
        })
    
    cursor.close()
    conn.close()
    
    return jsonify({
        'revenue_trend': revenue_trend,
        'revenue_stats': {
            'period': float(revenue_stats['period_revenue'] or 0),
            'today': float(revenue_stats['today_revenue'] or 0),
            'week': float(revenue_stats['week_revenue'] or 0),
            'month': float(revenue_stats['month_revenue'] or 0)
        },
        'conversion_funnel': conversion_funnel,
        'day_of_week': day_of_week,
        'service_performance': service_performance,
        'geographic': geographic,
        'period_days': days
    })


configure_telegram_error_log_handler()


if __name__ == '__main__':
    # Pre-warm the DB connection pool so the first request is fast
    try:
        _db_engine = (os.environ.get('DB_ENGINE') or '').strip().lower()
        if 'postgres' in _db_engine:
            _get_pg_pool()
    except Exception:
        pass

    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    server_log_path = os.path.join(log_dir, 'server.log')

    logging.basicConfig(
        level=logging.INFO,
        force=True,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(server_log_path, encoding='utf-8')
        ]
    )
    app.logger.setLevel(logging.INFO)
    logging.getLogger('werkzeug').setLevel(logging.INFO)

    host = '0.0.0.0'
    port = int(os.environ.get('PORT', 5000))
    debug_enabled = (os.environ.get('FLASK_DEBUG', '0') == '1')
    print(f"[startup] Flask server starting on http://{host}:{port} (debug={debug_enabled})", flush=True)
    print(f"[startup] Writing logs to {server_log_path}", flush=True)

    app.run(
        host=host,
        port=port,
        debug=debug_enabled
    )
