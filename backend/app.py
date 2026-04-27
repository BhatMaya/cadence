from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os
import random
import hashlib
import resend
from datetime import datetime, timedelta, timezone

load_dotenv()

from supabase import create_client

app = Flask(__name__)

# test env variables for api access 
print("URL:", os.getenv("SUPABASE_URL"))
print("KEY:", os.getenv("SUPABASE_KEY"))

supabase = create_client(
    os.getenv("SUPABASE_URL").strip(),
    os.getenv("SUPABASE_KEY").strip()
)

@app.get("/health")
def health():
    return {"status": "ok"}

# main endpoint 1: client calls with username and raw data, gets accepted or sent to 2fa.  
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

    # user not found
    if not user.data:
        return jsonify({"status": "user not found"}), 200

    # create new login attempt w user info 
    if create_login_attempt(username, raw_data) == False:
        return jsonify({"status": "can't verify login"}), 200

    # get the score from ML engine 
    score = get_score(username, raw_data)
    if score == None:
        return jsonify({"status":"no score available"}), 200
    
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
        # send 2fa email 
        send_code(username)

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
    
    # check for non stale code 
    entry = result.data[0]
    expires_at = entry.get("expires_at")
    
    if expires_at:
        expires_at = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if now > expires_at:
            return jsonify({"status": "rejected", "message": "expired"}), 200

    return jsonify({"status": "accepted"}), 200

# create new login attempt in DB, return successful/unsuccessful 
def create_login_attempt(username, raw_data):
    # filler 
    return True

# call ML engine and return the score given
def get_score(username, raw_data):
    # filler 
    return 0.69

# generate otp hash, send code to user's email. 
def send_code(username):
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