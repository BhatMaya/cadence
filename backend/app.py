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

# start supabase client
# backend service key is used here for auth and data operations
supabase = create_client(
    os.getenv("SUPABASE_URL").strip(),
    os.getenv("SUPABASE_KEY").strip()
)


def _supabase_sign_up(email, password):
    auth = supabase.auth
    # helper handles Supabase client method differences across versions
    try:
        return auth.sign_up({"email": email, "password": password})
    except TypeError:
        return auth.sign_up(email=email, password=password)


def _supabase_sign_in(email, password):
    auth = supabase.auth
    # helper handles Supabase sign in API differences
    try:
        return auth.sign_in_with_password({"email": email, "password": password})
    except TypeError:
        return auth.sign_in(email=email, password=password)


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

    sign_up_result = _supabase_sign_up(email, password)
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
    sign_in_result = _supabase_sign_in(email, password)
    sign_in_error = None
    if isinstance(sign_in_result, dict):
        sign_in_error = sign_in_result.get("error")
    else:
        sign_in_error = getattr(sign_in_result, "error", None)

    if sign_in_error:
        return jsonify({"status": "error", "message": "invalid credentials"}), 401

    # create new login attempt w user info 
    login_attempt_id = create_login_attempt(supabase, user_id, username, raw_data)
    if login_attempt_id == None:
        return jsonify({"status": "can't verify login"}), 200

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
        # mark as logged in
        supabase.table("user_profiles") \
            .update({"current_login_status": "logged in"}) \
            .eq("user_id", user_id) \
            .execute()
            
        return jsonify({"status": "accepted"}), 200
    else:
        # mark as pending 2fa
        supabase.table("user_profiles") \
            .update({"current_login_status": "pending 2fa"}) \
            .eq("user_id", user_id) \
            .execute()
        
        # MARK 2FA AS INVOKED FOR THIS LOGIN ATTEMPT  # ADDED
        supabase.table("login_attempts") \
            .update({"two_fa_invoked": True}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()

        # send 2fa email 
        send_code(user_id, username, login_attempt_id)

        return jsonify({"status": "2fa required", "login_attempt_id": login_attempt_id}), 200

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
        .single() \
        .execute()

    if not login_attempt_result.data:
        return jsonify({"status": "rejected", "message": "invalid attempt"}), 200

    user_id = login_attempt_result.data.get("user_id")

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
    
    # mark user as logged in
    supabase.table("user_profiles") \
        .update({"current_login_status": "logged in"}) \
        .eq("user_id", user_id) \
        .execute()
    
    return jsonify({"status": "accepted"}), 200

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
        .single() \
        .execute()

    if not login_attempt_result.data:
        return jsonify({"status": "invalid attempt"}), 200

    user_id = login_attempt_result.data.get("user_id")

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

    resend.api_key = os.getenv("RESEND_KEY")

    # send email
    resend.Emails.send({
        "from": "onboarding@resend.dev",
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

# generate otp hash, send code to user's email. 
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

    resend.api_key = os.getenv("RESEND_KEY")

    resend.Emails.send({
        "from": "onboarding@resend.dev",  # default test sender
        "to": email,
        "subject": "Verification Code",
        "html": f"<p>Your one-time code is: {otp}</p>"
    })

    

if __name__ == "__main__":
    app.run(debug=True)
