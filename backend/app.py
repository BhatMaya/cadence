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
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
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
        .eq("successful_login", True) \
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
            .update({"current_login_status": None}) \
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


# health check endpoint
@app.get("/health")
def health():
    return {"status": "ok"}

# get model health endpoint
@app.get("/model/health")
def model_health():
    return jsonify(model_service.health())




@app.post("/v1/apps")
@limiter.limit(ADMIN_RATE_LIMIT)
def create_platform_app():
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    application, app_error = create_application_record(get_json_body())
    if app_error:
        return app_error
    return jsonify({"status": "created", "application": application}), 201


@app.post("/v1/developer/signup")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def developer_signup():
    data = get_json_body()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email:
        return error_response("missing email")
    if not password:
        return error_response("missing password")

    try:
        result = sign_up_with_password(email, password)
    except Exception as exc:
        return error_response(str(exc), 400)

    user = auth_user_payload(result)
    session = auth_session_payload(result)
    return jsonify({
        "status": "signed_up",
        "developer": user,
        "session": session,
        "email_confirmed": bool(user and user.get("email_confirmed_at")),
        "message": "Check your email to confirm your developer account.",
    }), 201


@app.post("/v1/developer/login")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def developer_login():
    data = get_json_body()
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email:
        return error_response("missing email")
    if not password:
        return error_response("missing password")

    try:
        result = developer_sign_in(email, password)
    except Exception as exc:
        return error_response(str(exc), 401, "unauthorized")

    user = auth_user_payload(result)
    if not is_confirmed_developer(user):
        return error_response("developer email is not confirmed", 403, "email_not_confirmed")

    session = auth_session_payload(result)
    if not session or not session.get("access_token"):
        return error_response("developer session was not returned", 401, "unauthorized")

    return jsonify({
        "status": "ok",
        "developer": user,
        "session": session,
    })


@app.get("/v1/developer/me")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def developer_me():
    return jsonify({
        "status": "ok",
        "developer": request.cadence_developer_user,
    })


@app.get("/v1/developer/apps")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def list_developer_apps():
    result = supabase.table("applications") \
        .select("*") \
        .eq("contact_email", request.cadence_developer_user.get("email")) \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "applications": result.data or []})


@app.post("/v1/developer/apps")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def create_developer_app():
    data = get_json_body()
    developer_user = request.cadence_developer_user
    application, app_error = create_application_record({
        **data,
        "contact_email": developer_user.get("email"),
        "approved": True,
    })
    if app_error:
        return app_error

    key_row, api_key = create_api_key_record(
        application["application_id"],
        data.get("key_name") or "production",
    )
    return jsonify({
        "status": "created",
        "application": application,
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.get("/v1/developer/apps/<application_id>/usage")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def get_developer_app_usage(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    usage = build_platform_app_usage(application["application_id"])
    return jsonify({"status": "ok", "usage": usage})


@app.get("/v1/developer/apps/<application_id>/api-keys")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_developer
def list_developer_api_keys(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("application_id", application["application_id"]) \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "api_keys": result.data or []})


@app.post("/v1/developer/apps/<application_id>/api-keys")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def create_developer_api_key(application_id):
    application, app_error = developer_application_or_error(application_id)
    if app_error:
        return app_error

    data = get_json_body()
    key_row, api_key = create_api_key_record(
        application["application_id"],
        data.get("name") or "default",
    )
    return jsonify({
        "status": "created",
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.post("/v1/developer/api-keys/<api_key_id>/revoke")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
@require_developer
def revoke_developer_api_key(api_key_id):
    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("api_key_id", api_key_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("API key not found", 404, "not_found")

    key_row = rows[0]
    application = fetch_developer_application(
        key_row["application_id"],
        request.cadence_developer_user["user_id"],
        request.cadence_developer_user.get("email"),
    )
    if not application:
        return error_response("API key not found", 404, "not_found")

    if key_row.get("revoked_at") is None:
        revoked_at = datetime.now(timezone.utc).isoformat()
        update_result = supabase.table("api_keys") \
            .update({"revoked_at": revoked_at}) \
            .eq("api_key_id", api_key_id) \
            .execute()
        key_row = (update_result.data or [key_row])[0]

    return jsonify({"status": "revoked", "api_key": public_api_key_row(key_row)})


@app.post("/v1/app-registrations")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def submit_platform_app_registration():
    data = get_json_body()
    contact_email = (data.get("contact_email") or "").strip()
    if not contact_email:
        return error_response("missing contact_email")

    lookup_token = generate_registration_lookup_token()
    application, app_error = create_application_record({
        **data,
        "contact_email": contact_email,
        "approved": False,
    })
    if app_error:
        return app_error

    return jsonify({
        "status": "submitted",
        "registration": public_app_registration_row(application),
        "lookup_token": lookup_token,
    }), 201


@app.get("/v1/app-registrations/<app_registration_id>/status")
@limiter.limit(PUBLIC_REGISTRATION_RATE_LIMIT)
def get_platform_app_registration_status(app_registration_id):
    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", app_registration_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("registration not found", 404, "not_found")

    return jsonify({
        "status": "ok",
        "registration": public_app_registration_row(rows[0]),
    })


@app.get("/v1/app-registrations")
@limiter.limit(ADMIN_RATE_LIMIT)
def list_platform_app_registrations():
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({
        "status": "ok",
        "registrations": [
            public_app_registration_row(row)
            for row in result.data or []
        ],
    })


@app.post("/v1/app-registrations/<app_registration_id>/approve")
@limiter.limit(ADMIN_RATE_LIMIT)
def approve_platform_app_registration(app_registration_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", app_registration_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("registration not found", 404, "not_found")

    application = rows[0]
    if application.get("approved") is True:
        return jsonify({
            "status": "approved",
            "registration": public_app_registration_row(application),
            "application": application,
            "api_key": None,
        })

    data = get_json_body()
    key_row, api_key = create_api_key_record(
        application["application_id"],
        data.get("key_name") or "default",
    )

    update_result = supabase.table("applications") \
        .update({"approved": True}) \
        .eq("application_id", app_registration_id) \
        .execute()
    updated_application = (update_result.data or [application])[0]

    return jsonify({
        "status": "approved",
        "registration": public_app_registration_row(updated_application),
        "application": updated_application,
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.post("/v1/app-registrations/<app_registration_id>/reject")
@limiter.limit(ADMIN_RATE_LIMIT)
def reject_platform_app_registration(app_registration_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .eq("application_id", app_registration_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("registration not found", 404, "not_found")
    if rows[0].get("approved") is True:
        return error_response("registration already approved", 400)

    updated = supabase.table("applications") \
        .update({"approved": False}) \
        .eq("application_id", app_registration_id) \
        .execute()
    registration = public_app_registration_row((updated.data or rows)[0])
    registration["status"] = "rejected"
    return jsonify({
        "status": "rejected",
        "registration": registration,
    })


@app.get("/v1/apps")
@limiter.limit(ADMIN_RATE_LIMIT)
def list_platform_apps():
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("applications") \
        .select("*") \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "applications": result.data or []})


@app.get("/v1/apps/<application_id>/usage")
@limiter.limit(ADMIN_RATE_LIMIT)
def get_platform_app_usage(application_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    usage = build_platform_app_usage(application_id)
    if usage is None:
        return error_response("application not found", 404, "not_found")
    return jsonify({"status": "ok", "usage": usage})


@app.post("/v1/apps/<application_id>/api-keys")
@limiter.limit(ADMIN_RATE_LIMIT)
def create_platform_api_key(application_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    app_result = supabase.table("applications") \
        .select("application_id") \
        .eq("application_id", application_id) \
        .execute()
    if not (app_result.data or []):
        return error_response("application not found", 404, "not_found")

    data = get_json_body()
    key_row, api_key = create_api_key_record(application_id, data.get("name") or "default")
    return jsonify({
        "status": "created",
        "api_key": api_key_with_secret_response(key_row, api_key),
    }), 201


@app.get("/v1/apps/<application_id>/api-keys")
@limiter.limit(ADMIN_RATE_LIMIT)
def list_platform_api_keys(application_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    app_result = supabase.table("applications") \
        .select("application_id") \
        .eq("application_id", application_id) \
        .execute()
    if not (app_result.data or []):
        return error_response("application not found", 404, "not_found")

    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("application_id", application_id) \
        .order("created_at", desc=True) \
        .execute()
    return jsonify({"status": "ok", "api_keys": result.data or []})


@app.post("/v1/api-keys/<api_key_id>/revoke")
@limiter.limit(ADMIN_RATE_LIMIT)
def revoke_platform_api_key(api_key_id):
    admin_error = require_admin_if_configured()
    if admin_error:
        return admin_error

    result = supabase.table("api_keys") \
        .select("api_key_id, application_id, name, key_prefix, revoked_at, last_used_at, created_at") \
        .eq("api_key_id", api_key_id) \
        .execute()
    rows = result.data or []
    if not rows:
        return error_response("API key not found", 404, "not_found")

    key_row = rows[0]
    if key_row.get("revoked_at") is None:
        revoked_at = datetime.now(timezone.utc).isoformat()
        update_result = supabase.table("api_keys") \
            .update({"revoked_at": revoked_at}) \
            .eq("api_key_id", api_key_id) \
            .execute()
        key_row = (update_result.data or [key_row])[0]

    return jsonify({"status": "revoked", "api_key": public_api_key_row(key_row)})


@app.patch("/v1/apps/<application_id>/threshold")
@limiter.limit(PLATFORM_WRITE_RATE_LIMIT)
@require_api_key
def set_application_threshold(application_id):
    if request.cadence_application["application_id"] != application_id:
        return error_response("forbidden", 403, "forbidden")

    data = get_json_body()
    threshold = data.get("threshold")
    if threshold is None:
        return error_response("missing threshold")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        return error_response("threshold must be a number")
    if not (0.0 <= threshold <= 1.0):
        return error_response("threshold must be between 0 and 1")

    supabase.table("applications") \
        .update({"threshold": threshold}) \
        .eq("application_id", application_id) \
        .execute()

    return jsonify({"status": "ok", "application_id": application_id, "threshold": threshold})


# user signup endpoint
# register a new account through Supabase and add a local profile
@app.post("/signup")
def signup():
    data = request.json
    email = data.get("email")
    password = data.get("password")
    username = data.get("username")

    # error handling
    if not email:
        return jsonify({"status": "error", "message": "missing email"}), 400
    if not password:
        return jsonify({"status": "error", "message": "missing password"}), 400
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400
    if len(password) < 16:
        return jsonify({"status": "error", "message": "Password must be at least 16 characters."}), 400
    if not re.search(r'[A-Z]', password):
        return jsonify({"status": "error", "message": "Password must contain at least one uppercase letter."}), 400
    if not re.search(r'[a-z]', password):
        return jsonify({"status": "error", "message": "Password must contain at least one lowercase letter."}), 400
    if not re.search(r'[0-9]', password):
        return jsonify({"status": "error", "message": "Password must contain at least one number."}), 400
    if not re.search(r'[^A-Za-z0-9]', password):
        return jsonify({"status": "error", "message": "Password must contain at least one special character."}), 400
    if username.lower() in password.lower():
        return jsonify({"status": "error", "message": "Password must not contain your username."}), 400

    # check if username already exists
    try:
        existing_user = supabase.table("user_profiles") \
            .select("username") \
            .eq("username", username) \
            .execute()
    except Exception as exc:
        app.logger.exception("signup username lookup failed")
        return jsonify({
            "status": "error",
            "message": f"could not check username availability: {exc}",
        }), 502
    
    if existing_user.data:
        return jsonify({"status": "error", "message": "username already exists"}), 400

    try:
        sign_up_result = _supabase_sign_up(email, password)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    print("SIGN UP RESULT:", sign_up_result)
    sign_up_error = None
    sign_up_user = None
    if isinstance(sign_up_result, dict):
        sign_up_error = sign_up_result.get("error")
        sign_up_user = sign_up_result.get("user")
    else:
        sign_up_error = getattr(sign_up_result, "error", None)
        sign_up_user = getattr(sign_up_result, "user", None)

    if sign_up_error:
        return jsonify({"status": "error", "message": str(sign_up_error)}), 400

    user_id = None
    if isinstance(sign_up_user, dict):
        user_id = sign_up_user.get("id")
    else:
        user_id = getattr(sign_up_user, "id", None)

    if not user_id:
        return jsonify({"status": "error", "message": "signup did not return user id"}), 400

    # create local user_profiles row for biometric login data
    try:
        supabase.table("user_profiles").insert({
            "user_id": user_id,
            "username": username,
            "email": email,
            "current_login_status": None,
            "number_login_attempts": 0,
            "failed_password_attempts": 0,
            "number_of_successful_logins": 0,
        }).execute()
    except Exception as exc:
        app.logger.exception("signup profile insert failed for user_id=%s", user_id)
        try:
            supabase.auth.admin.delete_user(user_id)
        except Exception:
            app.logger.exception("signup rollback failed for user_id=%s", user_id)
        return jsonify({
            "status": "error",
            "message": f"could not create user profile: {exc}",
        }), 502

    return jsonify({"status": "signup_success", "user_id": user_id}), 200


# MAIN ENDPOINT 1: client calls with username/password and keystroke data.
# This endpoint authenticates user credentials through Supabase first,
# then applies biometric scoring and optional 2FA if the score is too low.
@app.post("/authenticate")
@limiter.limit("10 per minute; 50 per hour")
def authenticate():
    print("entering authenticate endpoint", flush=True)
    data = request.json
    username = data.get("username")
    password = data.get("password")
    raw_data = data.get("raw_data")
    is_mobile = bool(data.get("is_mobile"))

    # basic error handling 
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    if not password:
        return jsonify({"status": "error", "message": "missing password"}), 400

    if raw_data is None:
        return jsonify({"status": "error", "message": "missing raw_data"}), 400

    # Reject suspiciously uniform or impossibly fast keystroke sequences.
    # Matches the same message as the paste check so automated tooling gets
    # no signal about which specific heuristic was triggered.
    if _is_scripted_typing(raw_data):
        return jsonify({"status": "error", "message": "please type your password manually"}), 400

    # query user_profiles table to check user exists and get email for Supabase auth
    user = supabase.table("user_profiles") \
        .select("*") \
        .eq("username", username) \
        .execute()

    # if user not found
    if not user.data:
        return jsonify({"status": "user not found"}), 200

    # check current login status
    user_profile = user.data[0]
    user_id = user_profile.get("user_id")
    current_login_status = user_profile.get("current_login_status")
    
    # ensure user_id exists (required for RLS and data integrity)
    if not user_id:
        return jsonify({"status": "error", "message": "user profile incomplete - missing user_id"}), 400
    
    # Block states that should not start a new authentication attempt.
    # A real logout clears "logged in" back to null.
    if current_login_status == "pending 2fa":
        return jsonify({"status": "pending 2fa"}), 200
    elif current_login_status == "locked":
        return jsonify({"status": "account is locked"}), 200
    elif current_login_status == "password_locked":
        # Account is locked due to too many wrong passwords. The unlock code
        # was already sent when the threshold was hit; just tell the client.
        return jsonify({"status": "password_locked"}), 200
    elif current_login_status == "logged in":
        return jsonify({"status": "logged in"}), 200

    # verify username/password against Supabase auth
    email = user_profile.get("email")
    try:
        sign_in_result = _supabase_sign_in(email, password)
    except Exception:
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    sign_in_error = None
    if isinstance(sign_in_result, dict):
        sign_in_error = sign_in_result.get("error")
    else:
        sign_in_error = getattr(sign_in_result, "error", None)

    if sign_in_error:
        # Wrong password: increment the failure counter and, if the threshold
        # is reached, lock the account and email an unlock code to the owner.
        lock_info = _handle_failed_password(
            user_id,
            user_profile.get("failed_password_attempts", 0),
        )
        if lock_info:
            body = {"status": "password_locked", "login_attempt_id": lock_info["login_attempt_id"]}
            if DEMO_MODE and lock_info.get("demo_otp"):
                body["demo_otp"] = lock_info["demo_otp"]
            return jsonify(body), 200
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    # Reject replayed keystroke payloads. We compute the hash after cred
    # verification intentionally — returning this error to an unauthenticated
    # caller would confirm that a given username has prior login attempts.
    events_hash = _hash_events(raw_data)
    if events_hash and _is_replayed_payload(user_id, events_hash):
        return jsonify({"status": "error", "message": "duplicate keystroke payload — please retype your password"}), 400

    # create new login attempt w user info
    enrollment_count = count_successful_login_attempts(user_id)
    login_attempt_id = create_login_attempt(supabase, user_id, raw_data, events_hash=events_hash)
    if login_attempt_id == None:
        return jsonify({"status": "can't verify login"}), 200

    if enrollment_count < REQUIRED_ENROLLMENT_SAMPLES:
        return require_2fa(
            user_id,
            login_attempt_id,
            enrollment_count,
            "enrollment_required",
        )

    # Biometric model is trained on desktop typing — skip scoring on mobile
    # and require 2FA directly so the user isn't falsely rejected.
    if is_mobile:
        return require_2fa(
            user_id,
            login_attempt_id,
            enrollment_count,
            "mobile_device",
        )

    # get the score from ML engine 
    score = get_score(user_id, raw_data, login_attempt_id)
    app.logger.info("score: %s", score)
    print("score =", score, flush=True)
    
    # if no score available, treat as low confidence and trigger 2FA
    if score == None:
        score = 0.0  # treat as failed biometric check
    
    # store ml confidence score in login attempt table
    supabase.table("login_attempts") \
        .update({"confidence_score": score}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()
    
    threshold = DEFAULT_THRESHOLD
    app.logger.info("threshold: %s", threshold)

    # check it
    if (score >= threshold):
        enrollment_count += 1
        supabase.table("login_attempts") \
            .update({"successful_login": True}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()

        # mark as logged in and clear any lingering failure counter
        supabase.table("user_profiles") \
            .update({"current_login_status": "logged in", "failed_password_attempts": 0}) \
            .eq("user_id", user_id) \
            .execute()

        _increment_successful_logins(user_id)

        return jsonify({
            "status": "accepted",
            **enrollment_payload(enrollment_count),
        }), 200
    else:
        return require_2fa(
            user_id,
            login_attempt_id,
            enrollment_count,
            "low_confidence",
        )


@app.post("/logout")
def logout():
    print("entering logout endpoint", flush=True)
    data = request.json or {}
    username = data.get("username")

    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    user = supabase.table("user_profiles") \
        .select("user_id") \
        .eq("username", username) \
        .execute()

    if not user.data:
        return jsonify({"status": "user not found"}), 200

    supabase.table("user_profiles") \
        .update({"current_login_status": None}) \
        .eq("username", username) \
        .execute()

    return jsonify({"status": "logged out"}), 200


# main endpoint 2: after code is sent to user's email, client gets one-time code from user. 
# this method verifies it against the OTP hash that was generated and stored in _2fa challenges table in supabase. 
@app.post("/code_verification")
def code_verification():
    data = request.json
    code = data.get("code")
    login_attempt_id = data.get("login_attempt_id")

    # error handling
    if not code:
        return jsonify({"status": "error", "message": "missing code"}), 400

    if not login_attempt_id:
        return jsonify({"status": "error", "message": "missing login_attempt_id"}), 400

    # Get user_id from login_attempts table using login_attempt_id
    login_attempt_result = supabase.table("login_attempts") \
        .select("user_id") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not login_attempt_result.data:
        return jsonify({"status": "rejected", "message": "invalid attempt"}), 200

    user_id = login_attempt_result.data[0].get("user_id")

    # query _2fa table by login_attempt_id (user_id also available for consistency check)
    result = supabase.table("_2fa") \
        .select("*") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not result.data:
        return jsonify({"status": "rejected", "message": "invalid attempt"}), 200

    entry = result.data[0]
    attempt_count = entry.get("attempt_count", 0)

    # check if max attempts exceeded
    if attempt_count >= 3:
        # mark account as locked
        supabase.table("user_profiles") \
            .update({"current_login_status": "locked"}) \
            .eq("user_id", user_id) \
            .execute()
        return jsonify({"status": "rejected", "message": "max attempts exceeded"}), 200

    # hash the provided code and check against stored hash
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    stored_hash = entry.get("otp_hash")

    if code_hash != stored_hash:
        # increment attempt count only on wrong code
        supabase.table("_2fa") \
            .update({"attempt_count": attempt_count + 1}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()
        return jsonify({"status": "rejected"}), 200  
    
    # check for expiration
    expires_at = entry.get("expires_at")
    
    if expires_at:
        expires_at = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            return jsonify({"status": "rejected", "message": "expired"}), 200

    # delete the login attempt from 2fa table so code isn't reusable
    supabase.table("_2fa").delete().eq("login_attempt_id", login_attempt_id).execute()

    # Check if this verification is unlocking a brute-forced account rather
    # than completing a normal 2FA login. In the unlock flow the original
    # password was never correct, so we must not log the user in or count
    # this as an enrollment sample — we just clear the lock and let them
    # try again with their real password.
    profile_result = supabase.table("user_profiles") \
        .select("current_login_status") \
        .eq("user_id", user_id) \
        .single() \
        .execute()
    is_unlock = (profile_result.data or {}).get("current_login_status") == "password_locked"

    if is_unlock:
        supabase.table("user_profiles") \
            .update({"current_login_status": None, "failed_password_attempts": 0}) \
            .eq("user_id", user_id) \
            .execute()
        return jsonify({"status": "unlocked"}), 200

    # Normal 2FA success: mark the login attempt as successful and log in.
    supabase.table("login_attempts") \
        .update({"successful_login": True}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    enrollment_count = count_successful_login_attempts(user_id)
    user_status = "logged in" if enrollment_count >= REQUIRED_ENROLLMENT_SAMPLES else None

    # Enrollment attempts stay login-capable until enough samples are collected.
    # Also clear any stale failure counter on successful authentication.
    supabase.table("user_profiles") \
        .update({"current_login_status": user_status, "failed_password_attempts": 0}) \
        .eq("user_id", user_id) \
        .execute()

    _increment_successful_logins(user_id)

    return jsonify({
        "status": "accepted",
        **enrollment_payload(enrollment_count),
    }), 200

# resend 2fa code endpoint
@app.post("/resend_code")
def resend_code():
    data = request.json
    login_attempt_id = data.get("login_attempt_id")

    # error handling
    if not login_attempt_id:
        return jsonify({"status": "error", "message": "missing login_attempt_id"}), 400

    # Get user_id from login_attempts table
    login_attempt_result = supabase.table("login_attempts") \
        .select("user_id") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not login_attempt_result.data:
        return jsonify({"status": "invalid attempt"}), 200

    user_id = login_attempt_result.data[0].get("user_id")

    # find the pending 2fa attempt for this login
    result = supabase.table("_2fa") \
        .select("*") \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    if not result.data:
        return jsonify({"status": "invalid attempt"}), 200

    # delete any expired 2fa rows for this user before issuing a new code
    supabase.table("_2fa") \
        .delete() \
        .eq("user_id", user_id) \
        .lt("expires_at", datetime.now(timezone.utc).isoformat()) \
        .execute()

    # generate new OTP
    otp = str(random.randint(100000, 999999))
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    # update the 2fa record with new code
    supabase.table("_2fa") \
        .update({
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat(),
            "attempt_count": 0
        }) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()

    # get email from user_profiles table
    email_result = supabase.table("user_profiles") \
        .select("email") \
        .eq("user_id", user_id) \
        .execute()
    email = email_result.data[0]["email"]

    if DEMO_MODE:
        app.logger.warning("[DEMO_MODE] resent OTP for user_id=%s (%s): %s", user_id, email, otp)
        return jsonify({"status": "code sent", "demo_otp": otp}), 200

    resend.api_key = os.getenv("RESEND_KEY")
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": email,
        "subject": "Verification Code",
        "html": f"<p>Your one-time code is: {otp}</p>"
    })

    return jsonify({"status": "code sent"}), 200

# ---------------------------------------------------------------------------
# Brute-force protection helpers
# ---------------------------------------------------------------------------

def _handle_failed_password(user_id, current_failures):
    # Increment the consecutive wrong-password counter.
    # If the threshold is reached, send an unlock code and set the account to
    # "password_locked" so no further login attempts can proceed until the user
    # verifies their identity via email. Returns a dict with the unlock
    # login_attempt_id and (in demo mode) the OTP if the threshold was just
    # hit, otherwise returns None.
    new_count = current_failures + 1

    if new_count < FAILED_PASSWORD_THRESHOLD:
        supabase.table("user_profiles") \
            .update({"failed_password_attempts": new_count}) \
            .eq("user_id", user_id) \
            .execute()
        return None

    # Threshold reached. Create a minimal login_attempt to anchor the OTP
    # record (send_code requires a login_attempt_id with a matching row),
    # then send the unlock code. If sending fails we leave the account
    # unlocked rather than stranding the user with no way to recover.
    unlock_attempt_id = create_login_attempt(supabase, user_id, {})
    try:
        otp = send_code(user_id, unlock_attempt_id)
    except Exception:
        app.logger.exception("send_code failed during password-lock for user_id=%s", user_id)
        return None

    supabase.table("user_profiles") \
        .update({
            "failed_password_attempts": new_count,
            "current_login_status": "password_locked",
        }) \
        .eq("user_id", user_id) \
        .execute()

    return {
        "login_attempt_id": unlock_attempt_id,
        "demo_otp": otp if DEMO_MODE else None,
    }


# ---------------------------------------------------------------------------
# Scripted-typing detection
# ---------------------------------------------------------------------------

# Thresholds derived from human typing research and the specific 10ms-apart
# script the team observed. Configurable via env so they can be tuned without
# a redeploy if legitimate users with unusual typing styles are affected.
_MIN_MEAN_INTERVAL_MS  = float(os.getenv("CADENCE_MIN_MEAN_INTERVAL_MS",  "30"))
_MIN_STDDEV_INTERVAL_MS = float(os.getenv("CADENCE_MIN_STDDEV_INTERVAL_MS", "8"))

def _is_scripted_typing(raw_data):
    # Extract timestamps of keydown events only (down-to-down intervals are
    # the standard measure of typing speed and rhythm).
    events = (raw_data.get("events") if isinstance(raw_data, dict) else raw_data) or []  # FIX: handle list vs dict
    down_times = [e["t"] for e in events if e.get("type") == "down"]

    # Need at least 3 intervals to compute a meaningful standard deviation.
    if len(down_times) < 4:
        return False

    intervals = [down_times[i] - down_times[i - 1] for i in range(1, len(down_times))]

    mean_ms   = statistics.mean(intervals)
    stddev_ms = statistics.pstdev(intervals)  # population stdev — no sampling assumption

    # A real human cannot sustain sub-30ms intervals, and no human produces
    # near-zero variance across all keystrokes. Either condition alone is
    # sufficient to flag the session as automated.
    return mean_ms < _MIN_MEAN_INTERVAL_MS or stddev_ms < _MIN_STDDEV_INTERVAL_MS


# ---------------------------------------------------------------------------
# Replay-detection helpers
# ---------------------------------------------------------------------------

def _hash_events(raw_data):
    # Canonicalize the events array (sort_keys so key ordering can't create
    # two different hashes for the same payload) and return its SHA-256 digest.
    # Returns None when there are no events so callers can skip the DB check.
    events = (raw_data.get("events") if isinstance(raw_data, dict) else raw_data) or []  # FIX: handle list vs dict
    if not events:
        return None
    canonical = json.dumps(events, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _is_replayed_payload(user_id, events_hash):
    # An exact payload match within 24 hours is treated as a replay.
    # 24 h is wide enough to cover an attacker who captures all 5 enrollment
    # logins in one session and immediately tries to reuse them, while being
    # narrow enough not to bother legitimate users who might return the next day
    # (and whose live capture will differ by at least a few milliseconds anyway).
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    result = (
        supabase.table("login_attempts")
        .select("login_attempt_id")
        .eq("user_id", user_id)
        .eq("events_hash", events_hash)
        .gte("created_at", cutoff)
        .limit(1)
        .execute()
    )
    return bool(result.data)


# create new login attempt in DB, return login attempt id
def create_login_attempt(supabase, user_id, raw_data, events_hash=None):
    login_attempt_id = str(uuid.uuid4())
     # 1. fetch profile
    profile = (
        supabase
        .table("user_profiles")
        .select("number_login_attempts")
        .eq("user_id", user_id)
        .single()
        .execute()
    )

    current_count = profile.data["number_login_attempts"] or 0
    login_number = current_count + 1

    # 2. create login attempt row
    new_attempt = {
        "login_attempt_id": login_attempt_id,
        "user_id": user_id,
        "login_number": login_number,
        "two_fa_invoked": False,
        "successful_login": None,
        "confidence_score": None,
        "raw_data": raw_data or {},
        "events_hash": events_hash,
    }

    # 3. insert into login_attempts
    supabase.table("login_attempts").insert(new_attempt).execute()

    # 4. update user profile counter
    supabase.table("user_profiles") \
        .update({"number_login_attempts": login_number}) \
        .eq("user_id", user_id) \
        .execute()

    return login_attempt_id

# call ML engine and return the score given
def get_score(user_id, raw_data, login_attempt_id=None):
    return model_service.score_login_attempt(
        supabase,
        user_id,
        raw_data,
        login_attempt_id=login_attempt_id,
    )

# generate otp hash, send code to user's email. Returns the plaintext
# OTP so the caller can surface it in demo mode.
def send_code(user_id, login_attempt_id):
    email_result = supabase.table("user_profiles") \
        .select("email") \
        .eq("user_id", user_id) \
        .execute()
    email = email_result.data[0]["email"]

    otp = str(random.randint(100000, 999999))
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    # delete any expired 2fa rows for this user before inserting a fresh one
    supabase.table("_2fa") \
        .delete() \
        .eq("user_id", user_id) \
        .lt("expires_at", datetime.now(timezone.utc).isoformat()) \
        .execute()

    # insert the 2fa attempt
    supabase.table("_2fa") \
        .insert({
            "login_attempt_id": login_attempt_id,
            "user_id": user_id,
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat(),
            "attempt_count": 0
        }) \
        .execute()

    if DEMO_MODE:
        app.logger.warning("[DEMO_MODE] OTP for user_id=%s (%s): %s", user_id, email, otp)
        return otp

    resend.api_key = os.getenv("RESEND_KEY")
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": email,
        "subject": "Verification Code",
        "html": f"<p>Your one-time code is: {otp}</p>"
    })
    return otp

    

if __name__ == "__main__":
    app.run()
