from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import random
import hashlib
import resend
from datetime import datetime, timedelta, timezone
import uuid

load_dotenv()

from supabase import create_client

try:
    from .model_service import CadenceModelService
except ImportError:
    from model_service import CadenceModelService

app = Flask(__name__)
model_service = CadenceModelService()

# Demo mode: skips the Resend email and returns the freshly-generated
# OTP in the API response so testers without access to the inbox can
# still complete 2FA. Never enable in production.
DEMO_MODE = os.getenv("CADENCE_DEMO_MODE", "0").lower() in {"1", "true", "yes"}
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "team@cadence-capstone.us")

# CORS for the local dev frontend. Override with CADENCE_CORS_ORIGINS
# (comma-separated) when deploying behind a different origin.
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
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
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/<path:_any>", methods=["OPTIONS"])
@app.route("/", methods=["OPTIONS"])
def cors_preflight(_any=None):
    return ("", 204)
REQUIRED_ENROLLMENT_SAMPLES = int(
    os.getenv("CADENCE_REQUIRED_ENROLLMENT_SAMPLES", "5")
)

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


def count_successful_login_attempts(user_id):
    result = supabase.table("login_attempts") \
        .select("login_attempt_id") \
        .eq("user_id", user_id) \
        .eq("successful_login", True) \
        .execute()
    return len(result.data or [])


def enrollment_payload(enrollment_count):
    samples_needed = max(REQUIRED_ENROLLMENT_SAMPLES - enrollment_count, 0)
    return {
        "enrolled": samples_needed == 0,
        "enrollment_count": enrollment_count,
        "enrollment_required": REQUIRED_ENROLLMENT_SAMPLES,
        "enrollment_samples_needed": samples_needed,
    }


def require_2fa(user_id, username, login_attempt_id, enrollment_count, reason):
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
        otp = send_code(user_id, username, login_attempt_id)
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
    
    # check if username already exists
    existing_user = supabase.table("user_profiles") \
        .select("username") \
        .eq("username", username) \
        .execute()
    
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

    print({
        "user_id": user_id,
        "username": username,
        "email": email,
        "threshold": 0.5,
        "current_login_status": None,
        "number_login_attempts": 0
    })
    # create local user_profiles row for biometric login data
    supabase.table("user_profiles").insert({
        "user_id": user_id,
        "username": username,
        "email": email,
        "threshold": 0.5,
        "current_login_status": None,
        "number_login_attempts": 0
    }).execute()

    return jsonify({"status": "signup_success", "user_id": user_id}), 200


# MAIN ENDPOINT 1: client calls with username/password and keystroke data.
# This endpoint authenticates user credentials through Supabase first,
# then applies biometric scoring and optional 2FA if the score is too low.
@app.post("/authenticate")
def authenticate():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    raw_data = data.get("raw_data")

    # basic error handling 
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    if not password:
        return jsonify({"status": "error", "message": "missing password"}), 400

    if raw_data is None:
        return jsonify({"status": "error", "message": "missing raw_data"}), 400

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
    
    # ensure this is a user who is not logged in and hasn't tried.
    if current_login_status == "pending 2fa":
        return jsonify({"status": "pending 2fa"}), 200
    elif current_login_status == "locked":
        return jsonify({"status": "account is locked"}), 200
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
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    # create new login attempt w user info 
    enrollment_count = count_successful_login_attempts(user_id)
    login_attempt_id = create_login_attempt(supabase, user_id, username, raw_data)
    if login_attempt_id == None:
        return jsonify({"status": "can't verify login"}), 200

    if enrollment_count < REQUIRED_ENROLLMENT_SAMPLES:
        return require_2fa(
            user_id,
            username,
            login_attempt_id,
            enrollment_count,
            "enrollment_required",
        )

    # get the score from ML engine 
    score = get_score(username, raw_data, login_attempt_id)
    print(score)
    
    # if no score available, treat as low confidence and trigger 2FA
    if score == None:
        score = 0.0  # treat as failed biometric check
    
    # store ml confidence score in login attempt table
    supabase.table("login_attempts") \
        .update({"confidence_score": score}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()
    
    # get user's threshold 
    threshold_result = supabase.table("user_profiles") \
                .select("threshold") \
                .eq("user_id", user_id) \
                .execute()
    threshold = threshold_result.data[0]["threshold"]

    # check it
    if (score >= threshold):
        enrollment_count += 1
        supabase.table("login_attempts") \
            .update({"successful_login": True}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()

        # mark as logged in
        supabase.table("user_profiles") \
            .update({"current_login_status": "logged in"}) \
            .eq("user_id", user_id) \
            .execute()
            
        return jsonify({
            "status": "accepted",
            **enrollment_payload(enrollment_count),
        }), 200
    else:
        return require_2fa(
            user_id,
            username,
            login_attempt_id,
            enrollment_count,
            "low_confidence",
        )

# main endpoint 2: after code is sent to user's email, client gets one-time code from user. 
# this method verifies it against the OTP hash that was generated and stored in _2fa challenges table in supabase. 
@app.post("/code_verification")
def code_verification():
    data = request.json
    username = data.get("username")
    code = data.get("code")
    login_attempt_id = data.get("login_attempt_id")

    # error handling 
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

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

    # mark the login attempt as successful 
    supabase.table("login_attempts") \
    .update({"successful_login": True}) \
    .eq("login_attempt_id", login_attempt_id) \
    .execute()

    enrollment_count = count_successful_login_attempts(user_id)
    user_status = "logged in" if enrollment_count >= REQUIRED_ENROLLMENT_SAMPLES else None
    
    # Enrollment attempts stay login-capable until enough samples are collected.
    supabase.table("user_profiles") \
        .update({"current_login_status": user_status}) \
        .eq("user_id", user_id) \
        .execute()
    
    return jsonify({
        "status": "accepted",
        **enrollment_payload(enrollment_count),
    }), 200

# resend 2fa code endpoint
@app.post("/resend_code")
def resend_code():
    data = request.json
    username = data.get("username")
    login_attempt_id = data.get("login_attempt_id")

    # error handling
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

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
        app.logger.warning("[DEMO_MODE] resent OTP for %s (%s): %s", username, email, otp)
        return jsonify({"status": "code sent", "demo_otp": otp}), 200

    resend.api_key = os.getenv("RESEND_KEY")
    resend.Emails.send({
        "from": RESEND_FROM_EMAIL,
        "to": email,
        "subject": "Verification Code",
        "html": f"<p>Your one-time code is: {otp}</p>"
    })

    return jsonify({"status": "code sent"}), 200

# create new login attempt in DB, return login attempt id 
def create_login_attempt(supabase, user_id, username, raw_data):
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
        "username": username,
        "login_number": login_number,
        "two_fa_invoked": False,
        "successful_login": None,
        "confidence_score": None,
        "raw_data": raw_data or {}
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
def get_score(username, raw_data, login_attempt_id=None):
    return model_service.score_login_attempt(
        supabase,
        username,
        raw_data,
        login_attempt_id=login_attempt_id,
    )

# generate otp hash, send code to user's email. Returns the plaintext
# OTP so the caller can surface it in demo mode.
def send_code(user_id, username, login_attempt_id):
    email_result = supabase.table("user_profiles") \
        .select("email") \
        .eq("user_id", user_id) \
        .execute()
    email = email_result.data[0]["email"]

    otp = str(random.randint(100000, 999999))
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    # insert the 2fa attempt
    supabase.table("_2fa") \
        .insert({
            "login_attempt_id": login_attempt_id,
            "user_id": user_id,
            "username": username,
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat(),
            "attempt_count": 0
        }) \
        .execute()

    if DEMO_MODE:
        app.logger.warning("[DEMO_MODE] OTP for %s (%s): %s", username, email, otp)
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
