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
supabase = create_client(
    os.getenv("SUPABASE_URL").strip(),
    os.getenv("SUPABASE_KEY").strip()
)


# health check endpoint 
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/model/health")
def model_health():
    return jsonify(model_service.health())


# MAIN ENDPOINT 1: client calls with username and raw data, gets accepted or sent to 2fa.  
@app.post("/authenticate")
def authenticate():
    data = request.json
    username = data.get("username")
    raw_data = data.get("raw_data")

    # basic error handling 
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    if raw_data is None:
        return jsonify({"status": "error", "message": "missing raw_data"}), 400

    # query user_profiles table to check user exists. filler test. 
    user = supabase.table("user_profiles") \
        .select("*") \
        .eq("username", username) \
        .execute()

    # if user not found
    if not user.data:
        return jsonify({"status": "user not found"}), 200

    # create new login attempt w user info 
    login_attempt_id = create_login_attempt(supabase, username, raw_data)
    if login_attempt_id == None:
        return jsonify({"status": "can't verify login"}), 200

    # get the score from ML engine 
    score = get_score(username, raw_data, login_attempt_id)
    if score == None:
        return jsonify({"status":"no score available"}), 200
    
    # STORE ML SCORE IN LOGIN ATTEMPT RECORD  # ADDED
    supabase.table("login_attempts") \
        .update({"confidence_score": score}) \
        .eq("login_attempt_id", login_attempt_id) \
        .execute()
    
    # get user's threshold 
    threshold_result = supabase.table("user_profiles") \
                .select("threshold") \
                .eq("username", username) \
                .execute()
    threshold = threshold_result.data[0]["threshold"]

    # check it
    if (score >= threshold):
        return jsonify({"status": "accepted"}), 200
    else:
        # MARK 2FA AS INVOKED FOR THIS LOGIN ATTEMPT  # ADDED
        supabase.table("login_attempts") \
            .update({"two_fa_invoked": True}) \
            .eq("login_attempt_id", login_attempt_id) \
            .execute()

        # send 2fa email 
        send_code(username, login_attempt_id)

        return jsonify({"status": "2fa required"}), 200

# main endpoint 2: after code is sent to user's email, client gets one-time code from user. 
# this method verifies it against the OTP hash that was generated and stored in _2fa challenges table in supabase. 
@app.post("/code_verification")
def code_verification():
    data = request.json
    username = data.get("username")
    code = data.get("code")

    # error handling 
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    if not code:
        return jsonify({"status": "error", "message": "missing code"}), 400

    code_hash = hashlib.sha256(code.encode()).hexdigest()  

    # check if username has that code for this user 
    result = supabase.table("_2fa") \
        .select("*") \
        .eq("username", username) \
        .eq("otp_hash", code_hash) \
        .execute()

    # wrong code 
    if not result.data:
        return jsonify({"status": "rejected"}), 200  
    
    # check for expiration
    entry = result.data[0]
    expires_at = entry.get("expires_at")
    
    if expires_at:
        expires_at = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            return jsonify({"status": "rejected", "message": "expired"}), 200

    # get login attempt id 
    login_attempt_id = entry["login_attempt_id"]
    
    # delete the login attempt from 2fa table so code isn't reusable 
    supabase.table("_2fa").delete().eq("login_attempt_id", login_attempt_id).execute()

    # mark the login attempt as successful 
    supabase.table("login_attempts") \
    .update({"successful_login": True}) \
    .eq("login_attempt_id", login_attempt_id) \
    .execute()
    return jsonify({"status": "accepted"}), 200

# create new login attempt in DB, return login attempt id 
def create_login_attempt(supabase, username, raw_data):
    login_attempt_id = str(uuid.uuid4())
     # 1. fetch profile
    profile = (
        supabase
        .table("user_profiles")
        .select("number_login_attempts")
        .eq("username", username)
        .single()
        .execute()
    )

    current_count = profile.data["number_login_attempts"] or 0
    login_number = current_count + 1

    # 2. create login attempt row
    new_attempt = {
        "login_attempt_id": login_attempt_id, 
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
        .eq("username", username) \
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
def send_code(username, login_attempt_id):
    email_result = supabase.table("user_profiles") \
        .select("email") \
        .eq("username", username) \
        .execute()
    email = email_result.data[0]["email"]

    otp = str(random.randint(100000, 999999))
    otp_hash = hashlib.sha256(otp.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    # insert the 2fa attempt 
    supabase.table("_2fa") \
        .insert({
            "login_attempt_id": login_attempt_id,
            "username": username,
            "otp_hash": otp_hash,
            "expires_at": expires_at.isoformat()
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
