from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import random
import hashlib
import json
import hmac
import re
import resend
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import secrets
import uuid
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
import statistics

load_dotenv()

from supabase import create_client

try:
    from .model_service import CadenceModelService
except ImportError:
    from model_service import CadenceModelService

app = Flask(__name__)
app.logger.setLevel("INFO")
# Trust one level of X-Forwarded-For so the rate limiter sees the real
# client IP rather than the hosting platform's proxy address.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
model_service = CadenceModelService()

# ---------------------------------------------------------------------------
# Rate limiting — applied per originating IP.
# /authenticate is the sensitive endpoint; limits are intentionally strict.
# Uses in-memory storage (fine for single-instance / demo). Swap storage_uri
# to "redis://..." for a multi-instance production deployment.
# ---------------------------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=os.getenv("CADENCE_RATE_LIMIT_STORAGE_URI", "memory://"),
    default_limits=[],
)

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"status": "error", "message": "too many attempts — slow down and try again"}), 429


# How many consecutive wrong passwords before we lock the account and send
# an unlock email. Configurable via env so it can be tuned without a deploy.
FAILED_PASSWORD_THRESHOLD = int(os.getenv("CADENCE_PASSWORD_ATTEMPT_THRESHOLD", "5"))

# Demo mode: skips the Resend email and returns the freshly-generated
# OTP in the API response so testers without access to the inbox can
# still complete 2FA. Never enable in production.
DEMO_MODE = os.getenv("CADENCE_DEMO_MODE", "0").lower() in {"1", "true", "yes"}
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "team@cadence-capstone.us")

# CORS for the local dev frontend. Override with CADENCE_CORS_ORIGINS
# (comma-separated) when deploying behind a different origin.
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:5173,http://127.0.0.1:5173"
)
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.getenv("CADENCE_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
}


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PATCH,OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Cadence-Admin-Token"
        )
    return response


@app.route("/<path:_any>", methods=["OPTIONS"])
@app.route("/", methods=["OPTIONS"])
def cors_preflight(_any=None):
    return ("", 204)
REQUIRED_ENROLLMENT_SAMPLES = int(
    os.getenv("CADENCE_REQUIRED_ENROLLMENT_SAMPLES", "5")
)
DEFAULT_THRESHOLD = 0.40
ADMIN_TOKEN = os.getenv("CADENCE_ADMIN_TOKEN", "").strip()
ALLOW_OPEN_ADMIN = os.getenv("CADENCE_ALLOW_OPEN_ADMIN", "0").lower() in {"1", "true", "yes"}
API_KEY_PREFIX_LENGTH = 18
ADMIN_RATE_LIMIT = os.getenv("CADENCE_ADMIN_RATE_LIMIT", "30 per minute; 300 per hour")
PUBLIC_REGISTRATION_RATE_LIMIT = os.getenv(
    "CADENCE_PUBLIC_REGISTRATION_RATE_LIMIT", "10 per minute; 100 per hour"
)
PLATFORM_WRITE_RATE_LIMIT = os.getenv("CADENCE_PLATFORM_WRITE_RATE_LIMIT", "120 per minute; 5000 per hour")
PLATFORM_SCORE_RATE_LIMIT = os.getenv("CADENCE_PLATFORM_SCORE_RATE_LIMIT", "240 per minute; 10000 per hour")

# Two clients on purpose:
#   `supabase`      → service-role, used for ALL table reads/writes.
#   `supabase_auth` → service-role, used ONLY for auth.sign_up /
#                     auth.sign_in_with_password. After a successful
#                     sign-in the client's session switches to the
#                     newly-authenticated user, putting subsequent DB
#                     calls under that user's RLS context. Isolating
#                     auth on a separate client keeps `supabase` in
#                     service-role context for the rest of the request.
SUPABASE_URL = os.getenv("SUPABASE_URL").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY").strip()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
supabase_auth = create_client(SUPABASE_URL, SUPABASE_KEY)



def _supabase_sign_up(email, password):
    # Admin create skips the confirmation email Supabase would otherwise
    # send — important because the project's free-tier email rate limit
    # gets tripped quickly during demo signups. The created user is
    # marked already-confirmed so they can immediately sign in.
    return supabase.auth.admin.create_user({
        "email": email,
        "password": password,
        "email_confirm": True,
    })


def _supabase_sign_in(email, password):
    auth = supabase_auth.auth
    # helper handles Supabase sign in API differences
    try:
        return auth.sign_in_with_password({"email": email, "password": password})
    except TypeError:
        return auth.sign_in(email=email, password=password)


def error_response(message, status=400, code="error"):
    return jsonify({"status": code, "message": message}), status


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or f"app-{secrets.token_hex(4)}"


def hash_api_key(api_key):
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key():
    return f"sk_live_{secrets.token_urlsafe(32)}"


def validate_allowed_origins(value):
    allowed_origins = value or []
    if not isinstance(allowed_origins, list) or not all(
        isinstance(origin, str) for origin in allowed_origins
    ):
        return None, "allowed_origins must be a list of strings"
    return allowed_origins, None


def public_api_key_row(key_row):
    return {
        "api_key_id": key_row.get("api_key_id"),
        "application_id": key_row.get("application_id"),
        "name": key_row.get("name"),
        "key_prefix": key_row.get("key_prefix"),
        "revoked_at": key_row.get("revoked_at"),
        "last_used_at": key_row.get("last_used_at"),
        "created_at": key_row.get("created_at"),
    }


def public_app_registration_row(registration_row):
    approved = registration_row.get("approved")
    application_id = registration_row.get("application_id")
    return {
        "app_registration_id": application_id,
        "name": registration_row.get("name"),
        "slug": registration_row.get("slug"),
        "contact_email": registration_row.get("contact_email"),
        "allowed_origins": registration_row.get("allowed_origins") or [],
        "use_case": registration_row.get("use_case"),
        "status": "approved" if approved is True else "pending",
        "application_id": application_id if approved is True else None,
        "approved": approved is True,
        "reviewed_at": registration_row.get("reviewed_at"),
        "created_at": registration_row.get("created_at"),
        "updated_at": registration_row.get("updated_at"),
    }


def generate_registration_lookup_token():
    return f"reg_status_{secrets.token_urlsafe(32)}"


def object_value(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def auth_user_payload(auth_response):
    user = object_value(auth_response, "user")
    if user is None and isinstance(auth_response, dict):
        user = auth_response.get("data", {}).get("user")
    if user is None:
        return None

    confirmed_at = (
        object_value(user, "email_confirmed_at")
        or object_value(user, "confirmed_at")
    )
    return {
        "user_id": object_value(user, "id"),
        "email": object_value(user, "email"),
        "email_confirmed_at": confirmed_at,
    }


def auth_session_payload(auth_response):
    session = object_value(auth_response, "session")
    if session is None and isinstance(auth_response, dict):
        session = auth_response.get("data", {}).get("session")
    if session is None:
        return None
    return {
        "access_token": object_value(session, "access_token"),
        "refresh_token": object_value(session, "refresh_token"),
        "expires_at": object_value(session, "expires_at"),
        "expires_in": object_value(session, "expires_in"),
    }


def public_developer_user(auth_response_or_user):
    user = auth_user_payload(auth_response_or_user)
    if not user and (
        isinstance(auth_response_or_user, dict)
        or hasattr(auth_response_or_user, "id")
    ):
        user = {
            "user_id": object_value(auth_response_or_user, "id"),
            "email": object_value(auth_response_or_user, "email"),
            "email_confirmed_at": (
                object_value(auth_response_or_user, "email_confirmed_at")
                or object_value(auth_response_or_user, "confirmed_at")
            ),
        }
    return user


def sign_up_with_password(email, password):
    try:
        return supabase_auth.auth.sign_up({"email": email, "password": password})
    except TypeError:
        return supabase_auth.auth.sign_up(email=email, password=password)


def developer_sign_in(email, password):
    return _supabase_sign_in(email, password)


def get_user_for_access_token(access_token):
    return supabase_auth.auth.get_user(access_token)


def is_confirmed_developer(user):
    return bool(user and user.get("user_id") and user.get("email_confirmed_at"))


def developer_auth_error():
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None, error_response("missing developer bearer token", 401, "unauthorized")
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None, error_response("missing developer bearer token", 401, "unauthorized")

    try:
        user = public_developer_user(get_user_for_access_token(token))
    except Exception:
        app.logger.exception("developer token verification failed")
        return None, error_response("invalid developer bearer token", 401, "unauthorized")
    if not user or not user.get("user_id"):
        return None, error_response("invalid developer bearer token", 401, "unauthorized")
    if not is_confirmed_developer(user):
        return None, error_response("developer email is not confirmed", 403, "email_not_confirmed")
    return user, None


def require_developer(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        developer_user, auth_error = developer_auth_error()
        if auth_error:
            return auth_error
        request.cadence_developer_user = developer_user
        return handler(*args, **kwargs)

    return wrapped


def require_admin_if_configured():
    if not ADMIN_TOKEN:
        if ALLOW_OPEN_ADMIN:
            return None
        return error_response(
            "CADENCE_ADMIN_TOKEN is required for admin endpoints",
            503,
            "misconfigured",
        )
    if len(ADMIN_TOKEN) < 32:
        return error_response(
            "CADENCE_ADMIN_TOKEN must be at least 32 characters",
            503,
            "misconfigured",
        )
    provided = request.headers.get("X-Cadence-Admin-Token", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        provided = auth.split(" ", 1)[1].strip()
    if hmac.compare_digest(provided, ADMIN_TOKEN):
        return None
    return error_response("missing or invalid admin token", 401, "unauthorized")


def require_api_key(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return error_response("missing bearer API key", 401, "unauthorized")

        api_key = auth.split(" ", 1)[1].strip()
        if not api_key:
            return error_response("missing bearer API key", 401, "unauthorized")

        prefix = api_key[:API_KEY_PREFIX_LENGTH]
        result = supabase.table("api_keys") \
            .select("api_key_id, application_id, key_prefix, key_hash, revoked_at") \
            .eq("key_prefix", prefix) \
            .execute()
        rows = result.data or []
        key_row = rows[0] if rows else None
        if (
            not key_row
            or key_row.get("revoked_at") is not None
            or not hmac.compare_digest(key_row.get("key_hash", ""), hash_api_key(api_key))
        ):
            return error_response("invalid API key", 401, "unauthorized")

        app_result = supabase.table("applications") \
            .select("*") \
            .eq("application_id", key_row["application_id"]) \
            .execute()
        app_rows = app_result.data or []
        if not app_rows:
            return error_response("API key application not found", 401, "unauthorized")
        app_row = app_rows[0]
        if app_row.get("approved") is False:
            return error_response("application is not approved", 403, "forbidden")

        origin = request.headers.get("Origin")
        allowed_origins = app_row.get("allowed_origins") or []
        if origin and allowed_origins and origin not in allowed_origins:
            return error_response("origin is not allowed for this application", 403, "forbidden")

        supabase.table("api_keys") \
            .update({"last_used_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("api_key_id", key_row["api_key_id"]) \
            .execute()
        request.cadence_api_key = key_row
        request.cadence_application = app_row
        return handler(*args, **kwargs)

    return wrapped


def get_json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def create_application_record(data):
    name = (data.get("name") or "").strip()
    if not name:
        return None, error_response("missing name")

    allowed_origins, origins_error = validate_allowed_origins(data.get("allowed_origins"))
    if origins_error:
        return None, error_response(origins_error)

    payload = {
        "name": name,
        "slug": slugify(data.get("slug") or name),
        "allowed_origins": allowed_origins,
        "threshold": float(data.get("threshold") or DEFAULT_THRESHOLD),
        "approved": bool(data.get("approved", True)),
    }
    contact_email = (data.get("contact_email") or "").strip()
    if contact_email:
        payload["contact_email"] = contact_email

    try:
        result = supabase.table("applications").insert(payload).execute()
    except Exception as exc:
        return None, error_response(str(exc), 400)
    return result.data[0], None


def create_api_key_record(application_id, name="default"):
    api_key = generate_api_key()
    payload = {
        "application_id": application_id,
        "name": (name or "default").strip() or "default",
        "key_prefix": api_key[:API_KEY_PREFIX_LENGTH],
        "key_hash": hash_api_key(api_key),
    }
    result = supabase.table("api_keys").insert(payload).execute()
    key_row = result.data[0]
    return key_row, api_key


def api_key_with_secret_response(key_row, api_key):
    return {
        "api_key_id": key_row["api_key_id"],
        "application_id": key_row["application_id"],
        "name": key_row["name"],
        "key_prefix": key_row["key_prefix"],
        "key": api_key,
    }


def fetch_developer_application(application_id, developer_user_id=None, developer_email=None):
    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", application_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return None

    application = rows[0]
    if application.get("developer_user_id"):
        return application if application.get("developer_user_id") == developer_user_id else None
    if developer_email:
        return application if application.get("contact_email") == developer_email else None
    return None


def developer_application_or_error(application_id):
    application = fetch_developer_application(
        application_id,
        request.cadence_developer_user["user_id"],
        request.cadence_developer_user.get("email"),
    )
    if not application:
        return None, error_response("application not found", 404, "not_found")
    return application, None


def latest_value(rows, key):
    values = [row.get(key) for row in rows if row.get(key)]
    return max(values) if values else None


def build_platform_app_usage(application_id):
    app_result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", application_id) \
        .execute()
    app_rows = app_result.data or []
    if not app_rows:
        return None

    api_keys = supabase.table("api_keys") \
        .select("api_key_id, revoked_at, last_used_at, created_at") \
        .eq("application_id", application_id) \
        .execute().data or []

    return {
        "application": app_rows[0],
        "api_keys": {
            "total": len(api_keys),
            "active": sum(1 for row in api_keys if row.get("revoked_at") is None),
            "revoked": sum(1 for row in api_keys if row.get("revoked_at") is not None),
            "last_used_at": latest_value(api_keys, "last_used_at"),
        },
    }


def count_successful_login_attempts(user_id):
    result = supabase.table("login_attempts") \
        .select("login_attempt_id") \
        .eq("user_id", user_id) \
        .eq("successful_login", "successful") \
        .execute()
    return len(result.data or [])


def _increment_successful_logins(user_id):
    profile = supabase.table("user_profiles") \
        .select("number_of_successful_logins") \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    current = (profile.data or {}).get("number_of_successful_logins") or 0
    supabase.table("user_profiles") \
        .update({"number_of_successful_logins": current + 1}) \
        .eq("user_id", user_id) \
        .execute()


def enrollment_payload(enrollment_count):
    samples_needed = max(REQUIRED_ENROLLMENT_SAMPLES - enrollment_count, 0)
    return {
        "enrolled": samples_needed == 0,
        "enrollment_count": enrollment_count,
        "enrollment_required": REQUIRED_ENROLLMENT_SAMPLES,
        "enrollment_samples_needed": samples_needed,
    }


def require_2fa(user_id, login_attempt_id, enrollment_count, reason):
    supabase.table("user_profiles") \
        .update({"current_login_status": "pending 2fa"}) \
        .eq("user_id", user_id) \
        .execute()

    supabase.table("login_attempts") \
        .update({"two_fa_invoked": True}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    # If sending the OTP fails (e.g. Resend rejects the recipient in
    # test mode), roll back the pending state so the user can retry
    # instead of getting wedged into "previous login still pending".
    try:
        otp = send_code(user_id, login_attempt_id)
    except Exception as exc:
        app.logger.exception("send_code failed; rolling back pending 2fa")
        supabase.table("user_profiles") \
            .update({"current_login_status": "not logged in"}) \
            .eq("user_id", user_id) \
            .execute()
        supabase.table("_2fa") \
            .delete() \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()
        return jsonify({
            "status": "error",
            "message": f"could not send 2FA email: {exc}",
        }), 502

    body = {
        "status": "2fa required",
        "login_attempt_id": login_attempt_id,
        "reason": reason,
        **enrollment_payload(enrollment_count),
    }
    if DEMO_MODE:
        body["demo_otp"] = otp
    return jsonify(body), 200


def _load_app_section(filename):
    """Execute a split app section against this module's globals.

    The backend tests monkeypatch globals on ``backend.app`` directly. Loading
    the route files into this namespace preserves that compatibility while
    letting the source live in smaller, purpose-specific files.
    """
    section_path = Path(__file__).with_name(filename)
    source = section_path.read_text(encoding="utf-8")
    exec(compile(source, str(section_path), "exec"), globals())


# Shared auth-flow helpers must be loaded before the route modules that call
# them. Endpoint sections are intentionally grouped by product surface.
_load_app_section("internal_helpers.py")
_load_app_section("developer_portal_endpoints.py")
_load_app_section("platform_endpoints.py")
_load_app_section("auth_flow_endpoints.py")


if __name__ == "__main__":
    app.run()
