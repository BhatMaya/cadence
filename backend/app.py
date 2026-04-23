from flask import Flask, request, jsonify
from dotenv import load_dotenv
import os

load_dotenv()

from supabase import create_client

app = Flask(__name__)

# env variables for api access 
print("URL:", os.getenv("SUPABASE_URL"))
print("KEY:", os.getenv("SUPABASE_KEY"))

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
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

    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    if raw_data is None:
        return jsonify({"status": "error", "message": "missing raw_data"}), 400

    user = supabase.table("user_profiles") \
        .select("*") \
        .eq("username", username) \
        .execute()

    if not user.data:
        return jsonify({"status": "user not found"}), 200

    return jsonify({
        "status": "2fa required"
    }), 200

# main endpoint 2: after code is sent to user's email, client gets one-time code from user. 
# this method verifies it against the OTP hash that was generated and stored in _2fa challenges table in supabase. 
@app.post("/code_verification")
def code_verification():
    data = request.json
    username = data.get("username")
    code = data.get("code")

    # basic error handling 
    if not username:
        return jsonify({"status": "error", "message": "missing username"}), 400

    if not code:
        return jsonify({"status": "error", "message": "missing code"}), 400

    # query _2fa table in supabase to get user's correct otp_hash. 
    result = supabase.table("_2fa") \
    .select("*") \
    .eq("username", username) \
    .eq("otp_hash", code) \
    .execute()

    # if not matching, reject user. (will implement retries later)
    if not result.data:
        return jsonify({"status": "rejected"}), 200

    # code matches, accept user
    return jsonify({"status": "accepted"}), 200

if __name__ == "__main__":
    app.run(debug=True)